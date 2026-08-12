# ledger-core — Design Contract

This document is the contract for `ledger-core` v1. It is written **before** any
implementation, and defines *what* the library guarantees — not *how* it does it.
No function bodies appear here. Implementation commits follow this one.

The library takes any list of transactions and returns a double-entry journal
that sums to zero — or a report of exactly what does not balance and why. Its
value is not the splitting arithmetic; it is two disciplines carried over from
the shipped `stripe-reconciler` code:

1. **Integer cents throughout.** No floats touch money, ever.
2. **Explicit Suspense, never a silent default.** Unknowns are routed somewhere
   visible instead of being quietly plugged.

The sellable sentence: *this tells you what doesn't balance instead of guessing.*

---

## 1. Money rule

All monetary amounts are **integer cents**. No `float` appears in any money field,
intermediate, or return value anywhere in the library. Formatting to a decimal
string for display is a presentation concern and uses integer `divmod`, never
float division.

A violation of this rule is a defect, not a rounding difference.

---

## 2. The models

Three types. All money fields are `int` cents.

### `Transaction`
The processor-agnostic input. The library never sees a Stripe object, a request,
or a database row — only this.

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | source identifier |
| `amount` | `int` | gross amount, integer cents |
| `fee` | `int` | processor fee, integer cents |
| `net` | `int` | amount − fee, integer cents |
| `type` | `str` | transaction kind (e.g. charge, refund, payout) |
| `created` | `int` | epoch seconds |
| `currency` | `str` | ISO 4217 code, e.g. `"usd"` |

`currency` is **required** in v1. See §5.

### `JournalEntry`
A double-entry record: a list of debit/credit lines that must sum to zero within
a single currency. A `JournalEntry` carries the `currency` of the transaction it
was generated from; every line in an entry shares that currency.

### `Account`
A named ledger account drawn from a fixed chart of accounts (revenue, fees, cash,
Suspense, etc.). Accounts are identifiers, not balances.

---

## 3. The zero-sum invariant and the balance report

The central guarantee: **for every `JournalEntry`, total debits equal total
credits, within its currency.**

The balance check is the centerpiece of the library, and it has one contract that
governs its entire API shape:

> **The balance check never raises. It always returns a report.**

- On success the report states the entry (or batch) is balanced.
- On failure the report names *what* did not balance and *by how much* — the
  offending entry, the debit and credit totals, and their difference in integer
  cents.

This is deliberate. The pitch is "tells you what doesn't balance," which is a
value a caller **returns and inspects**, not an exception they must catch. It
also means a caller can check an entire batch and collect *every* problem in one
pass, rather than dying on the first bad entry.

Callers who *want* exception semantics can raise on a non-balanced report
themselves; the library will not make that choice for them.

A property test asserts that every journal the library generates from valid
input sums to zero within its currency. This test exists from the first
implementing commit.

---

## 4. Suspense routing

When a transaction cannot be classified into a known entry shape, or carries a
value the generators cannot account for, it is routed to an explicit **Suspense**
account — never dropped, never silently defaulted, never guessed.

Suspense is a visible, first-class destination. A transaction landing there is a
signal to a human, not a failure hidden from one. The presence of a Suspense
entry in a returned journal is itself information the caller is meant to see.

**Open decision deferred to implementation, flagged here:** the shipped code
couples Suspense routing with a `skipped` / review-list semantic — a transaction
routed to Suspense is *also* recorded for human review. v1 must decide whether
that review-list behavior lives inside the library or is handed to the caller.
This is noted as a contract question, not resolved in this document.

---

## 5. Currency guard

`Transaction` carries a required `currency` field. The library enforces one rule
in v1:

> **The balance check refuses to net amounts across differing currencies. Two
> lines in different currencies never sum against each other. A cross-currency
> attempt produces a balance report describing the mismatch — it does not guess,
> and it does not raise.**

This is **not** multi-currency support. It is the same discipline as integer
cents and explicit Suspense: make the unknown *visible* rather than silently
plugging it. A journal mixing USD and EUR does not quietly sum to a meaningless
number and report "balanced"; the report names the currency mismatch.

### Explicit non-goal (v2)

Full multi-currency support — per-currency sub-ledgers, foreign-exchange
conversion, and per-currency balance checking across a mixed journal — is **out
of scope for v1**. It is a larger project: it changes what "balanced" means (from
one scalar to balanced-per-currency), reshapes the balance report, and drags in
FX modeling (a EUR payout settling from USD charges is an FX event, not a balance
check). v1 deliberately stops at the guard.

---

## 6. Scope boundary

**In v1:**
- `Transaction`, `JournalEntry`, `Account` models, integer cents
- Revenue / fee splitting for the known transaction shapes
- Explicit Suspense routing for unknowns
- The zero-sum balance check that returns a report
- The currency guard of §5
- A Stripe input adapter (free tier)
- Tests from the first implementing commit, including the zero-sum property test

**Not in v1:**
- Full multi-currency / FX (§5)
- Persistence, logging, HTTP, or any host-application concern — these belong to
  the caller, not the library
- Additional input connectors beyond Stripe — these are a later, separately
  packaged paid tier

---

## 7. What the library does not do

The library holds no state, performs no I/O, and names no processor in its core.
It does not persist journals, does not log, does not retry, and does not decide
what a caller does with an unbalanced report. It converts transactions to a
journal and tells the truth about whether that journal balances. Everything else
is the host's job.
