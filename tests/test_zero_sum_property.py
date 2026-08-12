"""The zero-sum property test of DESIGN.md §3.

    The central guarantee: for every JournalEntry, total debits equal total
    credits, within its currency.

§3 requires this test to exist from the first implementing commit, so it does
— before the balance check it will eventually be the specification for. It
sweeps every generator across the value shapes that show up in real processor
data and asserts the invariant holds on all of them.

"Within its currency" has two halves, and both are asserted here:

1. The lines of an entry sum to zero.
2. The entry carries the currency of the input it was generated from, and it
   is the only currency in play. An entry whose currency drifted from its
   source would let a caller net two unrelated currencies against each other
   and call the result balanced — the exact failure §5 exists to prevent.

The sweep is deterministic rather than randomized: these are the shapes the
shipped code was proven against, and a property test that fails only on some
runs is a property test people learn to re-run. A generative pass (hypothesis)
would strengthen this and is worth adding once the input domain is pinned by
the balance report's own contract.
"""
import pytest

from ledger_core import (
    JournalEntry,
    Payout,
    Transaction,
    charge_entry,
    manual_payout_entry,
    payout_entry,
    refund_entry,
    suspense_entry,
)

# ISO 4217 codes only; the invariant must not care which one it is handed.
CURRENCIES = ("usd", "eur", "jpy")

AMOUNTS = (1, 99, 100, 12345, 10**9)
FEES = (0, 1, 30)


def _valid_cases():
    """(label, entry, source_currency) for every generator across value shapes.

    Only inputs the generators consider valid — §3 scopes the guarantee to
    valid input, and the shapes the guards reject (a charge violating
    net = amount - fee, a refund whose net flips positive) are covered as
    rejections in test_entries.py.
    """
    for currency in CURRENCIES:
        def txn(amount, fee, net, type):
            return Transaction(id="txn_t", amount=amount, fee=fee, net=net,
                               type=type, created=1751000000, currency=currency)

        for amount in AMOUNTS:
            for fee in FEES:
                if fee >= amount:
                    continue
                net = amount - fee

                yield (f"charge {amount}/{fee} {currency}",
                       charge_entry(txn(amount, fee, net, "charge")),
                       currency)

                # Stripe returned the original fee with the refund
                yield (f"refund fee-returned {amount}/{fee} {currency}",
                       refund_entry(txn(-amount, 0, -net, "refund")),
                       currency)

                # Stripe charged a fee on top of the refund
                yield (f"refund fee-charged {amount}/{fee} {currency}",
                       refund_entry(txn(-amount, 0, -(amount + fee), "refund")),
                       currency)

                # payout: what lands + fee == what leaves the balance
                yield (f"payout {amount}/{fee} {currency}",
                       payout_entry(txn(-amount, fee, -(amount + fee), "payout")),
                       currency)

                # suspense takes the unclassifiable in either direction
                for signed in (net, -net):
                    yield (f"suspense {signed} {currency}",
                           suspense_entry(txn(signed, 0, signed, "other")),
                           currency)

            yield (f"manual payout {amount} {currency}",
                   manual_payout_entry(Payout(id="po_1", amount=amount,
                                              created=1751000000,
                                              balance_transaction="txn_po",
                                              currency=currency)),
                   currency)


CASES = list(_valid_cases())
IDS = [label for label, _, _ in CASES]


def test_the_sweep_is_not_vacuous():
    """A property test that silently generates nothing passes forever."""
    assert len(CASES) > 200


@pytest.mark.parametrize("entry,currency", [(e, c) for _, e, c in CASES], ids=IDS)
def test_every_generated_entry_sums_to_zero_within_its_currency(
    entry: JournalEntry, currency: str
):
    debits = sum(line["debit"] for line in entry.lines)
    credits = sum(line["credit"] for line in entry.lines)

    assert debits == credits, (
        f"entry does not balance: {debits} debits != {credits} credits"
    )
    assert entry.currency == currency, (
        "entry currency drifted from its source; two entries in different "
        "currencies could then be netted against each other and called balanced"
    )
    assert entry.lines, "an entry with no lines sums to zero vacuously"


@pytest.mark.parametrize("entry", [e for _, e, _ in CASES], ids=IDS)
def test_no_float_ever_reaches_a_money_field(entry: JournalEntry):
    """DESIGN.md §1. A float here is a defect, not a rounding difference.

    bool is an int subclass and would pass isinstance, so it is excluded
    explicitly — True in a debit field is nonsense that happens to arithmetic
    correctly.
    """
    for line in entry.lines:
        for side in ("debit", "credit"):
            value = line[side]
            assert type(value) is int, (
                f"{line['account']} {side} is {type(value).__name__}, not int"
            )
