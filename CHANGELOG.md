# Changelog

All notable changes to this project are recorded here. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The unit of change worth reading in this project is the *contract*, not the
diff: [docs/DESIGN.md](docs/DESIGN.md) defines what the library guarantees,
and entries below say which of those guarantees moved.

## [Unreleased]

Nothing yet.

## [0.1.0] — unreleased

First release. Implements the v1 contract in `docs/DESIGN.md` in full.

Published on PyPI as **ledger-tieout**. The import package is `ledger_core`
and does not change: `ledgercore` already exists on PyPI, and the name
comparison there strips separators, so `ledger-core` — and `ledger_core`, and
every other punctuation variant — collides with it. The distribution is named
after what the library does rather than where it sits in an architecture.

### Added

- **Models (§2).** `Transaction`, `Payout`, and `JournalEntry`, all money in
  integer cents, plus the chart of accounts. `currency` is required on
  `Transaction`; nothing defaults it.
- **Entry generators.** `charge_entry`, `refund_entry`, `payout_entry`,
  `suspense_entry`, and `manual_payout_entry`. Each returns a balanced
  `JournalEntry` carrying its source transaction's currency, and raises
  `UnbalancedEntry` on data that cannot produce one.
- **The balance check (§3).** `check_entry` and `check_journal` never raise —
  they return a `BalanceReport` naming every problem in one pass, each with
  both totals and the signed difference in cents. `BalanceReport` defines no
  `__bool__`, so the inverted `if not report:` cannot be written by accident.
- **Suspense routing and the review list (§4).** `entries_for` books every
  transaction it is given: known types to their generator, everything else to
  an explicit Suspense account, and a transaction whose generator rejects its
  data to Suspense as well rather than dropping it. Each review item says
  *why* it is there — an unrecognized type and a failed balance check are
  different problems. This resolves the open contract question §4 deferred to
  implementation: the review list lives in the library.
- **Manual payouts in the same call.** `entries_for(transactions, payouts=…)`
  books `Payout` objects alongside the transactions. A payout Stripe returns
  in both forms — a balance transaction *and* a `Payout` — is booked once and
  the duplicate is recorded for review (`REVIEW_ALREADY_BOOKED`). Doing that
  merge by hand is what hides the double-booking: both entries balance
  individually, so the journal reports "balanced" while being wrong by the
  payout amount.
- **The currency guard (§5).** A journal mixing currencies is reported as a
  mismatch, naming which source ids came from which currency. Amounts in
  different currencies are never netted against each other.
- **Stripe adapter (§6).** `stripe_to_transaction` and `stripe_to_payout` take
  a plain mapping, so they need neither the network nor the Stripe SDK. A
  missing field raises rather than defaulting.
- **Display helpers.** `format_cents` and `format_amount`, both integer
  `divmod` throughout (§1). A float raises at the boundary instead of being
  rendered as a plausible amount.
- **Tests from the first implementing commit**, including the zero-sum
  property sweep across every generator and a check that the sweep is not
  vacuous. CI fails closed: a run that collects zero tests is an error, not a
  pass.
- **Packaging.** MIT `LICENSE`, and a PEP 561 `py.typed` marker so the
  library's annotations reach downstream type checkers. CI asserts both are
  present in the built wheel.
- **Lint and type checking.** `ruff check` and `mypy --strict` over the
  library, both configured in `pyproject.toml` and both run in CI. Python 3.13
  is in the test matrix.
- **Release workflow.** A `v*` tag builds, verifies, and publishes to PyPI via
  trusted publishing — no API token in the repository or its secrets. The
  build job refuses a tag that disagrees with the packaged version, because a
  PyPI version number cannot be reused once taken. See
  [docs/RELEASING.md](docs/RELEASING.md).

### Fixed

- `Journal.summary()` read "1 transaction need review", and called every
  review item a transaction now that payouts can appear among them. It reports
  "1 item needs review" / "3 items need review".
- A defect carried from the `stripe-reconciler` route this library was
  extracted from (`app.py:990-1014`): a transaction whose generator raised was
  recorded for review and then **dropped from the journal entirely**, so the
  payout it belonged to silently stopped tying out. It now routes to Suspense
  like any other unclassifiable transaction and carries its discrepancy into
  the review list.

### Known limitations

Recorded rather than hidden — each is deliberate in v1 and has a test pinning
current behavior.

- `format_cents` hardcodes a `$` and takes no currency argument. Use
  `format_amount` wherever the currency is known; it is what the balance
  report uses.
- `format_amount` assumes a two-decimal minor unit, so zero-decimal (JPY, KRW)
  and three-decimal (BHD, KWD) currencies are misrendered. This is a display
  limitation only — the balance check compares cents to cents within one
  currency and is exponent-agnostic.
- Full multi-currency support and FX are an explicit non-goal for v1 (§5). v1
  stops at the guard.
- The duplicate-payout guard matches on source id — a payout's
  `balance_transaction` against the ids already booked. Two genuinely distinct
  cash movements sharing an id would be treated as one, and a duplicate payout
  whose `balance_transaction` is still null (Stripe has not posted it) is not
  detectable at all, because there is nothing yet to match on.
