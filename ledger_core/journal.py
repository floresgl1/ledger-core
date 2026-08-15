"""Dispatch and policy: a list of transactions in, a journal out.

This is the only genuinely new logic in the extraction. In stripe-reconciler
it did not exist as a function — it was inlined in the payout_journal route
(app.py:958-1014), tangled with Stripe pagination, a DB write, and Flask
response shaping. Lifting it means separating the *policy* (which generator
books which transaction type, and what happens to the ones that fit none of
them) from the host concerns around it (§7).

The policy, read off the shipped route:

    charge   -> charge_entry
    refund   -> refund_entry
    payout   -> payout_entry           booked straight through; a payout is
                                       the least ambiguous line in a journal
    anything -> suspense_entry         explicit Suspense, never dropped (§4)
    else                               ...and also recorded for human review


THE §4 DECISION: the review list lives in the library
----------------------------------------------------
DESIGN.md §4 deferred this deliberately: "v1 must decide whether that
review-list behavior lives inside the library or is handed to the caller."

It lives here. `entries_for` returns a `Journal` carrying both the entries and
a review list, and each review item names *why* it is there.

The reasoning: §4 says "a transaction landing there is a signal to a human."
That is only true if the human can tell what the signal means. "I have no rule
for this adjustment" and "I have a rule for this charge and its numbers are
corrupt" are different problems requiring different actions, and a Suspense
account alone cannot distinguish them. Handing the caller an undifferentiated
pile of Suspense entries technically satisfies "never dropped" while losing
the information that makes the routing useful.

The cost, recorded honestly: §6's scope list does not mention a review list,
and this adds a concept to a library that is otherwise three models and a
handful of functions. §7's "holds no state" is not violated — a return value
is not state — but the scope list is the contract, and this stretches it.


A DEFECT IN THE SHIPPED CODE, FIXED HERE
----------------------------------------
app.py:990-1014 had three cases, not two:

  - unrecognized type, net != 0  -> Suspense entry AND a skipped record. Fine.
  - unrecognized type, net == 0  -> skipped record, no entry. Fine, there is
                                    nothing to book.
  - generator RAISED             -> skipped record and NO ENTRY AT ALL.

That third case dropped the transaction from the journal entirely, and the
payout it belonged to silently stopped tying out. It is exactly what §4
forbids: "never dropped, never silently defaulted, never guessed." Here, a
transaction whose generator raises routes to Suspense like any other
unclassifiable transaction, and carries its Discrepancy into the review list
so the caller knows it failed rather than merely being unrecognized.

The zero-net case is kept as the one sanctioned absence: there is nothing to
book, and balance.check_entry treats a lines-less entry as a discrepancy, so
emitting an empty Suspense entry would manufacture a problem rather than
record one. It still reaches the caller as a review item.


MANUAL PAYOUTS: WHY THEY ARE AN ARGUMENT HERE AND NOT A SECOND FUNCTION
----------------------------------------------------------------------
`manual_payout_entry` takes a `Payout`, not a `Transaction`, so it cannot be
reached through the type dispatch above. That left callers with manual payouts
calling the generator themselves and concatenating the two lists by hand —
which is where the double-booking lives.

An automatic payout arrives twice in Stripe data: as a balance transaction of
type "payout" (dispatched above) and as a Payout object. A caller merging both
lists books the same cash movement twice, the journal still balances — both
entries balance individually — and the ledger is silently wrong by the payout
amount. That is the precise failure this library exists to make impossible,
and a caller doing the merge by hand cannot see it.

So `entries_for` takes an optional `payouts` argument and does the merge
itself, which is the only place the duplicate is detectable: a payout's entry
takes its source id from `payout.balance_transaction`, and that is exactly the
id the balance-transaction form was booked under. Same id, same cash, already
booked. The second one is refused and recorded for review rather than dropped
silently or booked twice.

The cost, recorded like the one above: this widens `entries_for`'s signature
and adds a third review reason, in a library whose scope list (§6) does not
mention payouts as a separate input. The argument defaults to empty, so every
existing call is unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .balance import Discrepancy, UnbalancedEntry
from .entries import (
    charge_entry,
    manual_payout_entry,
    payout_entry,
    refund_entry,
    suspense_entry,
)
from .model import JournalEntry, Payout, Transaction

# Why a transaction needs a human. Plain string constants, matching the
# ACCOUNT_* and DISCREPANCY_* idiom rather than an Enum — they survive a JSON
# round-trip without a serializer.
#
# "Nothing to book" and "Suspense itself failed" are deliberately not in here.
# They are *outcomes*, not reasons, and live on `booked_to_suspense` instead:
# folding them in would mean a zero-net charge with corrupt numbers had to
# choose between reporting that its data is broken and reporting that nothing
# was booked, when both are true and the caller wants both.
REVIEW_UNKNOWN_TYPE = "unknown_type"
REVIEW_FAILED_GUARD = "failed_guard"
# A payout already booked from the transaction list. Not an error and not bad
# data — the caller handed over both forms of the same payout, which is what
# Stripe returns — but the second one must not become a second entry.
REVIEW_ALREADY_BOOKED = "already_booked"

# The known transaction shapes. A dict rather than the shipped if/elif chain
# so the set of things this library claims to understand is inspectable — and
# so adding one is a line of data, not a branch. The else-case is deliberately
# not in here: routing to Suspense is not a lookup hit, it is what happens
# when the lookup misses.
_GENERATORS = {
    "charge": charge_entry,
    "refund": refund_entry,
    "payout": payout_entry,
}


@dataclass
class ReviewItem:
    """One transaction that needs a human, and why.

    Carries the shipped skipped-list shape (id, type, net, created) plus the
    two things it was missing.

    `reason` says what went wrong. `discrepancy` is populated when the reason
    is a failed guard: UnbalancedEntry already holds one naming both totals
    and the difference, so the information is free at the catch site and
    irrecoverable afterwards. It is the difference between "this needs review"
    and "this needs review because its debits are 100 cents short" — and the
    second one is the product.

    `booked_to_suspense` says whether the transaction made it into the journal
    anyway. For a transaction, False has exactly two causes, distinguished by
    `net`:

      - net == 0: nothing to book. The sanctioned absence.
      - net != 0: the Suspense fallback itself failed, which is the one case
        where a transaction is genuinely absent from the journal. It has no
        fallback of its own, so this must be loud rather than silent.

    A payout is never routed to Suspense — see `for_payout` — so the field is
    always False on items built from one, and `reason` is what distinguishes a
    refused duplicate (booked once already, correctly) from a payout that
    failed its own check (absent, and a problem).
    """
    source_id: str
    type: str
    net: int
    created: int
    reason: str
    booked_to_suspense: bool
    discrepancy: Discrepancy | None = None

    @classmethod
    def for_transaction(
        cls,
        txn: Transaction,
        reason: str,
        booked_to_suspense: bool,
        discrepancy: Discrepancy | None = None,
    ) -> ReviewItem:
        return cls(
            source_id=txn.id,
            type=txn.type,
            net=txn.net,
            created=txn.created,
            reason=reason,
            booked_to_suspense=booked_to_suspense,
            discrepancy=discrepancy,
        )

    @classmethod
    def for_payout(
        cls,
        payout: Payout,
        reason: str,
        discrepancy: Discrepancy | None = None,
    ) -> ReviewItem:
        """A payout that needs a human.

        `type` is filled in as "payout" — a Payout carries no type field
        because being one is the whole of its type — so a caller can read the
        review list without caring which input a given item came from.

        `net` is negative: a payout takes cash out of the Stripe balance, and
        the balance-transaction form of the same payout carries a negative net
        for the same reason. The two forms describe one movement and must not
        disagree about its direction.

        There is no `booked_to_suspense` parameter. Suspense is where an
        unclassifiable *transaction* goes; a payout that reaches this list was
        either already booked or failed its own check, and neither is a
        routing decision.
        """
        return cls(
            source_id=payout.balance_transaction or payout.id,
            type="payout",
            net=-abs(payout.amount),
            created=payout.created,
            reason=reason,
            booked_to_suspense=False,
            discrepancy=discrepancy,
        )

    def describe(self) -> str:
        """One line, so a review list prints as readably as a balance report."""
        if self.reason == REVIEW_FAILED_GUARD:
            what = "failed its balance check"
        elif self.reason == REVIEW_ALREADY_BOOKED:
            what = "already booked from the transaction list"
        else:
            what = f"unrecognized type {self.type!r}"

        if self.booked_to_suspense:
            where = "routed to Suspense"
        elif self.reason == REVIEW_ALREADY_BOOKED:
            # Checked before the net cases: this one was refused deliberately,
            # and reporting a correct refusal as a failure would send someone
            # looking for a bug that is not there.
            where = "not booked twice"
        elif self.net == 0:
            where = "nothing to book"
        else:
            where = "NOT BOOKED — nothing left to fall back to"

        line = f"{self.source_id}: {what}, {where}"
        if self.discrepancy is not None:
            line += f" ({self.discrepancy.describe()})"
        return line


@dataclass
class Journal:
    """The result of dispatching a batch: what was booked, and what needs eyes.

    Deliberately has no `check()` convenience delegating to
    balance.check_journal. It would make the happy path one line; it would also
    put a second door on a balance API that has exactly one, and two doors are
    how two behaviors eventually diverge. `check_journal(journal.entries)` is
    one extra token and keeps the balance check in one place.
    """
    entries: list[JournalEntry] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable, matching BalanceReport.summary in shape."""
        noun = "entry" if len(self.entries) == 1 else "entries"
        head = f"{len(self.entries)} {noun} booked"
        if not self.review:
            return f"{head}, nothing needs review."

        # "items", not "transactions": the review list holds payouts too now,
        # and the verb agrees with the count — a report that says "1
        # transaction need review" undercuts every number next to it.
        item = "item needs" if len(self.review) == 1 else "items need"
        head += f", {len(self.review)} {item} review:"
        return "\n".join([head] + [f"  - {r.describe()}" for r in self.review])

    def __str__(self) -> str:
        return self.summary()


def entries_for(
    transactions: list[Transaction],
    payouts: list[Payout] | None = None,
) -> Journal:
    """Book every transaction in the batch, and any manual payouts alongside.

    The contract:

      - Every transaction is accounted for. Unknown types route to Suspense
        (§4); transactions whose generator raises UnbalancedEntry route to
        Suspense as well, rather than vanishing the way the shipped route
        dropped them. The single exception is a zero-net transaction, which
        has nothing to book and appears in the review list only.

      - Every payout in `payouts` is booked with manual_payout_entry, unless
        the transaction list already booked it — see the double-booking note
        at the top of this module. A refused duplicate is recorded for review,
        never silently discarded, because "your two inputs overlap" is
        something the caller needs to know about their own data.

      - One bad transaction never aborts the batch, exactly as app.py:1007
        ensured.

      - No I/O, no logging, no persistence (§7). The shipped route logged every
        failure; here the caller does that with what it gets back.

      - The batch may mix currencies and this function does not care. Each
        entry inherits its transaction's currency (§2), and check_journal
        reports the mismatch (§5). Rejecting mixed batches here would move the
        currency guard out of the balance report, where §5 puts it.

    On raising: only UnbalancedEntry is caught, not bare Exception. The shipped
    route caught everything, which meant a typo in a generator looked exactly
    like bad Stripe data and was quietly filed as "needs review". A TypeError
    from a malformed Transaction is an adapter bug, and it stays loud. So this
    function does not raise on any Transaction the models accept — which is the
    honest version of "never raises".
    """
    entries: list[JournalEntry] = []
    review: list[ReviewItem] = []

    for txn in transactions:
        generator = _GENERATORS.get(txn.type)

        if generator is not None:
            try:
                entries.append(generator(txn))
                continue
            except UnbalancedEntry as failure:
                # A rule existed and the data broke it. Falls through to
                # Suspense below rather than being dropped.
                reason: str = REVIEW_FAILED_GUARD
                discrepancy: Discrepancy | None = failure.discrepancy
        else:
            reason, discrepancy = REVIEW_UNKNOWN_TYPE, None

        if txn.net == 0:
            # Nothing to book. A 0/0 Suspense entry would be noise, and an
            # empty one is a discrepancy by check_entry's reckoning — either
            # way it would manufacture a problem instead of recording one.
            review.append(ReviewItem.for_transaction(
                txn, reason, booked_to_suspense=False, discrepancy=discrepancy,
            ))
            continue

        try:
            entries.append(suspense_entry(txn))
            booked = True
        except UnbalancedEntry as failure:
            # The fallback has no fallback. Currently unreachable —
            # suspense_entry builds both its lines from one abs(net) — but
            # "hard to fail" is not a contract, and a transaction that reaches
            # here is genuinely absent from the journal. The more proximate
            # failure replaces the original discrepancy.
            booked = False
            discrepancy = failure.discrepancy

        review.append(ReviewItem.for_transaction(
            txn, reason, booked_to_suspense=booked, discrepancy=discrepancy,
        ))

    # Payouts come second so the transaction list decides what is already
    # booked. Doing it the other way round would make the outcome depend on
    # argument order, and "which of my two inputs won" is not something a
    # bookkeeper should have to reason about.
    booked_ids = {entry.source_id for entry in entries}

    for payout in payouts or ():
        source_id = payout.balance_transaction or payout.id

        if source_id in booked_ids:
            review.append(ReviewItem.for_payout(payout, REVIEW_ALREADY_BOOKED))
            continue

        try:
            entries.append(manual_payout_entry(payout))
            booked_ids.add(source_id)
        except UnbalancedEntry as failure:
            # As unreachable as the Suspense fallback above — both lines come
            # from one amount — and recorded for the same reason: a payout
            # that lands here is absent from the journal, and there is no
            # Suspense leg to catch it, because Suspense is for transactions
            # that need reclassifying rather than cash that failed to book.
            review.append(ReviewItem.for_payout(
                payout, REVIEW_FAILED_GUARD, discrepancy=failure.discrepancy,
            ))

    return Journal(entries=entries, review=review)
