"""The balance check — DESIGN.md §3 and §5.

SKELETON. Signatures, contracts and the settled design; the logic is yours.

    The balance check never raises. It always returns a report.

Everything about the public API's shape follows from that one line. A caller
returns and inspects a report, so a batch check collects every problem in one
pass instead of dying on the first bad entry.


THE SETTLED DESIGN: two functions, two audiences (option B)
----------------------------------------------------------
The five copy-pasted guards in entries.py are load-bearing behavior, not
hygiene. They trip on bad *input data* — a charge whose net != amount - fee, a
refund whose net flips positive — and stripe-reconciler depended on the raise:
app.py:1007 caught the ValueError and diverted that one transaction to the
skipped/review list, so a single bad row never aborted a whole payout.

So this module serves two audiences with two different contracts:

  assert_balanced(entry)      internal. Raises on an unbalanced entry. Called
                              by every generator, replacing the five copies.
                              Fails fast on bad input, exactly as today.

  check_entry(entry)          public. Never raises. Returns a report.
  check_journal(entries)      public. Never raises. Returns a report.

§3 is satisfied as written: *the balance check* never raises. The guard is not
the balance check — it is a generator's precondition on its own input.

The known cost, recorded so nobody rediscovers it in six months: §3 says the
library will not make the exception choice for the caller, and the guard makes
it for them one layer down. That was weighed and accepted. What keeps it
honest is that the guard is a documented property of the *generators*, not of
the balance API, and callers of the balance API get the report semantics §3
promises.

CONSEQUENCE FOR STEP 4: entries_for must wrap each generator call in
try/except and route the failures to the review list. That is not incidental —
it is where the §4 skipped-list question gets answered.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import JournalEntry


class UnbalancedEntry(ValueError):
    """Raised by assert_balanced when an entry's debits do not equal its credits.

    Subclasses ValueError deliberately. The shipped route catches ValueError
    (app.py:1007) and tests/test_entries.py asserts ValueError, so existing
    handlers keep working while callers who want to be specific can be.

    TODO: decide what it carries. A bare message means a caller who catches it
    has to re-derive the numbers to report them. Attaching the Discrepancy
    below means the raise path and the report path describe a failure
    identically, which is the whole point of the shared implementation noted
    on assert_balanced.
    """


@dataclass
class Discrepancy:
    """One thing that did not balance, named precisely enough to act on.

    §3 requires a failure to name *what* did not balance and *by how much*:
    the offending entry, the debit and credit totals, and their difference in
    integer cents.

    TODO: settle the fields. The working set is:
        source_id: str        which entry — JournalEntry.source_id
        currency: str         the entry's currency
        debits: int           total debits, integer cents
        credits: int          total credits, integer cents
        difference: int       debits - credits, integer cents, signed

    TODO: decide whether `difference` is stored or derived. Storing it
    duplicates state that can disagree with itself; deriving it via @property
    means the number §3 promises isn't visible in a repr() or a JSON dump
    without help. Lean stored-and-computed-once, but it is a real trade.
    """


@dataclass
class CurrencyMismatch:
    """A journal that mixes currencies (§5).

    Not an error and not an exception — another kind of unbalanced. §5 is
    explicit that the library does not net across currencies and does not
    guess: a journal holding USD and EUR does not quietly sum to a
    meaningless number and report "balanced".

    Kept a separate type from Discrepancy rather than one type with optional
    fields: a mismatch has no single currency and its "difference" is not one
    number, so the optional fields would be lies half the time.

    TODO: fields. Minimum is the set of currencies found and which entries
    carried each, so a caller can see *where* the mixing came from rather than
    just that it happened.
    """


@dataclass
class BalanceReport:
    """The return value of every public balance check. Never raised, always
    returned.

    TODO: fields. The working set is:
        balanced: bool                     the headline answer
        discrepancies: list[Discrepancy]   every entry that did not balance
        currency_mismatch: CurrencyMismatch | None

    TODO — still open, my recommendation is no: does this define __bool__?
    `__bool__ = balanced` reads beautifully (`if not report:`) and is a trap —
    `if report:` on a *failed* report is exactly the inverted check that
    passes review. Forcing the explicit `if report.balanced:` costs one word
    and cannot be misread. The library's pitch is making the unbalanced case
    visible; truthiness hides it.

    TODO: a human-readable summary — __str__ or an explicit .summary(). "Tells
    you what doesn't balance" is the sellable sentence, and it should be one
    print() away. Use money.format_cents for any cents in it (§1).
    """


def check_entry(entry: JournalEntry) -> BalanceReport:
    """Check one entry. Returns a report; never raises.

    A single JournalEntry carries one currency by construction (§2), so §5
    cannot bite here — every line already shares the entry's currency. This
    answers one question: do the debits equal the credits?

    TODO: sum debits, sum credits, compare, build the report.

    TODO: what about an entry with no lines? It sums to zero and is
    technically balanced, but it is almost certainly a generator bug rather
    than a real record. Decide: balanced, or a discrepancy of its own kind?
    tests/test_zero_sum_property.py already refuses to accept an empty entry
    as proof of anything.
    """
    raise NotImplementedError("skeleton — see TODOs above")


def check_journal(entries: list[JournalEntry]) -> BalanceReport:
    """Check a whole journal in one pass. Returns a report; never raises.

    This is the function the pitch is about. It must collect *every* problem,
    not stop at the first — a bookkeeper wants one list of what to fix, not
    one problem at a time across five runs.

    Two independent checks, and both must run:
      1. Per entry: debits == credits (§3).
      2. Across entries: are they all in the same currency (§5)? If not, the
         report names the mismatch. It does not net them, and it does not
         raise.

    TODO: implement, running both passes unconditionally. A batch can be
    cross-currency *and* contain an individually unbalanced entry; do not let
    the currency mismatch short-circuit the per-entry pass.

    TODO: decide what an empty list returns. Balanced-by-vacuity is defensible
    and is also how a silent bug upstream (zero transactions fetched) would
    present as a clean bill of health. Consider making emptiness visible in
    the report rather than reporting a confident "balanced".
    """
    raise NotImplementedError("skeleton — see TODOs above")


def assert_balanced(entry: JournalEntry) -> None:
    """Raise if the entry does not balance. The generators' precondition.

    This is the internal half of the two-audience design at the top of this
    module, and the single replacement for the five copy-pasted guards in
    entries.py. Not part of the public balance API — callers who want a
    verdict without an exception use check_entry.

    TODO: implement in terms of check_entry, not alongside it:

        report = check_entry(entry)
        if not report.balanced:
            raise UnbalancedEntry(...)

    Doing it this way matters more than it looks. Two separate implementations
    of "does this balance" is how the guard and the report end up disagreeing
    about the same entry — which would be the single most damaging bug this
    library could have, given what it sells. One summing path, two ways of
    delivering the answer.

    TODO: the message. Upstream was
        f"Entry doesn't balance: {total_debits} != {total_credits}"
    which omits *which* entry — fine when it was caught next to the
    transaction id, worth including now that it can surface anywhere. The
    Discrepancy already holds everything needed to format it.

    WIRING (do this only after the body above is real): replace the guard in
    all five generators in entries.py with a call to this, and drop the local
    total_debits / total_credits lines. Doing it before then turns every
    generator into a NotImplementedError and takes all 517 tests with it.
    """
    raise NotImplementedError("skeleton — see TODOs above")
