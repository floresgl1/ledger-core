"""The Stripe input adapter (§6, free tier).

Lifted from stripe-reconciler `backend/adapters.py`, with `currency` added and
a payout converter alongside the transaction one.

These take a plain mapping — a dict, or anything that indexes like one, which
includes a live `stripe.BalanceTransaction`. The library does not import the
Stripe SDK and does not depend on it; that keeps §7's "names no processor in
its core" true at the dependency level too, and it is what makes these
functions testable without the network.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..model import Payout, Transaction

# What "a plain mapping" means in a signature. Mapping rather than dict so a
# live stripe.BalanceTransaction — which indexes like one without being one —
# still type-checks, and Any for the values because the fields are a mix of
# str and int and this adapter's job is to sort them into the typed model.
RawStripeObject = Mapping[str, Any]


def stripe_to_transaction(raw: RawStripeObject) -> Transaction:
    """A Stripe balance transaction to a ledger-core Transaction.

    Indexes every field rather than reaching for .get() with a default. A
    missing key raises KeyError here, loudly, at the boundary. That is the
    point: a defaulted `currency` — quietly deciding an unlabeled amount is
    USD — is precisely the silent plug §4 and §5 exist to refuse, and it would
    turn a cross-currency mismatch the balance report is meant to catch into a
    journal that reports "balanced".
    """
    return Transaction(
        id=raw['id'],
        amount=raw['amount'],
        fee=raw['fee'],
        net=raw['net'],
        type=raw['type'],
        created=raw['created'],
        currency=raw['currency'],
    )


def stripe_to_payout(raw: RawStripeObject) -> Payout:
    """A Stripe payout to a ledger-core Payout, for the manual-payout leg.

    New — upstream, `manual_payout_entry` read attributes straight off a live
    `stripe.Payout`, which is what made it untestable and what the Payout
    dataclass exists to undo.

    `balance_transaction` is the one optional field: Stripe leaves it null
    until the payout posts, and the entry falls back to the payout id for its
    source id. It is fetched with .get() for exactly that reason — absent and
    null are the same state here, and both are expected. Every other field is
    indexed.

    Stripe may return `balance_transaction` expanded into an object rather
    than an id string. Unwrapped here so callers get an id either way; an
    expanded object arriving where an id is expected would otherwise become a
    source id no bookkeeper could trace.
    """
    balance_transaction = raw.get('balance_transaction')
    if isinstance(balance_transaction, dict):
        balance_transaction = balance_transaction.get('id')

    return Payout(
        id=raw['id'],
        amount=raw['amount'],
        created=raw['created'],
        balance_transaction=balance_transaction,
        currency=raw['currency'],
    )
