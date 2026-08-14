"""Tests for the display boundary — DESIGN.md §1.

    All monetary amounts are integer cents. No float appears in any money
    field, intermediate, or return value anywhere in the library. Formatting
    to a decimal string for display is a presentation concern and uses integer
    divmod, never float division.

Formatting is the one place a float could re-enter a library that otherwise
never sees one, which is why these two functions live inside the guarantee
instead of being left to callers. The tests that matter here are the ones that
would catch `cents / 100` being substituted for the divmod: an implementation
that divides passes every small-value case and only breaks past 2**53, so the
large-value round-trips below are the real check and the tidy cases are the
readable ones.

Both functions carry documented limitations. Those are pinned as tests too,
clearly labelled, so the behavior is characterized rather than accidental and
so the v2 fix has a failing test waiting for it.
"""
import pytest

from ledger_core import format_amount, format_cents


def to_cents(rendered: str) -> int:
    """Read a rendered amount back to integer cents.

    Deliberately naive — it undoes exactly what the two formatters do and
    nothing more, so a round-trip through it proves the rendering lost
    nothing. Integer arithmetic only, for the same reason the formatters use
    it: a parser that went through float would hide the very defect it is here
    to catch.
    """
    negative = rendered.startswith("-")
    body = rendered.lstrip("-").lstrip("$").split(" ")[0].replace(",", "")
    whole, frac = body.split(".")
    cents = int(whole) * 100 + int(frac)
    return -cents if negative else cents


# Every shape that has ever broken a money formatter: zero, the sub-unit range
# where the pad matters, the carry at 100, the separator boundaries, and two
# values past 2**53 where a float division stops being exact.
VALUES = (
    0,
    1,
    5,
    50,
    99,
    100,
    101,
    999,
    1000,
    123456789,
    10**12,
    2**53,
    2**53 + 1,
)


# --- format_cents ------------------------------------------------------------

@pytest.mark.parametrize("cents,expected", [
    (0, "$0.00"),
    (5, "$0.05"),
    (50, "$0.50"),
    (99, "$0.99"),
    (100, "$1.00"),
    (101, "$1.01"),
    (1000, "$10.00"),
    (123456, "$1,234.56"),
    (123456789, "$1,234,567.89"),
])
def test_cents_render_as_dollars_and_cents(cents, expected):
    assert format_cents(cents) == expected


@pytest.mark.parametrize("cents,expected", [
    (-5, "-$0.05"),
    (-500, "-$5.00"),
    (-123456, "-$1,234.56"),
])
def test_a_negative_amount_signs_the_whole_string(cents, expected):
    """'-$5.00', not '$-5.00'. The sign belongs in front of the amount as a
    reader expects it, which is why it is built rather than left to the
    format spec."""
    assert format_cents(cents) == expected


def test_the_minor_unit_is_always_two_digits():
    """The pad is the difference between '$0.05' and '$0.5' — the second is a
    fifty-cent amount to anyone skimming a report."""
    assert format_cents(5).endswith(".05")
    assert format_cents(-5).endswith(".05")


# --- format_amount -----------------------------------------------------------

@pytest.mark.parametrize("cents,currency,expected", [
    (0, "usd", "0.00 USD"),
    (150, "usd", "1.50 USD"),
    (123456, "eur", "1,234.56 EUR"),
    (-123456, "eur", "-1,234.56 EUR"),
])
def test_amounts_render_with_their_currency_code(cents, currency, expected):
    assert format_amount(cents, currency) == expected


def test_the_code_is_appended_never_translated_to_a_symbol():
    """§5's guard is a report naming a USD/EUR mismatch. It cannot render both
    sides with a dollar sign, and a symbol table is a presentation decision v1
    has not made — so no output of this function contains a symbol at all."""
    for currency in ("usd", "eur", "gbp", "jpy"):
        assert "$" not in format_amount(1000, currency)
        assert format_amount(1000, currency).endswith(currency.upper())


def test_the_currency_code_is_uppercased():
    """Transactions carry lowercase ISO codes ('usd', §2); reports show the
    canonical uppercase form. Both spellings must render identically or the
    same journal reads two ways."""
    assert format_amount(1000, "usd") == format_amount(1000, "USD")
    assert format_amount(1000, "usd") == "10.00 USD"


# --- §1: money never becomes a float -----------------------------------------

@pytest.mark.parametrize("cents", VALUES, ids=[str(v) for v in VALUES])
@pytest.mark.parametrize("sign", (1, -1), ids=("positive", "negative"))
def test_format_cents_round_trips_exactly(cents, sign):
    """The divmod discipline of §1, stated as a property.

    2**53 + 1 is the case with teeth: `cents / 100` is a float, floats hold
    integers exactly only up to 2**53, and past that the division silently
    lands on a neighbouring value. Rendering it and reading it back has to
    return the same integer, or a cent was invented somewhere.
    """
    value = sign * cents
    assert to_cents(format_cents(value)) == value


@pytest.mark.parametrize("cents", VALUES, ids=[str(v) for v in VALUES])
@pytest.mark.parametrize("sign", (1, -1), ids=("positive", "negative"))
def test_format_amount_round_trips_exactly(cents, sign):
    value = sign * cents
    assert to_cents(format_amount(value, "usd")) == value


@pytest.mark.parametrize("amount", (1000.5, 1000.0), ids=("fractional", "integral"))
def test_a_float_amount_is_rejected_rather_than_rendered(amount):
    """§1: a float in a money field is a defect, not a rounding difference.

    Neither formatter launders one into something that looks like currency.
    divmod on a float returns floats, and the '02d' pad on the minor unit
    refuses them — so a float raises at the display boundary instead of
    printing a plausible amount that no longer ties to the ledger.

    The integral case (1000.0) is the one worth pinning: it is the float most
    likely to arrive from an upstream division and the one that would be
    easiest to render as if it were fine.
    """
    with pytest.raises(ValueError):
        format_cents(amount)
    with pytest.raises(ValueError):
        format_amount(amount, "usd")


# --- known limitations, pinned so they cannot drift silently -----------------

def test_format_cents_hardcodes_a_dollar_sign_on_any_currency():
    """KNOWN LIMITATION (money.py, flagged not fixed).

    format_cents was lifted from a codebase whose route rejected every non-USD
    payout up front, so the '$' was always right. Now that `currency` is
    required on Transaction it is not, and this function takes no currency
    argument rather than guessing a symbol from one.

    This is why format_amount exists and why it — not this — is what the
    balance report uses. Pinned so the mismatch is a recorded limitation
    rather than something rediscovered from a customer's EUR report.
    """
    eur_amount_in_cents = 123456
    assert format_cents(eur_amount_in_cents) == "$1,234.56"
    assert format_amount(eur_amount_in_cents, "eur") == "1,234.56 EUR"


def test_format_amount_assumes_a_two_decimal_minor_unit():
    """KNOWN LIMITATION (money.py, deferred to v2 by §5).

    JPY has no minor unit: 150 JPY is one hundred and fifty yen, and this
    renders it as '1.50 JPY'. Fixing it means a per-currency exponent table,
    which is multi-currency support — explicitly out of scope for v1.

    Scoped deliberately: this is a *display* limitation. The balance check
    compares cents to cents within one currency and is exponent-agnostic, so a
    JPY journal still balances correctly and still reports correctly on
    everything except the rendering of its numbers. The assertion below is
    what today does, not what is right; v2 flips it.
    """
    assert format_amount(150, "jpy") == "1.50 JPY"
