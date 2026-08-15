"""Tests for the Stripe input adapter.

No network and no Stripe SDK — the adapter takes a mapping, so a dict is a
faithful stand-in for a live object.
"""
import pytest

from ledger_core import charge_entry, manual_payout_entry
from ledger_core.adapters.stripe import stripe_to_payout, stripe_to_transaction

RAW_TXN = {
    "id": "txn_1",
    "amount": 1000,
    "fee": 59,
    "net": 941,
    "type": "charge",
    "created": 1751000000,
    "currency": "usd",
}

RAW_PAYOUT = {
    "id": "po_1",
    "amount": 9380,
    "created": 1751000000,
    "balance_transaction": "txn_po",
    "currency": "usd",
}


def test_transaction_round_trips_every_field():
    txn = stripe_to_transaction(RAW_TXN)
    assert (txn.id, txn.amount, txn.fee, txn.net) == ("txn_1", 1000, 59, 941)
    assert (txn.type, txn.created, txn.currency) == ("charge", 1751000000, "usd")


def test_a_missing_currency_is_loud_not_defaulted():
    # Defaulting an unlabeled amount to USD would turn a cross-currency
    # mismatch the balance report is meant to catch into a false "balanced".
    raw = {k: v for k, v in RAW_TXN.items() if k != "currency"}
    with pytest.raises(KeyError):
        stripe_to_transaction(raw)


@pytest.mark.parametrize("missing", ["id", "amount", "fee", "net", "type", "created"])
def test_every_missing_transaction_field_is_loud(missing):
    raw = {k: v for k, v in RAW_TXN.items() if k != missing}
    with pytest.raises(KeyError):
        stripe_to_transaction(raw)


def test_non_usd_currency_passes_through_untouched():
    txn = stripe_to_transaction({**RAW_TXN, "currency": "eur"})
    assert txn.currency == "eur"
    # and it reaches the entry, which is what §5 later checks against
    assert charge_entry(txn).currency == "eur"


def test_payout_round_trips_every_field():
    payout = stripe_to_payout(RAW_PAYOUT)
    assert (payout.id, payout.amount, payout.created) == ("po_1", 9380, 1751000000)
    assert payout.balance_transaction == "txn_po"
    assert payout.currency == "usd"


def test_unposted_payout_has_no_balance_transaction():
    # Stripe leaves it null until the payout posts; absent and null are the
    # same state and both are expected.
    assert stripe_to_payout({**RAW_PAYOUT, "balance_transaction": None}) \
        .balance_transaction is None

    raw = {k: v for k, v in RAW_PAYOUT.items() if k != "balance_transaction"}
    assert stripe_to_payout(raw).balance_transaction is None


def test_expanded_balance_transaction_is_unwrapped_to_its_id():
    # Stripe may expand this into an object. An object arriving where an id
    # is expected would become a source id no bookkeeper could trace.
    payout = stripe_to_payout({
        **RAW_PAYOUT,
        "balance_transaction": {"id": "txn_po", "object": "balance_transaction"},
    })
    assert payout.balance_transaction == "txn_po"
    assert manual_payout_entry(payout).source_id == "txn_po"


def test_a_missing_payout_currency_is_loud():
    raw = {k: v for k, v in RAW_PAYOUT.items() if k != "currency"}
    with pytest.raises(KeyError):
        stripe_to_payout(raw)


def test_adapted_data_flows_straight_into_a_balanced_entry():
    entry = charge_entry(stripe_to_transaction(RAW_TXN))
    assert entry.source_id == "txn_1"
    assert entry.currency == "usd"
    assert sum(line["debit"] for line in entry.lines) == sum(line["credit"] for line in entry.lines)
