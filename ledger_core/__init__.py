"""ledger-core — transactions in, a double-entry journal out.

The library takes any list of transactions and returns a journal that sums to
zero, or a report of exactly what does not balance and why. See docs/DESIGN.md
for the contract this implements.

Two disciplines carried over from the shipped stripe-reconciler code:
integer cents throughout, and explicit Suspense rather than a silent default.
"""
from __future__ import annotations

from .balance import (
    DISCREPANCY_EMPTY,
    DISCREPANCY_UNBALANCED,
    BalanceReport,
    CurrencyMismatch,
    Discrepancy,
    UnbalancedEntry,
    assert_balanced,
    check_entry,
    check_journal,
)
from .entries import (
    charge_entry,
    manual_payout_entry,
    payout_entry,
    refund_entry,
    suspense_entry,
)
from .journal import (
    REVIEW_ALREADY_BOOKED,
    REVIEW_FAILED_GUARD,
    REVIEW_UNKNOWN_TYPE,
    Journal,
    ReviewItem,
    entries_for,
)
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
from .money import format_amount, format_cents, minor_unit_exponent

__all__ = [
    "ACCOUNT_CASH_IN_TRANSIT",
    "ACCOUNT_PROCESSING_FEES",
    "ACCOUNT_SALES_REVENUE",
    "ACCOUNT_STRIPE_BANK",
    "ACCOUNT_SUSPENSE",
    "DISCREPANCY_EMPTY",
    "DISCREPANCY_UNBALANCED",
    "REVIEW_ALREADY_BOOKED",
    "REVIEW_FAILED_GUARD",
    "REVIEW_UNKNOWN_TYPE",
    "BalanceReport",
    "CurrencyMismatch",
    "Discrepancy",
    "Journal",
    "JournalEntry",
    "Payout",
    "ReviewItem",
    "Transaction",
    "UnbalancedEntry",
    "assert_balanced",
    "charge_entry",
    "check_entry",
    "check_journal",
    "entries_for",
    "format_amount",
    "format_cents",
    "manual_payout_entry",
    "minor_unit_exponent",
    "payout_entry",
    "refund_entry",
    "suspense_entry",
]
