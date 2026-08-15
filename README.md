# ledger-core

Takes any list of transactions and returns a double-entry journal that sums to
zero — or a report of exactly what does not balance and why.

**This tells you what doesn't balance instead of guessing.**

The value is not the splitting arithmetic. It is two disciplines:

1. **Integer cents throughout.** No floats touch money, ever.
2. **Explicit Suspense, never a silent default.** Unknowns are routed somewhere
   visible instead of being quietly plugged.

The full contract is [docs/DESIGN.md](docs/DESIGN.md), written before any
implementation. Where this README and that document disagree, the document
wins.

## Install

```sh
pip install ledger-tieout
```

```python
import ledger_core
```

The two names differ on purpose. `ledger-core` was taken on PyPI in all but
punctuation — `ledgercore` is an existing project, and PyPI compares names with
separators stripped — so the distribution is published as **ledger-tieout**,
after the thing it does: a batch that ties out. The import package keeps its
name.

Zero runtime dependencies. The library holds no state, does no I/O, and names
no processor in its core. It ships a PEP 561 `py.typed` marker, so its type
hints are visible to callers' type checkers.

## Use

```python
from ledger_core import Transaction, check_journal, entries_for

transactions = [
    Transaction(id="txn_1", amount=1000, fee=59, net=941,
                type="charge", created=1751000000, currency="usd"),
    ...
]

journal = entries_for(transactions)
report = check_journal(journal.entries)

if not report.balanced:
    print(report.summary())
```

`entries_for` books every transaction it is given. Known shapes go to their
generator; anything else routes to an explicit **Suspense** account and is
recorded for review, with the reason attached:

```
5 entries booked, 3 items need review:
  - adj_1: unrecognized type 'adjustment', routed to Suspense
  - adj_0: unrecognized type 'adjustment', nothing to book
  - bad_1: failed its balance check, routed to Suspense (bad_1: 9.59 USD debits != 10.00 USD credits (off by -0.41 USD))
```

Nothing is ever dropped. A transaction whose generator rejects its data still
lands in the journal via Suspense, so the batch keeps tying out, and the
review item says *why* it is there — "no rule for this type" and "rule exists,
data is broken" are different problems needing different fixes.

Manual payouts are booked in the same call. They arrive as `Payout` objects
rather than transactions, so they are a second argument:

```python
journal = entries_for(transactions, payouts=payouts)
```

A payout Stripe returns *both* ways — as a balance transaction and as a
`Payout` — is booked once, not twice, and the refused duplicate shows up in
the review list saying so. Merging the two lists by hand is what makes that
double-booking invisible: both entries balance on their own, so the journal
still reports "balanced" while being wrong by the payout amount.

The individual generators (`charge_entry`, `refund_entry`, `payout_entry`,
`suspense_entry`, `manual_payout_entry`) are public too, if you want to book
one transaction yourself. Unlike the balance check, they raise
`UnbalancedEntry` on data that cannot produce a balanced record.

The balance check **never raises**. It returns a report you inspect:

```
NOT balanced: 3 problems across 2 entries.
  - txn_over: 15.00 USD debits != 10.00 USD credits (off by 5.00 USD)
  - txn_under: 10.00 EUR debits != 15.00 EUR credits (off by -5.00 EUR)
  - journal mixes 2 currencies: USD (1), EUR (1). Amounts in different currencies are not netted against each other.
```

Amounts render with the number of digits their currency actually has: 150 JPY
is `150 JPY`, not `1.50 JPY`. `minor_unit_exponent` exposes the table if you
format your own output.

That last line is the currency guard. Two lines in different currencies never
sum against each other — a journal mixing USD and EUR does not quietly sum to
a meaningless number and report "balanced". This is **not** multi-currency
support; it is the same discipline as integer cents, making the unknown
visible rather than plugging it.

`if report.balanced:` is the check to write. `BalanceReport` deliberately
defines no `__bool__`, because `if not report:` would silently never fire.

## Stripe input

```python
from ledger_core.adapters.stripe import stripe_to_transaction

txn = stripe_to_transaction(raw_balance_transaction)
```

Takes a plain mapping, so it needs neither the network nor the Stripe SDK. A
missing field raises rather than defaulting — a quietly defaulted `currency`
would turn a mismatch this library exists to catch into a false "balanced".

## Develop

```sh
pip install -e ".[dev]"
python -m pytest
ruff check .
mypy
```

CI runs all three across Python 3.10–3.13, plus a packaging job that builds
the distribution and checks the wheel actually ships its `LICENSE` and
`py.typed` — neither of which the test suite can see, because it imports from
the source tree.

The test job fails closed: a run that collects zero tests is an error, not a
pass.

Releases go out on a `v*` tag — see [docs/RELEASING.md](docs/RELEASING.md).
