"""The money rule of DESIGN.md §1.

All monetary amounts in this library are integer cents. This module holds the
one sanctioned way to turn them into something a human reads.
"""
from __future__ import annotations


def format_cents(cents: int) -> str:
    """Integer cents to a '$1,234.56' string.

    Split with divmod rather than cents / 100: the division would land on a
    float and need rounding back to two places, and money never becomes a
    float anywhere else in this library. A negative amount signs the whole
    string ('-$5.00').

    §1 calls formatting a presentation concern but specifies how it must be
    done. This lives here so the one place a float could re-enter — the
    display boundary — is inside the library's guarantee rather than outside
    it. Callers are free to format their own way; this is the version that
    cannot silently become a rounding difference.

    Known limitation: the '$' is hardcoded, lifted from a codebase whose route
    rejected every non-USD payout up front. Now that `currency` is required on
    Transaction, this will happily print a EUR amount with a dollar sign. It
    takes no currency argument rather than guessing a symbol from one — the
    symbol table is a presentation decision v1 has not made. Flagged, not
    fixed.
    """
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"
