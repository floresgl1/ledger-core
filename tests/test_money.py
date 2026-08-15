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

from ledger_core import format_amount, format_cents, minor_unit_exponent

# format_cents is deprecated (0.1.1, removed in 0.2.0). Its behavior is still
# under test until it goes, so the warning is silenced for the tests that
# exercise it and asserted in the one test that is about the deprecation.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


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


# --- format_cents, on its way out --------------------------------------------

def test_format_cents_hardcodes_a_dollar_sign_on_any_currency():
    """Why format_cents is deprecated, in one assertion.

    It was lifted from a codebase whose route rejected every non-USD payout up
    front, so the '$' was always right. Now that `currency` is required on
    Transaction it is not: the same integer renders as dollars here and as
    what it actually is through format_amount.
    """
    eur_amount_in_cents = 123456
    assert format_cents(eur_amount_in_cents) == "$1,234.56"
    assert format_amount(eur_amount_in_cents, "eur") == "1,234.56 EUR"


@pytest.mark.filterwarnings("default::DeprecationWarning")
def test_format_cents_warns_that_it_is_going_away():
    """Deprecated in 0.1.1, removed in 0.2.0. Anyone who installed 0.1.0 gets
    a pointer to the replacement rather than a surprise ImportError later."""
    with pytest.warns(DeprecationWarning, match="format_amount"):
        format_cents(100)


# --- the minor unit comes from the currency ----------------------------------

@pytest.mark.parametrize("currency,expected", [
    ("usd", 2), ("eur", 2), ("gbp", 2),
    ("jpy", 0), ("krw", 0), ("vnd", 0), ("isk", 0),
    ("kwd", 3), ("bhd", 3), ("tnd", 3),
    ("clf", 4),
    ("zzz", 2),  # unknown codes fall through to the common case
])
def test_the_exponent_table_knows_the_odd_currencies(currency, expected):
    assert minor_unit_exponent(currency) == expected
    assert minor_unit_exponent(currency.upper()) == expected


def test_a_zero_decimal_currency_prints_no_fractional_part():
    """150 JPY is one hundred and fifty yen, not one yen fifty.

    Before 0.1.1 this rendered '1.50 JPY' — a hundredfold error in the one
    place a reader would not think to check. There is no separator at all: a
    trailing '.00' would invent precision the currency does not have.
    """
    assert format_amount(150, "jpy") == "150 JPY"
    assert format_amount(1234567, "krw") == "1,234,567 KRW"
    assert format_amount(-150, "jpy") == "-150 JPY"
    assert "." not in format_amount(150, "jpy")


def test_a_three_decimal_currency_prints_three_digits():
    assert format_amount(1234567, "kwd") == "1,234.567 KWD"
    assert format_amount(5, "bhd") == "0.005 BHD"
    assert format_amount(-1234567, "kwd") == "-1,234.567 KWD"


def test_a_four_decimal_currency_prints_four_digits():
    assert format_amount(12345678, "clf") == "1,234.5678 CLF"


def test_the_common_case_is_unchanged():
    """The table must not have moved the currencies that were already right."""
    assert format_amount(123456, "usd") == "1,234.56 USD"
    assert format_amount(123456, "eur") == "1,234.56 EUR"
    assert format_amount(0, "gbp") == "0.00 GBP"


def test_the_balance_check_never_needed_the_exponent():
    """DESIGN.md §5's non-goal is untouched by this.

    The check compares minor units to minor units within one currency and
    never divides by the exponent, so a JPY journal balanced correctly before
    the table existed — only the rendering was wrong. Asserted here so a
    future change that makes the check exponent-aware has to argue with a test.
    """
    from ledger_core import JournalEntry, check_entry

    entry = JournalEntry(
        source_id="txn_jpy", created=1751000000, currency="jpy",
        lines=[{"account": "Cash", "debit": 150, "credit": 0},
               {"account": "Revenue", "debit": 0, "credit": 150}],
    )
    assert check_entry(entry).balanced

    off_by_one = JournalEntry(
        source_id="txn_jpy_bad", created=1751000000, currency="jpy",
        lines=[{"account": "Cash", "debit": 150, "credit": 0},
               {"account": "Revenue", "debit": 0, "credit": 149}],
    )
    report = check_entry(off_by_one)
    assert not report.balanced
    assert "1 JPY" in report.summary(), "the discrepancy renders in whole yen"


# --- known limitations, pinned so they cannot drift silently -----------------

def test_the_base_five_currencies_are_not_covered():
    """KNOWN LIMITATION (money.py).

    MGA and MRU divide into five, not a power of ten, so no exponent describes
    them and the table would have to lie to include them. They fall through to
    two digits and are wrong in the same way they were before 0.1.1.

    Pinned rather than hidden: this is the one currency shape the table does
    not fix, and it should be a deliberate discovery rather than a surprise.
    """
    assert minor_unit_exponent("mga") == 2
    assert format_amount(150, "mga") == "1.50 MGA"
