"""The money rule of DESIGN.md §1.

All monetary amounts in this library are integer minor units. This module holds
the one sanctioned way to turn them into something a human reads.


WHY A CURRENCY TABLE IS NOT THE §5 NON-GOAL
-------------------------------------------
This module used to say that fixing the minor-unit assumption "means a
per-currency exponent table, which is multi-currency support — explicitly out
of scope for v1 (§5)." That conflated two different things, and the table is
here now because they come apart cleanly.

What §5 defers is a change to what *balanced means*: per-currency sub-ledgers,
a report that verifies balance per currency across a mixed journal, and FX
modeling for a EUR payout settling from USD charges. None of that is affected
by knowing how many digits JPY prints. The balance check compares minor units
to minor units within one currency and never divides by the exponent — it was
already correct for JPY and stays untouched by this module.

So the exponent table buys correct *display* and changes no semantics. The v1
guard of §5 still stands exactly where it was: a journal mixing currencies is
reported as a mismatch, not netted.
"""
from __future__ import annotations

import warnings

# ISO 4217 exponents that are not 2. Everything absent from this table prints
# with two digits, which covers the large majority of codes.
#
# A dict rather than a set of "zero-decimal currencies", so the three-decimal
# dinars are the same kind of fact as the zero-decimal yen instead of a special
# case bolted on beside it.
#
# MGA and MRU are deliberately absent. Their minor unit is one fifth, not a
# power of ten, so no exponent describes them and this table would have to lie
# to include them. They fall through to 2 and are wrong in the same way they
# were before — recorded here rather than silently plugged.
_MINOR_UNIT_EXPONENTS = {
    # No minor unit at all.
    "bif": 0, "clp": 0, "djf": 0, "gnf": 0, "isk": 0, "jpy": 0, "kmf": 0,
    "krw": 0, "pyg": 0, "rwf": 0, "ugx": 0, "uyi": 0, "vnd": 0, "vuv": 0,
    "xaf": 0, "xof": 0, "xpf": 0,
    # Three digits.
    "bhd": 3, "iqd": 3, "jod": 3, "kwd": 3, "lyd": 3, "omr": 3, "tnd": 3,
    # Four digits. Units of account rather than spending money, but they are
    # ISO 4217 codes and a caller who hands one over deserves it rendered.
    "clf": 4, "uyw": 4,
}


def minor_unit_exponent(currency: str) -> int:
    """How many digits this currency's minor unit prints with. Defaults to 2.

    Public because the table is a fact about the world that callers formatting
    their own output need too, and a private copy of it in a host application
    is a copy that drifts.
    """
    return _MINOR_UNIT_EXPONENTS.get(currency.lower(), 2)


def format_amount(cents: int, currency: str) -> str:
    """Integer minor units plus an ISO 4217 code: '1,234.56 USD'.

    The currency-aware formatter, and what the balance report uses. It appends
    the code rather than guessing a symbol — a report whose job is naming a
    USD/EUR mismatch cannot render both sides with a dollar sign, and inventing
    a symbol table would be a presentation decision v1 has not made.

    The number of digits comes from the currency: 150 JPY is one hundred and
    fifty yen and prints as '150 JPY', while 1234567 KWD is '1,234.567 KWD'.
    Anything not in the table above prints with two.

    Split with divmod rather than dividing by the scale: the division would
    land on a float and need rounding back, and money never becomes a float
    anywhere in this library (§1). A float amount raises here rather than
    rendering as a plausible number, because the '0Nd' pad refuses one.

    `cents` keeps its name for continuity with §1's "integer cents throughout",
    but it means minor units — for a zero-decimal currency the whole amount is
    the minor unit.
    """
    exponent = minor_unit_exponent(currency)
    sign = "-" if cents < 0 else ""
    code = currency.upper()

    if exponent == 0:
        # No fractional part exists, so there is no separator to print. A
        # trailing '.00' here would invent precision the currency does not have.
        return f"{sign}{abs(cents):,} {code}"

    whole, frac = divmod(abs(cents), 10 ** exponent)
    return f"{sign}{whole:,}.{frac:0{exponent}d} {code}"


def format_cents(cents: int) -> str:
    """Deprecated since 0.1.1; removed in 0.2.0. Use format_amount instead.

    Integer cents to a '$1,234.56' string.

    The '$' is hardcoded, lifted from a codebase whose route rejected every
    non-USD payout up front. Now that `currency` is required on Transaction it
    is not safe: this will happily print a EUR amount with a dollar sign, which
    is the silent plug this library exists to refuse — in the one place a
    reader is most likely to believe it.

    Nothing in the library calls it. It is deprecated rather than deleted
    outright so that anyone who installed 0.1.0 gets a warning pointing at
    format_amount instead of an ImportError.
    """
    warnings.warn(
        "format_cents is deprecated and will be removed in 0.2.0; use "
        "format_amount(cents, currency), which renders the currency it was "
        "given instead of assuming dollars.",
        DeprecationWarning,
        stacklevel=2,
    )
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"
