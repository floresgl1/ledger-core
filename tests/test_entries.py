"""Unit tests for the five journal entry generators — the library's core
correctness promise. Every entry must balance (total debits == total credits)
and put the right amounts on the right sides; one unbalanced entry shipped to
a bookkeeper ends the customer relationship.

Ported from stripe-reconciler `backend/test_journal_entries.py`, whose 34
assertions all appear below with their original values and intent. Changes:
the hand-rolled check() harness becomes pytest, entries are read through
`.lines` now that generators return a JournalEntry, and manual_payout_entry —
untestable upstream because it took a live Stripe object — is covered here.

Pure functions. No Flask, no DB, no Stripe.
"""
import pytest

from ledger_core import (
    ACCOUNT_CASH_IN_TRANSIT,
    ACCOUNT_PROCESSING_FEES,
    ACCOUNT_SALES_REVENUE,
    ACCOUNT_STRIPE_BANK,
    ACCOUNT_SUSPENSE,
    Payout,
    Transaction,
    charge_entry,
    manual_payout_entry,
    payout_entry,
    refund_entry,
    suspense_entry,
)


def txn(amount, fee, net, type="charge", currency="usd"):
    return Transaction(id="txn_t", amount=amount, fee=fee, net=net,
                       type=type, created=1751000000, currency=currency)


def lines(entry):
    """{account: (debit, credit)} for easy assertions."""
    return {l["account"]: (l["debit"], l["credit"]) for l in entry.lines}


def balances(entry):
    return (sum(l["debit"] for l in entry.lines)
            == sum(l["credit"] for l in entry.lines))


def account_delta(entry, account):
    return sum(l["debit"] - l["credit"] for l in entry.lines
               if l["account"] == account)


# --- charges -----------------------------------------------------------------

def test_charge_books_net_fee_and_gross():
    entry = charge_entry(txn(amount=1000, fee=59, net=941))
    by = lines(entry)
    assert balances(entry), "entry balances"
    assert by[ACCOUNT_STRIPE_BANK] == (941, 0), "bank debited the net"
    assert by[ACCOUNT_PROCESSING_FEES] == (59, 0), "fee expense debited"
    assert by[ACCOUNT_SALES_REVENUE] == (0, 1000), "revenue credited the gross"


def test_zero_fee_charge_still_balances():
    assert balances(charge_entry(txn(amount=250, fee=0, net=250)))


def test_unbalanced_charge_raises():
    # the balance guard must trip on data that violates net = amount - fee
    with pytest.raises(ValueError):
        charge_entry(txn(amount=1000, fee=59, net=900))


# --- refunds -----------------------------------------------------------------

def test_refund_with_fee_returned():
    # Stripe returned the original fee: net gives back more than the amount
    entry = refund_entry(txn(amount=-1000, fee=0, net=-941, type="refund"))
    by = lines(entry)
    assert balances(entry), "fee-returned refund balances"
    assert by[ACCOUNT_STRIPE_BANK] == (0, 941), "bank credited the cash out"
    assert by[ACCOUNT_PROCESSING_FEES] == (0, 59), "returned fee credits the expense"
    assert by[ACCOUNT_SALES_REVENUE] == (1000, 0), "revenue debited the gross"


def test_refund_with_fee_charged():
    # Stripe charged a fee ON the refund: cash out exceeds the amount
    entry = refund_entry(txn(amount=-1000, fee=0, net=-1059, type="refund"))
    by = lines(entry)
    assert balances(entry), "fee-charged refund balances"
    assert by[ACCOUNT_STRIPE_BANK] == (0, 1059), "bank credited amount plus fee"
    assert by[ACCOUNT_PROCESSING_FEES] == (59, 0), "refund fee debits the expense"


def test_refund_with_no_fee_either_way():
    entry = refund_entry(txn(amount=-500, fee=0, net=-500, type="refund"))
    by = lines(entry)
    assert balances(entry), "zero-fee refund balances"
    assert by[ACCOUNT_PROCESSING_FEES] == (0, 0), "zero-fee refund has an empty fee line"


def test_pathological_refund_with_positive_net_is_rejected():
    # A refund whose net flips positive (returned fee exceeding the refund
    # amount) can't be represented by the entry's fixed bank-credit side.
    # The guard must refuse to ship it — the caller then routes the txn to
    # the review list instead of emailing a broken journal.
    with pytest.raises(ValueError):
        refund_entry(txn(amount=-1, fee=0, net=29, type="refund"))


# --- suspense ----------------------------------------------------------------

def test_suspense_when_money_left_the_balance():
    # e.g. a stripe_fee: suspense debit, bank credit
    entry = suspense_entry(txn(amount=-200, fee=0, net=-200, type="stripe_fee"))
    by = lines(entry)
    assert balances(entry), "negative-net suspense balances"
    assert by[ACCOUNT_SUSPENSE] == (200, 0) and by[ACCOUNT_STRIPE_BANK] == (0, 200), \
        "suspense debited, bank credited"


def test_suspense_when_money_arrived():
    # e.g. an adjustment in the user's favor: mirror image
    entry = suspense_entry(txn(amount=300, fee=0, net=300, type="adjustment"))
    by = lines(entry)
    assert balances(entry), "positive-net suspense balances"
    assert by[ACCOUNT_STRIPE_BANK] == (300, 0) and by[ACCOUNT_SUSPENSE] == (0, 300), \
        "bank debited, suspense credited"


# --- payouts -----------------------------------------------------------------

def test_standard_payout_is_a_clean_two_line_move():
    # no fee, net == amount
    entry = payout_entry(txn(amount=-9380, fee=0, net=-9380, type="payout"))
    by = lines(entry)
    assert balances(entry), "standard payout balances"
    assert by[ACCOUNT_CASH_IN_TRANSIT] == (9380, 0), "cash in transit debited what lands"
    assert by[ACCOUNT_STRIPE_BANK] == (0, 9380), "stripe balance credited the outflow"
    assert ACCOUNT_PROCESSING_FEES not in by, "standard payout has no fee line"


def test_instant_payout_expenses_its_fee():
    # a fee rides along, booked as expense so the balance still washes to zero
    # (what lands + fee == what leaves the balance)
    entry = payout_entry(txn(amount=-10000, fee=50, net=-10050, type="payout"))
    by = lines(entry)
    assert balances(entry), "instant payout balances"
    assert by[ACCOUNT_CASH_IN_TRANSIT] == (10000, 0), "cash in transit gets only what lands"
    assert by[ACCOUNT_PROCESSING_FEES] == (50, 0), "payout fee expensed"
    assert by[ACCOUNT_STRIPE_BANK] == (0, 10050), "stripe balance credited the full outflow"


# --- the manual payout leg ---------------------------------------------------
# New coverage. Upstream this function read attributes off a live stripe.Payout
# and so could not be called from a unit test at all; the Payout dataclass is
# what makes these three assertions possible.

def payout(amount=9380, balance_transaction="txn_po", currency="usd"):
    return Payout(id="po_1", amount=amount, created=1751000000,
                  balance_transaction=balance_transaction, currency=currency)


def test_manual_payout_books_only_the_provable_leg():
    entry = manual_payout_entry(payout())
    by = lines(entry)
    assert balances(entry)
    assert by[ACCOUNT_CASH_IN_TRANSIT] == (9380, 0)
    assert by[ACCOUNT_STRIPE_BANK] == (0, 9380)
    assert ACCOUNT_SALES_REVENUE not in by, "no revenue a manual payout can source"


def test_manual_payout_prefers_the_balance_transaction_as_source_id():
    assert manual_payout_entry(payout()).source_id == "txn_po"


def test_manual_payout_falls_back_to_the_payout_id():
    # Stripe hasn't posted the balance transaction yet
    assert manual_payout_entry(payout(balance_transaction=None)).source_id == "po_1"


# --- the two legs of one story: charge in, payout out, clearing washes to 0 --
# The whole reason the payout leg belongs in the journal: the Stripe balance
# is a clearing account the charge fills and the payout drains, so a complete
# payout nets it to exactly zero — no cash double-counted, none stranded.

def test_stripe_balance_clearing_account_nets_to_zero():
    charge = charge_entry(txn(amount=10000, fee=620, net=9380))
    out = payout_entry(txn(amount=-9380, fee=0, net=-9380, type="payout"))

    assert (account_delta(charge, ACCOUNT_STRIPE_BANK)
            + account_delta(out, ACCOUNT_STRIPE_BANK)) == 0, \
        "Stripe balance nets to zero across charge + its payout"
    assert (account_delta(charge, ACCOUNT_CASH_IN_TRANSIT)
            + account_delta(out, ACCOUNT_CASH_IN_TRANSIT)) == 9380, \
        "the net cash lands in Cash in Transit, exactly once"
    assert (account_delta(charge, ACCOUNT_SALES_REVENUE)
            + account_delta(out, ACCOUNT_SALES_REVENUE)) == -10000, \
        "revenue recognized once, at the gross"


# --- the invariant, swept across realistic value shapes ----------------------
# fees never exceed the amount (Stripe's $0.50 minimum charge guarantees
# fee < amount at standard pricing), so sweep f strictly below a.

CASES = [(a, f, a - f)
         for a in (1, 99, 100, 12345, 10**9)
         for f in (0, 1, 30, a // 3) if f < a]


def test_charge_shapes_all_balance():
    assert all(balances(charge_entry(txn(a, f, n))) for a, f, n in CASES)


def test_refund_shapes_all_balance():
    fee_returned = all(balances(refund_entry(txn(-a, 0, -n, type="refund")))
                       for a, _, n in CASES)
    fee_charged = all(balances(refund_entry(txn(-a, 0, -(a + f), type="refund")))
                      for a, f, _ in CASES)
    assert fee_returned and fee_charged


def test_suspense_shapes_all_balance():
    assert all(balances(suspense_entry(txn(n, 0, n, type="other")))
               for n in (-10**9, -1, 1, 10**9))
