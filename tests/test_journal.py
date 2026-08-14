"""Tests for entries_for — dispatch and policy (DESIGN.md §4).

The tests that matter most here are the ones that fail if a transaction ever
goes missing. Everything else this module does is a lookup.
"""
from ledger_core import (
    ACCOUNT_CASH_IN_TRANSIT,
    ACCOUNT_SALES_REVENUE,
    ACCOUNT_STRIPE_BANK,
    ACCOUNT_SUSPENSE,
    REVIEW_ALREADY_BOOKED,
    REVIEW_FAILED_GUARD,
    REVIEW_UNKNOWN_TYPE,
    Payout,
    Transaction,
    check_journal,
    entries_for,
)


def txn(id="txn_1", amount=1000, fee=59, net=941, type="charge", currency="usd"):
    return Transaction(id=id, amount=amount, fee=fee, net=net, type=type,
                       created=1751000000, currency=currency)


def payout(id="po_1", amount=9380, balance_transaction=..., currency="usd"):
    """A payout whose balance_transaction defaults to the id the same payout
    would carry in the transaction list — the collision the dedupe is for."""
    if balance_transaction is ...:
        balance_transaction = f"txn_{id}"
    return Payout(id=id, amount=amount, created=1751000000,
                  balance_transaction=balance_transaction, currency=currency)


def accounts(entry):
    return {line["account"] for line in entry.lines}


# --- the known shapes dispatch to the right generator -------------------------

def test_charge_books_revenue():
    (entry,) = entries_for([txn(type="charge")]).entries
    assert ACCOUNT_SALES_REVENUE in accounts(entry)
    assert entry.source_id == "txn_1"


def test_refund_books_revenue_on_the_debit_side():
    (entry,) = entries_for([txn(amount=-1000, fee=0, net=-941, type="refund")]).entries
    by = {line["account"]: (line["debit"], line["credit"]) for line in entry.lines}
    assert by[ACCOUNT_SALES_REVENUE] == (1000, 0)


def test_payout_books_straight_through_not_to_suspense():
    journal = entries_for([txn(amount=-9380, fee=0, net=-9380, type="payout")])
    (entry,) = journal.entries
    assert ACCOUNT_SUSPENSE not in accounts(entry)
    assert journal.review == [], "a payout is the least ambiguous line in a journal"


def test_known_types_never_reach_the_review_list():
    journal = entries_for([
        txn("c", type="charge"),
        txn("r", amount=-1000, fee=0, net=-941, type="refund"),
        txn("p", amount=-9380, fee=0, net=-9380, type="payout"),
    ])
    assert len(journal.entries) == 3
    assert journal.review == []


# --- §4: unknown types --------------------------------------------------------

def test_unknown_type_routes_to_suspense_and_to_review():
    journal = entries_for([txn(id="adj_1", amount=300, fee=0, net=300,
                               type="adjustment")])

    (entry,) = journal.entries
    assert ACCOUNT_SUSPENSE in accounts(entry)

    (item,) = journal.review
    assert item.source_id == "adj_1"
    assert item.type == "adjustment"
    assert item.reason == REVIEW_UNKNOWN_TYPE
    assert item.booked_to_suspense is True
    assert item.discrepancy is None


def test_zero_net_unknown_is_surfaced_but_not_booked():
    # Nothing to book. An empty or 0/0 Suspense entry would manufacture a
    # problem rather than record one.
    journal = entries_for([txn(id="adj_0", amount=0, fee=0, net=0,
                               type="adjustment")])
    assert journal.entries == []

    (item,) = journal.review
    assert item.source_id == "adj_0"
    assert item.booked_to_suspense is False
    assert item.reason == REVIEW_UNKNOWN_TYPE


# --- the shipped defect, fixed ------------------------------------------------

def test_a_transaction_whose_generator_fails_is_not_dropped():
    """The regression test for app.py:990-1014.

    The shipped route gave a failed generator a skipped record and NO entry,
    so the transaction vanished and its payout silently stopped tying out.
    §4: never dropped.
    """
    journal = entries_for([txn(id="bad_1", amount=1000, fee=59, net=900)])

    (entry,) = journal.entries
    assert ACCOUNT_SUSPENSE in accounts(entry), "the failed charge was dropped"
    assert entry.source_id == "bad_1"


def test_a_failed_generator_says_why_not_just_that():
    journal = entries_for([txn(id="bad_1", amount=1000, fee=59, net=900)])

    (item,) = journal.review
    assert item.reason == REVIEW_FAILED_GUARD
    assert item.booked_to_suspense is True

    # The numbers are free at the catch site and irrecoverable afterwards.
    # The entry debited net + fee (900 + 59) against a 1000 credit, so it is
    # 41 cents short — not the 100 the amount/net gap might suggest. What the
    # review item reports is the imbalance of the entry, not the
    # inconsistency of the input that caused it.
    assert item.discrepancy is not None
    assert item.discrepancy.source_id == "bad_1"
    assert (item.discrepancy.debits, item.discrepancy.credits) == (959, 1000)
    assert item.discrepancy.difference == -41


def test_the_pathological_refund_is_routed_not_raised():
    # Upstream this raised out of the generator and the route caught it.
    journal = entries_for([txn(id="ref_bad", amount=-1, fee=0, net=29,
                               type="refund")])
    assert len(journal.entries) == 1
    assert journal.review[0].reason == REVIEW_FAILED_GUARD


def test_a_failed_generator_with_zero_net_keeps_its_reason():
    # booked_to_suspense=False because there is nothing to book, but the
    # reason must still say the data was broken, not merely unrecognized.
    journal = entries_for([txn(id="bad_0", amount=1000, fee=59, net=0)])
    assert journal.entries == []

    (item,) = journal.review
    assert item.reason == REVIEW_FAILED_GUARD
    assert item.booked_to_suspense is False
    assert item.discrepancy is not None


# --- batch behavior -----------------------------------------------------------

def test_one_bad_transaction_never_aborts_the_batch():
    journal = entries_for([
        txn("good_1"),
        txn("bad_1", amount=1000, fee=59, net=900),
        txn("good_2"),
        txn("adj_1", amount=300, fee=0, net=300, type="adjustment"),
        txn("good_3"),
    ])
    assert [e.source_id for e in journal.entries] == \
        ["good_1", "bad_1", "good_2", "adj_1", "good_3"]
    assert [r.source_id for r in journal.review] == ["bad_1", "adj_1"]


def test_nothing_is_dropped():
    """Every transaction is either booked or surfaced — §4's whole point."""
    transactions = [
        txn("good_1"),
        txn("bad_1", amount=1000, fee=59, net=900),
        txn("adj_1", amount=300, fee=0, net=300, type="adjustment"),
        txn("adj_0", amount=0, fee=0, net=0, type="adjustment"),
    ]
    journal = entries_for(transactions)

    seen = {e.source_id for e in journal.entries} | \
           {r.source_id for r in journal.review}
    assert seen == {t.id for t in transactions}


def test_an_empty_batch_produces_an_empty_journal():
    journal = entries_for([])
    assert journal.entries == []
    assert journal.review == []


def test_entries_for_does_not_raise_on_anything_the_models_accept():
    # Every shape that raised somewhere upstream, in one batch.
    journal = entries_for([
        txn("bad_charge", amount=1000, fee=59, net=900),
        txn("bad_refund", amount=-1, fee=0, net=29, type="refund"),
        txn("weird", amount=0, fee=0, net=0, type="stripe_fee"),
    ])
    assert len(journal.review) == 3


# --- manual payouts booked alongside the transactions -------------------------

def test_a_manual_payout_is_booked_from_the_payouts_argument():
    journal = entries_for([], payouts=[payout("po_1")])

    (entry,) = journal.entries
    assert entry.source_id == "txn_po_1"
    assert accounts(entry) == {ACCOUNT_CASH_IN_TRANSIT, ACCOUNT_STRIPE_BANK}
    assert journal.review == []


def test_payouts_and_transactions_land_in_one_journal():
    journal = entries_for([txn("c", amount=10000, fee=620, net=9380)],
                          payouts=[payout("po_1", amount=9380)])

    assert [e.source_id for e in journal.entries] == ["c", "txn_po_1"]
    assert check_journal(journal.entries).balanced


def test_the_same_payout_in_both_inputs_is_booked_once():
    """The double-booking this argument exists to prevent.

    Stripe returns an automatic payout twice — as a balance transaction and as
    a Payout — and both entries balance on their own, so a journal containing
    both still reports 'balanced' while being wrong by the payout amount.
    """
    journal = entries_for(
        [txn("txn_po_1", amount=-9380, fee=0, net=-9380, type="payout")],
        payouts=[payout("po_1", amount=9380)],  # balance_transaction txn_po_1
    )

    assert [e.source_id for e in journal.entries] == ["txn_po_1"]
    assert check_journal(journal.entries).balanced


def test_a_refused_duplicate_is_recorded_not_discarded():
    journal = entries_for(
        [txn("txn_po_1", amount=-9380, fee=0, net=-9380, type="payout")],
        payouts=[payout("po_1", amount=9380)],
    )

    (item,) = journal.review
    assert item.source_id == "txn_po_1"
    assert item.reason == REVIEW_ALREADY_BOOKED
    assert item.type == "payout"
    assert item.booked_to_suspense is False
    assert "already booked from the transaction list" in item.describe()
    assert "not booked twice" in item.describe()


def test_a_duplicate_is_not_reported_as_a_failure():
    """A correct refusal must not read like a bug — nobody should go looking
    for a Suspense routing failure that never happened."""
    journal = entries_for(
        [txn("txn_po_1", amount=-9380, fee=0, net=-9380, type="payout")],
        payouts=[payout("po_1", amount=9380)],
    )
    assert "NOT BOOKED" not in journal.review[0].describe()


def test_the_same_payout_twice_in_the_payouts_list_is_booked_once():
    journal = entries_for([], payouts=[payout("po_1"), payout("po_1")])

    assert len(journal.entries) == 1
    assert [r.reason for r in journal.review] == [REVIEW_ALREADY_BOOKED]


def test_an_unposted_payout_books_under_its_own_id():
    """Stripe leaves balance_transaction null until the payout posts, so there
    is no transaction-list id it could collide with."""
    journal = entries_for([], payouts=[payout("po_1", balance_transaction=None)])

    (entry,) = journal.entries
    assert entry.source_id == "po_1"
    assert journal.review == []


def test_the_review_item_agrees_with_the_transaction_form_on_direction():
    """Both forms describe one movement: cash leaving the Stripe balance."""
    journal = entries_for(
        [txn("txn_po_1", amount=-9380, fee=0, net=-9380, type="payout")],
        payouts=[payout("po_1", amount=9380)],
    )
    assert journal.review[0].net == -9380


def test_payouts_default_to_none_so_existing_calls_are_unaffected():
    assert entries_for([txn()]) == entries_for([txn()], payouts=None)
    assert len(entries_for([txn()]).entries) == 1


def test_nothing_is_dropped_from_the_payouts_list_either():
    payouts = [payout("po_1"), payout("po_1"), payout("po_2")]
    journal = entries_for([], payouts=payouts)

    seen = {e.source_id for e in journal.entries} | \
           {r.source_id for r in journal.review}
    assert seen == {"txn_po_1", "txn_po_2"}


# --- the journal it produces actually balances --------------------------------

def test_a_dispatched_journal_balances():
    journal = entries_for([
        txn("c", amount=10000, fee=620, net=9380),
        txn("p", amount=-9380, fee=0, net=-9380, type="payout"),
        txn("adj", amount=300, fee=0, net=300, type="adjustment"),
        txn("bad", amount=1000, fee=59, net=900),
    ])
    assert check_journal(journal.entries).balanced


def test_currency_is_left_to_the_balance_report_not_rejected_here():
    # §5 puts the mismatch in the report. entries_for books the batch and the
    # report names the problem.
    journal = entries_for([txn("usd_1", currency="usd"),
                           txn("eur_1", currency="eur")])
    assert len(journal.entries) == 2
    assert [e.currency for e in journal.entries] == ["usd", "eur"]

    report = check_journal(journal.entries)
    assert not report.balanced
    assert report.currency_mismatch is not None


# --- the review list as a thing a human reads ---------------------------------

def test_summary_of_a_clean_batch():
    assert entries_for([txn()]).summary() == "1 entry booked, nothing needs review."


def test_summary_names_each_problem():
    text = entries_for([
        txn("bad_1", amount=1000, fee=59, net=900),
        txn("adj_1", amount=300, fee=0, net=300, type="adjustment"),
        txn("adj_0", amount=0, fee=0, net=0, type="adjustment"),
    ]).summary()

    assert "3 items need review" in text
    assert "bad_1: failed its balance check, routed to Suspense" in text
    assert "9.59 USD debits != 10.00 USD credits" in text
    assert "off by -0.41 USD" in text
    assert "adj_1: unrecognized type 'adjustment', routed to Suspense" in text
    assert "adj_0: unrecognized type 'adjustment', nothing to book" in text
