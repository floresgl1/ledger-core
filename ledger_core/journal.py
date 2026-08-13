"""Dispatch and policy: a list of transactions in, a journal out.

SKELETON. The design is settled; the logic is yours.

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


THE §4 DECISION, NOW SETTLED: the review list lives in the library (option B)
----------------------------------------------------------------------------
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
and this adds a concept to a library that is otherwise three models and two
functions. §7's "holds no state" is not violated — a return value is not
state — but the scope list is the contract, and this stretches it.


A DEFECT IN THE SHIPPED CODE, WHICH THIS MUST FIX
-------------------------------------------------
app.py:990-1014 has three cases, not two:

  - unrecognized type, net != 0  -> Suspense entry AND a skipped record. Fine.
  - unrecognized type, net == 0  -> skipped record, no entry. Fine, there is
                                    nothing to book.
  - generator RAISED             -> skipped record and NO ENTRY AT ALL.

That third case drops the transaction from the journal entirely, and the
payout it belonged to silently stops tying out. It is exactly what §4 forbids:
"never dropped, never silently defaulted, never guessed." Here, a transaction
whose generator raises routes to Suspense like any other unclassifiable
transaction, and carries its Discrepancy into the review list so the caller
knows it failed rather than merely being unrecognized.

The zero-net case above is kept as the one sanctioned absence: a zero-net
unknown has nothing to book, and balance.check_entry already treats a
lines-less entry as a discrepancy, so emitting an empty Suspense entry would
manufacture a problem rather than record one. It still reaches the caller as
a review item, so it is surfaced without being booked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import JournalEntry, Transaction

# Why a transaction needs a human. Plain string constants, matching the
# ACCOUNT_* and DISCREPANCY_* idiom rather than an Enum — they survive a JSON
# round-trip without a serializer.
#
# TODO: confirm these two are the whole set. A third — "booked to Suspense but
# nothing to book", the zero-net case — may deserve its own reason rather than
# sharing REVIEW_UNKNOWN_TYPE, since the caller can act on it differently
# (there is nothing to reclassify).
REVIEW_UNKNOWN_TYPE = "unknown_type"
REVIEW_FAILED_GUARD = "failed_guard"


@dataclass
class ReviewItem:
    """One transaction that needs a human, and why.

    TODO: fields. The working set, from the shipped skipped-list shape plus
    the reason it was missing:
        source_id: str      the transaction id
        type: str           its processor type, e.g. "adjustment"
        net: int            integer cents
        created: int        epoch seconds
        reason: str         REVIEW_UNKNOWN_TYPE | REVIEW_FAILED_GUARD

    TODO — my recommendation is yes: carry the Discrepancy when the reason is
    a failed guard. UnbalancedEntry already holds one naming both totals and
    the difference, so the information is free at the catch site and
    irrecoverable afterwards. It is the difference between "this needs review"
    and "this needs review because its debits are 100 cents short" — and the
    second one is the product. Optional field, None for unknown types.

    TODO: a describe() like Discrepancy.describe(), so a review list prints as
    readably as a balance report. Use money.format_amount for any cents (§1).
    """


@dataclass
class Journal:
    """The result of dispatching a batch: what was booked, and what needs eyes.

    TODO: fields.
        entries: list[JournalEntry]
        review: list[ReviewItem]

    TODO: decide whether this carries a convenience `check()` delegating to
    balance.check_journal(self.entries). It makes the happy path one line; it
    also puts a second door on a balance API that so far has exactly one, and
    two doors are how two behaviors eventually diverge. Leaning no — the
    caller writing check_journal(journal.entries) is one extra token and keeps
    the balance check in one place.
    """


def entries_for(transactions: list[Transaction]) -> "Journal":
    """Book every transaction in the batch. Never raises.

    The contract:

      - Every transaction is accounted for. Unknown types route to Suspense
        (§4); transactions whose generator raises UnbalancedEntry route to
        Suspense as well, rather than vanishing the way the shipped route
        dropped them. The single exception is a zero-net unknown, which has
        nothing to book and appears in the review list only.

      - One bad transaction never aborts the batch. Each dispatch is wrapped,
        exactly as app.py:1007 wrapped it.

      - No I/O, no logging, no persistence (§7). The shipped route logged
        every failure; here the caller does that with what it gets back.

      - The batch may mix currencies and this function does not care. Each
        entry inherits its transaction's currency (§2), and check_journal
        reports the mismatch (§5). Rejecting mixed batches here would move the
        currency guard out of the balance report, where §5 puts it.

    TODO: the dispatch — charge / refund / payout / else-Suspense, per the
    table in the module docstring. A dict of {type: generator} reads better
    than the shipped if/elif chain and makes the known set inspectable, but
    the else-branch is not a lookup miss so it cannot be a pure dict.

    TODO: the try/except. Catch UnbalancedEntry specifically rather than bare
    Exception — the shipped route caught everything, which meant a typo in the
    generator looked identical to bad Stripe data. A KeyError from a malformed
    Transaction is a bug in the adapter and should not be quietly filed as
    "needs review".

    TODO: what happens when suspense_entry ITSELF raises. It is the fallback,
    so it has no fallback of its own. It is hard to make it fail — two lines
    from a single abs(net) — but "hard to fail" is not a contract. Decide: let
    it propagate (breaking "never raises"), or catch it and emit a review item
    with no entry, which is the one case where dropping is unavoidable and so
    must be loud.

    TODO: whether a zero-net *known* type (a 0-amount 0-fee charge) is worth
    the same treatment. The shipped guard sat only in the else-branch, so a
    zero charge books a 0/0/0 entry that balances fine. Probably correct to
    leave alone — it is a real charge that happens to be zero — but it is
    worth deciding rather than inheriting.
    """
    raise NotImplementedError("skeleton — see TODOs above")
