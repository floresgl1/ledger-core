"""The entry generators: one transaction in, one balanced JournalEntry out.

Lifted from stripe-reconciler `backend/app.py`. The arithmetic is unchanged —
it is the part that was already correct and already tested. Two things differ,
both required by the contract:

- Each generator returns a `JournalEntry` carrying the transaction's currency
  (§2) instead of a bare list of line dicts. The lines themselves are built
  exactly as before.
- `manual_payout_entry` takes a `Payout` dataclass instead of a live Stripe
  object, so it is callable without the network like the other four.

The five copy-pasted balance guards below are still copy-pasted. They are
replaced by a single helper in the balance module, which is a design change
(§3: the balance check returns a report and never raises) and lands in its own
commit rather than being smuggled in with a lift.
"""
from __future__ import annotations

from .model import (
    ACCOUNT_CASH_IN_TRANSIT,
    ACCOUNT_PROCESSING_FEES,
    ACCOUNT_SALES_REVENUE,
    ACCOUNT_STRIPE_BANK,
    ACCOUNT_SUSPENSE,
    JournalEntry,
    Payout,
    Transaction,
)


def charge_entry(txn: Transaction) -> JournalEntry:
    """Turn one charge into a balanced QuickBooks style journal entry. All
    values in cents.

    Named `generate_journal_entry` in stripe-reconciler, where it was the only
    generator and the name could be generic. In a library where every function
    generates a journal entry, it says what it books.
    """
    entry = [
        {"account": ACCOUNT_STRIPE_BANK, "debit": txn.net, "credit": 0},
        {"account": ACCOUNT_PROCESSING_FEES, "debit": txn.fee, "credit": 0},
        {"account": ACCOUNT_SALES_REVENUE, "debit": 0, "credit": txn.amount},
    ]

    total_debits = sum(line["debit"] for line in entry)
    total_credits = sum(line["credit"] for line in entry)
    if total_debits != total_credits:
        raise ValueError(f"Entry doesn't balance: {total_debits} != {total_credits}")

    return JournalEntry(
        source_id=txn.id,
        created=txn.created,
        currency=txn.currency,
        lines=entry,
    )


def refund_entry(txn: Transaction) -> JournalEntry:
    """Turn one refund into a balanced QuickBooks style journal entry. All
    values in cents."""
    # Signed fee (net = amount - fee). Negative means Stripe returned the
    # original fee (credit: expense reduced); positive means Stripe charged
    # a fee on the refund (debit: extra expense). A fixed credit side would
    # unbalance the entry whenever the fee is positive.
    fee = txn.amount - txn.net
    entry = [
        {"account": ACCOUNT_STRIPE_BANK, "debit": 0, "credit": abs(txn.net)},
        {
            "account": ACCOUNT_PROCESSING_FEES,
            "debit": fee if fee > 0 else 0,
            "credit": abs(fee) if fee < 0 else 0,
        },
        {"account": ACCOUNT_SALES_REVENUE, "debit": abs(txn.amount), "credit": 0},
    ]

    total_debits = sum(line["debit"] for line in entry)
    total_credits = sum(line["credit"] for line in entry)
    if total_debits != total_credits:
        raise ValueError(f"Entry doesn't balance: {total_debits} != {total_credits}")

    return JournalEntry(
        source_id=txn.id,
        created=txn.created,
        currency=txn.currency,
        lines=entry,
    )


def suspense_entry(txn: Transaction) -> JournalEntry:
    """Route a transaction the generators cannot classify to Suspense (§4).

    Never dropped, never silently defaulted, never guessed. The direction
    follows the cash: a negative net means money left the balance, so Suspense
    is debited and the balance credited; a positive net is the mirror image.
    """
    amount = abs(txn.net)

    if txn.net < 0:
        entry = [
            {"account": ACCOUNT_SUSPENSE, "debit": amount, "credit": 0},
            {"account": ACCOUNT_STRIPE_BANK, "debit": 0, "credit": amount},
        ]
    else:
        entry = [
            {"account": ACCOUNT_SUSPENSE, "debit": 0, "credit": amount},
            {"account": ACCOUNT_STRIPE_BANK, "debit": amount, "credit": 0},
        ]

    total_debits = sum(line["debit"] for line in entry)
    total_credits = sum(line["credit"] for line in entry)
    if total_debits != total_credits:
        raise ValueError(f"Entry doesn't balance: {total_debits} != {total_credits}")

    return JournalEntry(
        source_id=txn.id,
        created=txn.created,
        currency=txn.currency,
        lines=entry,
    )


def payout_entry(txn: Transaction) -> JournalEntry:
    """The payout leg of an automatic payout: cash leaving the Stripe balance
    for the bank. This is the second half of the charges in the same journal —
    they debit the Stripe balance (money in), the payout credits it (money
    out), so the balance washes to zero and the cash lands in Cash in Transit.

    Not Suspense: a payout is the least ambiguous line in the journal, not a
    transaction that needs human reclassification.

    txn.net is the balance impact (negative — funds leaving). Any payout fee
    (only instant payouts carry one) is booked as an expense so the balance
    still washes to zero; for a standard bank payout the fee is zero and this
    is a clean two-line entry. Uses the same accounts as manual_payout_entry,
    so a payout books the same way whichever schedule produced it."""
    cash = abs(txn.amount)   # what actually arrives at the bank
    fee = txn.fee            # payout fee; 0 for standard bank payouts
    out = abs(txn.net)       # total leaving the Stripe balance (= cash + fee)
    entry = [{"account": ACCOUNT_CASH_IN_TRANSIT, "debit": cash, "credit": 0}]
    if fee:
        entry.append({"account": ACCOUNT_PROCESSING_FEES, "debit": fee, "credit": 0})
    entry.append({"account": ACCOUNT_STRIPE_BANK, "debit": 0, "credit": out})

    total_debits = sum(line["debit"] for line in entry)
    total_credits = sum(line["credit"] for line in entry)
    if total_debits != total_credits:
        raise ValueError(f"Entry doesn't balance: {total_debits} != {total_credits}")

    return JournalEntry(
        source_id=txn.id,
        created=txn.created,
        currency=txn.currency,
        lines=entry,
    )


def manual_payout_entry(payout: Payout) -> JournalEntry:
    """The one journal entry a manual payout can honestly support: the cash
    leaving the Stripe balance for the bank.

    A manual payout is an amount the merchant chose to withdraw, and Stripe
    records no mapping from it to the charges or refunds it 'covers' — the
    available balance is a single pool, not a labeled set — so any
    per-transaction breakdown would be inferred rather than sourced. This
    treasury leg is the part that is provable, and it balances on its own."""
    amount = payout.amount
    entry = [
        {"account": ACCOUNT_CASH_IN_TRANSIT, "debit": amount, "credit": 0},
        {"account": ACCOUNT_STRIPE_BANK, "debit": 0, "credit": amount},
    ]

    total_debits = sum(line["debit"] for line in entry)
    total_credits = sum(line["credit"] for line in entry)
    if total_debits != total_credits:
        raise ValueError(f"Entry doesn't balance: {total_debits} != {total_credits}")

    # The payout's own balance transaction is the traceable source id for this
    # leg; fall back to the payout id if Stripe hasn't posted it yet. The
    # shipped version reached for this with getattr() because it was handed a
    # live Stripe object that might not carry the attribute at all; on a
    # dataclass the field always exists and may be None, so `or` is the whole
    # check.
    return JournalEntry(
        source_id=payout.balance_transaction or payout.id,
        created=payout.created,
        currency=payout.currency,
        lines=entry,
    )
