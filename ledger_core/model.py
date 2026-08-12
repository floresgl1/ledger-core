"""The models of DESIGN.md §2, plus the chart of accounts.

All money fields are integer cents (§1). No float appears in any money field,
intermediate, or return value anywhere in this library.

Lifted from stripe-reconciler `backend/adapters.py`, with `currency` added to
`Transaction` as §2 requires.
"""
from __future__ import annotations

from dataclasses import dataclass


# --- chart of accounts -------------------------------------------------------
# Accounts are identifiers, not balances (§2).

ACCOUNT_STRIPE_BANK = "Stripe Bank Account"
ACCOUNT_SALES_REVENUE = "Sales Revenue"
ACCOUNT_PROCESSING_FEES = "Stripe Processing Fees"
ACCOUNT_SUSPENSE = "Suspense"
# Where a payout's cash lands when it leaves the Stripe balance for the real
# bank. Used for manual payouts, whose constituent transactions Stripe does
# not record — the treasury leg is the only entry they can honestly support.
ACCOUNT_CASH_IN_TRANSIT = "Cash in Transit"


@dataclass
class Transaction:
    """Processor-agnostic transaction. All money in integer cents — never floats.

    The library never sees a Stripe object, a request, or a database row — only
    this (§2). Getting one of these from a processor is an adapter's job.

    `currency` is required in v1 (§2). It is the currency of every line in any
    entry generated from this transaction, and the thing the balance check
    refuses to net across (§5).
    """
    id: str
    amount: int
    fee: int
    net: int
    type: str
    created: int
    currency: str  # ISO 4217, lowercase, e.g. "usd"


@dataclass
class Payout:
    """A payout, as the manual-payout leg needs it.

    Exists so `manual_payout_entry` takes plain data instead of a live Stripe
    object — the shipped version read attributes off a `stripe.Payout` and so
    could not be called without the network. Four of these fields come straight
    off that object; `currency` is here because §2 requires every JournalEntry
    to carry the currency it was generated from, and a payout is the one
    generator input that is not a Transaction. Without it the manual-payout leg
    would have to default a currency, which is exactly the silent plug §4
    forbids.

    `balance_transaction` is the payout's own balance-transaction id, or None
    when Stripe has not posted it yet.
    """
    id: str
    amount: int
    created: int
    balance_transaction: str | None
    currency: str


@dataclass
class JournalEntry:
    """A double-entry record: debit/credit lines that must sum to zero within a
    single currency (§3).

    Carries the `currency` of the transaction it was generated from; every line
    in the entry shares that currency (§2).

    `lines` are plain dicts — {"account": str, "debit": int, "credit": int} —
    exactly as the shipped generators built them. §2 names three models, so a
    Line type would be a fourth; the dict shape stays until something needs
    more from it than the balance check does.

    `source_id` and `created` were carried in an ad-hoc wrapper dict at the
    call site in the shipped route (`{"transaction_id", "created",
    "journal_entry"}`). They belong on the record itself: an entry that cannot
    name the transaction it came from is not traceable, and traceability is
    what the library sells.

    This type deliberately has no `is_balanced` / `total_debits` helpers. The
    balance check is its own module and its own contract (§3).
    """
    source_id: str
    created: int
    currency: str
    lines: list[dict]
