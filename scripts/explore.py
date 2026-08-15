#!/usr/bin/env python3
"""Run real Stripe data through entries_for and report what needed a human.

This is a local exploration tool, not part of the published package — it lives
outside ledger_core/ and never reaches the wheel. Its whole job is to answer
one question with evidence instead of guesswork:

    On a real account, for a real month, what lands in Suspense — and how much
    of it is it worth teaching the library to book?

The library knows three transaction types. Stripe emits more than three. Every
unrecognized type is a line a human has to handle at close, so the breakdown
this prints is a backlog ranked by how much manual work each entry would remove.

Usage
-----
    pip install stripe                      # not a dependency of the library
    export STRIPE_API_KEY=rk_live_...       # a RESTRICTED, READ-ONLY key

    python scripts/explore.py --month 2026-07
    python scripts/explore.py --payout po_1234
    python scripts/explore.py --since 2026-07-01 --until 2026-08-01

    python scripts/explore.py --dump raw.json --month 2026-07   # save, then
    python scripts/explore.py --file raw.json                   # iterate offline

Use a restricted key with read access to balance transactions and nothing
else. This script only reads, but a key that can only read is the one you can
paste without thinking about it.

--payout answers "what is this deposit made of", which is the question a
bookkeeper asks about a bank line. --month answers "what happened in July",
which is the question they ask at close — and it is the better one for this
exercise, because payout-scoped data only contains types that settle into
payouts and so hides exactly the oddities most likely to reach Suspense.

Note on the payout itself: when a payout's own balance transaction appears in
the results it is booked by payout_entry, the same as any other line. The
`payouts=` argument of entries_for is for manual payouts, which are Payout
objects rather than balance transactions, and is deliberately not used here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger_core import (  # noqa: E402
    ACCOUNT_SUSPENSE,
    check_journal,
    entries_for,
    format_amount,
)
from ledger_core.adapters.stripe import stripe_to_transaction  # noqa: E402

# The fields stripe_to_transaction indexes. Kept here so a raw object saved by
# --dump carries exactly what a later --file run needs and nothing more: these
# are somebody's real transactions, and a dump that quietly included customer
# ids or descriptions would be a privacy problem sitting in a repo directory.
RAW_FIELDS = ("id", "amount", "fee", "net", "type", "created", "currency")


def month_bounds(month: str) -> tuple[int, int]:
    """'2026-07' to the epoch seconds bounding it, [start, next month).

    UTC, which is what Stripe's `created` filter uses. An account whose
    reporting timezone is not UTC will disagree with its own dashboard at the
    month boundary by a few hours of transactions — irrelevant for counting
    which types show up, and worth knowing before anyone reconciles a total
    against a dashboard figure.
    """
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    # Land in the middle of the following month, then snap to its first day.
    following = (start + timedelta(days=32)).replace(day=1)
    return int(start.timestamp()), int(following.timestamp())


def day_bounds(since: str, until: str) -> tuple[int, int]:
    fmt = "%Y-%m-%d"
    return (
        int(datetime.strptime(since, fmt).replace(tzinfo=timezone.utc).timestamp()),
        int(datetime.strptime(until, fmt).replace(tzinfo=timezone.utc).timestamp()),
    )


def fetch(args: argparse.Namespace) -> list[dict]:
    """Balance transactions from the API, as plain dicts.

    Plain dicts rather than Stripe objects so the rest of this script — and
    --dump — works the same whether the data came from the network or a file.
    """
    try:
        import stripe
    except ImportError:
        sys.exit("stripe is not installed. `pip install stripe` (it is not a "
                 "dependency of the library).")

    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        sys.exit("STRIPE_API_KEY is not set. Use a restricted, read-only key.")
    stripe.api_key = key

    if args.payout:
        query = {"payout": args.payout}
    elif args.month:
        gte, lt = month_bounds(args.month)
        query = {"created": {"gte": gte, "lt": lt}}
    else:
        gte, lt = day_bounds(args.since, args.until)
        query = {"created": {"gte": gte, "lt": lt}}

    listing = stripe.BalanceTransaction.list(limit=100, **query)
    return [{field: bt[field] for field in RAW_FIELDS}
            for bt in listing.auto_paging_iter()]


def load(path: str) -> list[dict]:
    return json.loads(Path(path).read_text())


def totals_by_currency(amounts: list[tuple[int, str]]) -> str:
    """Sum per currency and render each with its own minor unit.

    Never one total across currencies — that is the sum §5 exists to refuse,
    and it would be as wrong here as it is in a journal.
    """
    by_currency: dict[str, int] = defaultdict(int)
    for cents, currency in amounts:
        by_currency[currency] += cents
    return ", ".join(format_amount(v, c) for c, v in sorted(by_currency.items()))


def report(transactions: list) -> None:
    journal = entries_for(transactions)

    print(f"\n{len(transactions)} transactions in, {len(journal.entries)} entries booked, "
          f"{len(journal.review)} needing review.")

    # --- what the library already understands ---------------------------------
    print("\nTYPES SEEN")
    counts: dict[str, int] = defaultdict(int)
    for txn in transactions:
        counts[txn.type] += 1
    booked = {"charge", "refund", "payout"}
    for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        mark = "booked  " if kind in booked else "SUSPENSE"
        print(f"  {mark}  {count:>5}  {kind}")

    # --- the backlog, ranked --------------------------------------------------
    if journal.review:
        print("\nNEEDS A HUMAN, most common first")
        # A review item carries its own net but not its currency, so look that
        # up by source id rather than pairing the two lists positionally — the
        # review list is shorter than the batch and in its own order.
        currency_of = {txn.id: txn.currency for txn in transactions}

        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for item in journal.review:
            grouped[(item.reason, item.type)].append(item)

        for (reason, kind), items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            amounts = [(i.net, currency_of[i.source_id]) for i in items
                       if i.source_id in currency_of]
            total = totals_by_currency(amounts) if amounts else "—"
            print(f"  {len(items):>5}  {kind:<24} {reason:<16} net {total}")
            for item in items[:3]:
                print(f"         e.g. {item.describe()}")

    # --- does the batch tie out ----------------------------------------------
    print("\nBALANCE")
    currencies = {e.currency for e in journal.entries}
    if len(currencies) > 1:
        # Expected on a multi-currency account, and not a failure: §5 refuses to
        # net across currencies rather than reporting a meaningless total. Check
        # each currency's own journal instead.
        print(f"  {len(currencies)} currencies present — checking each separately (§5).")
        for currency in sorted(currencies):
            subset = [e for e in journal.entries if e.currency == currency]
            print(f"  {currency.upper():<5} {check_journal(subset).summary()}")
    else:
        print(f"  {check_journal(journal.entries).summary()}")

    suspense = [e for e in journal.entries
                if any(line["account"] == ACCOUNT_SUSPENSE for line in e.lines)]
    if suspense:
        print(f"\n  {len(suspense)} of {len(journal.entries)} entries sit in Suspense. "
              "Each one is a line somebody reclassifies by hand at close.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Stripe balance transactions through entries_for.",
        epilog="Use a restricted, read-only STRIPE_API_KEY.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payout", help="balance transactions behind one payout (po_...)")
    source.add_argument("--month", help="a calendar month, YYYY-MM, UTC")
    source.add_argument("--since", help="start date, YYYY-MM-DD (with --until)")
    source.add_argument("--file", help="a JSON array saved earlier by --dump")
    parser.add_argument("--until", help="end date, YYYY-MM-DD, exclusive")
    parser.add_argument("--dump", help="save the raw response here before reporting")
    args = parser.parse_args()

    if args.since and not args.until:
        parser.error("--since needs --until")

    raw = load(args.file) if args.file else fetch(args)

    if args.dump:
        Path(args.dump).write_text(json.dumps(raw, indent=2))
        print(f"wrote {len(raw)} transactions to {args.dump}")

    if not raw:
        sys.exit("No transactions matched. Nothing to report.")

    report([stripe_to_transaction(item) for item in raw])


if __name__ == "__main__":
    main()
