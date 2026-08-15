"""The balance check — DESIGN.md §3 and §5.

    The balance check never raises. It always returns a report.

Everything about the public API's shape follows from that one line. A caller
returns and inspects a report, so a batch check collects every problem in one
pass instead of dying on the first bad entry.


THE DESIGN: two functions, two audiences (option B)
--------------------------------------------------
The five copy-pasted guards this module replaces were load-bearing behavior,
not hygiene. They trip on bad *input data* — a charge whose net != amount - fee,
a refund whose net flips positive — and stripe-reconciler depended on the
raise: app.py:1007 caught the ValueError and diverted that one transaction to
the skipped/review list, so a single bad row never aborted a whole payout.

So this module serves two audiences with two different contracts:

  assert_balanced(entry)      internal. Raises on an unbalanced entry. Called
                              by every generator, replacing the five copies.
                              Fails fast on bad input, exactly as before.

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

assert_balanced is implemented in terms of check_entry rather than alongside
it. Two independent implementations of "does this balance" is how the guard
and the report end up disagreeing about the same entry — the single most
damaging bug this library could ship, given what it sells. One summing path,
two ways of delivering the answer.

CONSEQUENCE FOR STEP 4: entries_for must wrap each generator call in
try/except and route the failures to the review list. That is where the §4
skipped-list question gets answered.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import JournalEntry
from .money import format_amount

# The two ways an entry can fail its own check. Plain string constants rather
# than an Enum, matching the ACCOUNT_* constants in model.py — they survive a
# JSON round-trip without a serializer, which matters for a value a caller is
# expected to return and inspect.
DISCREPANCY_UNBALANCED = "unbalanced"
DISCREPANCY_EMPTY = "empty"


class UnbalancedEntry(ValueError):
    """Raised by assert_balanced when an entry does not balance.

    Subclasses ValueError deliberately. The shipped route catches ValueError
    (app.py:1007) and tests/test_entries.py asserts ValueError, so existing
    handlers keep working while callers who want to be specific can be.

    Carries the Discrepancy that caused it, so a caller who catches this does
    not have to re-derive the numbers to report them — the raise path and the
    report path describe a failure identically.
    """

    def __init__(self, discrepancy: Discrepancy):
        self.discrepancy = discrepancy
        super().__init__(discrepancy.describe())


@dataclass
class Discrepancy:
    """One thing that did not balance, named precisely enough to act on.

    §3 requires a failure to name *what* did not balance and *by how much*:
    the offending entry, the debit and credit totals, and their difference in
    integer cents.

    `difference` is stored rather than derived, and computed in exactly one
    place — the two constructors below. Deriving it via @property would keep
    it from ever disagreeing with the totals, but would also hide the number
    §3 promises from a repr() or a JSON dump. Storing it once is the trade.
    """
    source_id: str
    currency: str
    debits: int
    credits: int
    difference: int
    kind: str = DISCREPANCY_UNBALANCED

    @classmethod
    def unbalanced(cls, entry: JournalEntry, debits: int, credits: int) -> Discrepancy:
        return cls(
            source_id=entry.source_id,
            currency=entry.currency,
            debits=debits,
            credits=credits,
            difference=debits - credits,
            kind=DISCREPANCY_UNBALANCED,
        )

    @classmethod
    def empty(cls, entry: JournalEntry) -> Discrepancy:
        """An entry with no lines.

        It sums to zero and is therefore balanced in the arithmetic sense,
        which is exactly why it needs its own kind: reporting "balanced" for a
        record that books nothing is the silent plug this library exists to
        refuse. No generator can produce one; a caller constructing entries by
        hand can.
        """
        return cls(
            source_id=entry.source_id,
            currency=entry.currency,
            debits=0,
            credits=0,
            difference=0,
            kind=DISCREPANCY_EMPTY,
        )

    def describe(self) -> str:
        """One line naming what did not balance and by how much."""
        if self.kind == DISCREPANCY_EMPTY:
            return f"{self.source_id}: entry has no lines"
        return (
            f"{self.source_id}: "
            f"{format_amount(self.debits, self.currency)} debits != "
            f"{format_amount(self.credits, self.currency)} credits "
            f"(off by {format_amount(self.difference, self.currency)})"
        )


@dataclass
class CurrencyMismatch:
    """A journal that mixes currencies (§5).

    Not an error and not an exception — another kind of unbalanced. §5 is
    explicit that the library does not net across currencies and does not
    guess: a journal holding USD and EUR does not quietly sum to a meaningless
    number and report "balanced".

    Kept a separate type from Discrepancy rather than one type with optional
    fields: a mismatch has no single currency and its "difference" is not one
    number, so those fields would be lies half the time.

    `source_ids_by_currency` is the part that makes this actionable — a caller
    can see *where* the mixing came from, not just that it happened.
    """
    currencies: list[str]
    source_ids_by_currency: dict[str, list[str]]

    def describe(self) -> str:
        found = ", ".join(
            f"{c.upper()} ({len(self.source_ids_by_currency[c])})"
            for c in self.currencies
        )
        return (
            f"journal mixes {len(self.currencies)} currencies: {found}. "
            "Amounts in different currencies are not netted against each other."
        )


@dataclass
class BalanceReport:
    """The return value of every public balance check. Never raised, always
    returned.

    This type deliberately does not define __bool__. `__bool__ = balanced`
    reads beautifully (`if not report:`) and is a trap — `if report:` on a
    *failed* report is exactly the inverted check that passes review. Forcing
    the explicit `if report.balanced:` costs one word and cannot be misread.
    The library's pitch is making the unbalanced case visible; truthiness
    hides it. (A __bool__ that raises would catch the misuse loudly, but
    surprising a caller who writes `assert report is not None` is its own
    bug.)

    `entry_count` exists so emptiness is visible. An empty journal is balanced
    by vacuity, and that is also how a silent bug upstream — zero transactions
    fetched — would present as a clean bill of health. The report stays
    truthful about balance and lets summary() say how many entries earned that
    verdict.
    """
    balanced: bool
    entry_count: int
    discrepancies: list[Discrepancy] = field(default_factory=list)
    currency_mismatch: CurrencyMismatch | None = None

    def summary(self) -> str:
        """Human-readable. "Tells you what doesn't balance" should be one
        print() away."""
        if self.balanced:
            if self.entry_count == 0:
                return "Balanced: no entries checked."
            noun = "entry" if self.entry_count == 1 else "entries"
            return f"Balanced: {self.entry_count} {noun}, debits equal credits."

        problems = [d.describe() for d in self.discrepancies]
        if self.currency_mismatch is not None:
            problems.append(self.currency_mismatch.describe())

        noun = "entry" if self.entry_count == 1 else "entries"
        head = f"NOT balanced: {len(problems)} problem"
        if len(problems) != 1:
            head += "s"
        head += f" across {self.entry_count} {noun}."
        return "\n".join([head] + [f"  - {p}" for p in problems])

    def __str__(self) -> str:
        return self.summary()


def check_entry(entry: JournalEntry) -> BalanceReport:
    """Check one entry. Returns a report; never raises.

    A single JournalEntry carries one currency by construction (§2), so §5
    cannot bite here — every line already shares the entry's currency. This
    answers one question: do the debits equal the credits?
    """
    debits = sum(line["debit"] for line in entry.lines)
    credits = sum(line["credit"] for line in entry.lines)

    if not entry.lines:
        # Checked before the totals: an empty entry passes debits == credits
        # trivially, and reporting it as balanced would be the silent plug.
        discrepancies = [Discrepancy.empty(entry)]
    elif debits != credits:
        discrepancies = [Discrepancy.unbalanced(entry, debits, credits)]
    else:
        discrepancies = []

    return BalanceReport(
        balanced=not discrepancies,
        entry_count=1,
        discrepancies=discrepancies,
    )


def check_journal(entries: list[JournalEntry]) -> BalanceReport:
    """Check a whole journal in one pass. Returns a report; never raises.

    This is the function the pitch is about. It collects *every* problem
    rather than stopping at the first — a bookkeeper wants one list of what to
    fix, not one problem at a time across five runs.

    Two independent checks, both of which always run:
      1. Per entry: debits == credits (§3).
      2. Across entries: are they all in the same currency (§5)? If not, the
         report names the mismatch. It does not net them, and it does not
         raise.

    A batch can be cross-currency *and* contain an individually unbalanced
    entry; the currency pass never short-circuits the per-entry pass.

    An empty list is balanced by vacuity — see BalanceReport.entry_count for
    why that is reported rather than hidden.
    """
    discrepancies: list[Discrepancy] = []
    for entry in entries:
        discrepancies.extend(check_entry(entry).discrepancies)

    mismatch = _currency_mismatch(entries)

    return BalanceReport(
        balanced=not discrepancies and mismatch is None,
        entry_count=len(entries),
        discrepancies=discrepancies,
        currency_mismatch=mismatch,
    )


def _currency_mismatch(entries: list[JournalEntry]) -> CurrencyMismatch | None:
    """The §5 guard. None when every entry shares one currency.

    Order of discovery is preserved rather than sorted: the first currency
    listed is the one the journal started in, which is almost always the one
    the caller expected and the rest are the surprise.
    """
    by_currency: dict[str, list[str]] = {}
    for entry in entries:
        by_currency.setdefault(entry.currency, []).append(entry.source_id)

    if len(by_currency) <= 1:
        return None

    return CurrencyMismatch(
        currencies=list(by_currency),
        source_ids_by_currency=by_currency,
    )


def assert_balanced(entry: JournalEntry) -> None:
    """Raise if the entry does not balance. The generators' precondition.

    The internal half of the two-audience design at the top of this module,
    and the single replacement for the five copy-pasted guards. Not part of
    the public balance API — callers who want a verdict without an exception
    use check_entry.

    Delegates to check_entry so the guard and the report can never disagree
    about the same entry.
    """
    report = check_entry(entry)
    if not report.balanced:
        raise UnbalancedEntry(report.discrepancies[0])
