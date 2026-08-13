"""Tests for the balance check — DESIGN.md §3 and §5.

The contract under test is one sentence: the balance check never raises, it
always returns a report. The tests that matter most here are the ones that
would still pass if the module quietly started netting across currencies or
stopping at the first bad entry, so they are written to fail loudly if it
does.
"""
import pytest

from ledger_core import (
    ACCOUNT_SALES_REVENUE,
    ACCOUNT_STRIPE_BANK,
    DISCREPANCY_EMPTY,
    DISCREPANCY_UNBALANCED,
    JournalEntry,
    Transaction,
    UnbalancedEntry,
    assert_balanced,
    charge_entry,
    check_entry,
    check_journal,
)


def entry(source_id="txn_1", currency="usd", debit=1000, credit=1000):
    """A hand-built entry, so the unbalanced cases the generators refuse to
    produce can still be checked."""
    return JournalEntry(
        source_id=source_id,
        created=1751000000,
        currency=currency,
        lines=[
            {"account": ACCOUNT_STRIPE_BANK, "debit": debit, "credit": 0},
            {"account": ACCOUNT_SALES_REVENUE, "debit": 0, "credit": credit},
        ],
    )


def empty_entry(source_id="txn_empty", currency="usd"):
    return JournalEntry(source_id=source_id, created=1751000000,
                        currency=currency, lines=[])


# --- check_entry -------------------------------------------------------------

def test_balanced_entry_reports_balanced():
    report = check_entry(entry())
    assert report.balanced
    assert report.discrepancies == []
    assert report.entry_count == 1
    assert report.currency_mismatch is None


def test_unbalanced_entry_names_what_and_by_how_much():
    # §3: the report names the offending entry, both totals, and the
    # difference in integer cents.
    report = check_entry(entry(source_id="txn_bad", debit=1000, credit=900))
    assert not report.balanced

    (d,) = report.discrepancies
    assert d.source_id == "txn_bad"
    assert d.currency == "usd"
    assert d.debits == 1000
    assert d.credits == 900
    assert d.difference == 100
    assert d.kind == DISCREPANCY_UNBALANCED


def test_difference_is_signed():
    (d,) = check_entry(entry(debit=900, credit=1000)).discrepancies
    assert d.difference == -100


def test_empty_entry_is_not_quietly_balanced():
    # It sums to zero, which is exactly why it needs its own kind: reporting
    # "balanced" for a record that books nothing is the silent plug the
    # library exists to refuse.
    report = check_entry(empty_entry())
    assert not report.balanced

    (d,) = report.discrepancies
    assert d.kind == DISCREPANCY_EMPTY
    assert d.source_id == "txn_empty"


def test_check_entry_never_raises():
    # The whole contract. Both of these would raise under the old guards.
    assert check_entry(entry(debit=1, credit=999_999_999)).balanced is False
    assert check_entry(empty_entry()).balanced is False


# --- check_journal -----------------------------------------------------------

def test_balanced_journal_reports_balanced():
    report = check_journal([entry("a"), entry("b"), entry("c")])
    assert report.balanced
    assert report.entry_count == 3
    assert report.discrepancies == []


def test_journal_collects_every_problem_not_just_the_first():
    # §3: a caller checks an entire batch and collects every problem in one
    # pass rather than dying on the first bad entry.
    report = check_journal([
        entry("bad_1", debit=1000, credit=900),
        entry("good"),
        entry("bad_2", debit=500, credit=400),
        empty_entry("bad_3"),
    ])
    assert not report.balanced
    assert [d.source_id for d in report.discrepancies] == ["bad_1", "bad_2", "bad_3"]
    assert report.entry_count == 4


def test_empty_journal_is_balanced_but_visibly_empty():
    report = check_journal([])
    assert report.balanced
    assert report.entry_count == 0
    assert "no entries" in report.summary()


# --- §5, the currency guard --------------------------------------------------

def test_mixed_currency_journal_is_not_balanced():
    report = check_journal([entry("usd_1", currency="usd"),
                            entry("eur_1", currency="eur")])
    assert not report.balanced
    assert report.currency_mismatch is not None
    assert set(report.currency_mismatch.currencies) == {"usd", "eur"}


def test_mismatch_names_where_the_mixing_came_from():
    report = check_journal([entry("usd_1", currency="usd"),
                            entry("usd_2", currency="usd"),
                            entry("eur_1", currency="eur")])
    by = report.currency_mismatch.source_ids_by_currency
    assert by["usd"] == ["usd_1", "usd_2"]
    assert by["eur"] == ["eur_1"]


def test_cross_currency_amounts_are_never_netted_against_each_other():
    """The test §5 exists for.

    A USD entry over by 500 and a EUR entry under by 500. Anything that nets
    across currencies sums these to zero and reports a confident, meaningless
    "balanced". The report must instead surface both discrepancies *and* the
    mismatch.
    """
    report = check_journal([
        entry("usd_over", currency="usd", debit=1500, credit=1000),
        entry("eur_under", currency="eur", debit=1000, credit=1500),
    ])

    assert not report.balanced, "netted two currencies and called it balanced"
    assert [d.difference for d in report.discrepancies] == [500, -500]
    assert report.currency_mismatch is not None


def test_currency_mismatch_does_not_short_circuit_the_per_entry_pass():
    # A batch can be cross-currency AND hold an individually unbalanced entry.
    # Both facts must survive into one report.
    report = check_journal([
        entry("usd_bad", currency="usd", debit=1000, credit=900),
        entry("eur_ok", currency="eur"),
    ])
    assert report.currency_mismatch is not None
    assert [d.source_id for d in report.discrepancies] == ["usd_bad"]


def test_single_currency_journal_has_no_mismatch():
    report = check_journal([entry("a", currency="eur"), entry("b", currency="eur")])
    assert report.balanced
    assert report.currency_mismatch is None


# --- assert_balanced, the generators' precondition ----------------------------

def test_assert_balanced_is_silent_on_a_balanced_entry():
    assert assert_balanced(entry()) is None


def test_assert_balanced_raises_on_an_unbalanced_entry():
    with pytest.raises(UnbalancedEntry):
        assert_balanced(entry(debit=1000, credit=900))


def test_unbalanced_entry_is_a_value_error():
    # stripe-reconciler's route catches ValueError (app.py:1007) and the
    # ported generator tests assert ValueError. Both must keep working.
    assert issubclass(UnbalancedEntry, ValueError)
    with pytest.raises(ValueError):
        assert_balanced(entry(debit=1000, credit=900))


def test_the_exception_carries_the_discrepancy():
    # So a caller that catches it can report the numbers without re-deriving
    # them from the entry.
    with pytest.raises(UnbalancedEntry) as caught:
        assert_balanced(entry(source_id="txn_bad", debit=1000, credit=900))

    assert caught.value.discrepancy.source_id == "txn_bad"
    assert caught.value.discrepancy.difference == 100
    assert "txn_bad" in str(caught.value)


@pytest.mark.parametrize("candidate", [
    entry(debit=1000, credit=1000),
    entry(debit=1000, credit=900),
    entry(debit=0, credit=0),
    entry(debit=-500, credit=-500),
    empty_entry(),
])
def test_the_guard_and_the_report_never_disagree(candidate):
    """The property that justifies implementing one in terms of the other.

    A guard that raises on an entry the report calls balanced — or the
    reverse — would be the single most damaging bug this library could ship.
    """
    report_says_ok = check_entry(candidate).balanced
    try:
        assert_balanced(candidate)
        guard_says_ok = True
    except UnbalancedEntry:
        guard_says_ok = False

    assert report_says_ok == guard_says_ok


# --- the report as a thing a human reads --------------------------------------

def test_summary_of_a_failure_names_the_entry_and_the_amount():
    report = check_journal([entry("txn_bad", debit=1000, credit=900)])
    text = report.summary()
    assert "NOT balanced" in text
    assert "txn_bad" in text
    assert "10.00 USD" in text   # debits
    assert "9.00 USD" in text    # credits


def test_summary_reports_the_currency_code_not_a_guessed_symbol():
    # A report naming a USD/EUR mismatch cannot render both sides with '$'.
    report = check_journal([entry("eur_bad", currency="eur", debit=1000, credit=900)])
    text = report.summary()
    assert "EUR" in text
    assert "$" not in text


def test_summary_of_a_mismatch_explains_that_nothing_was_netted():
    report = check_journal([entry("a", currency="usd"), entry("b", currency="eur")])
    assert "not netted" in report.summary()


def test_report_deliberately_has_no_bool_shortcut():
    """A failed report is still truthy. Documented, not accidental.

    `if not report:` must never look like a balance check — it would silently
    never fire. Callers are forced to write `if report.balanced:`.
    """
    failed = check_journal([entry(debit=1000, credit=900)])
    assert not failed.balanced
    assert bool(failed) is True


# --- the generators still behave exactly as they did --------------------------

def test_generators_still_raise_through_the_shared_guard():
    txn = Transaction(id="txn_t", amount=1000, fee=59, net=900,
                      type="charge", created=1751000000, currency="usd")
    with pytest.raises(UnbalancedEntry):
        charge_entry(txn)


def test_a_generated_entry_passes_the_public_check():
    txn = Transaction(id="txn_t", amount=1000, fee=59, net=941,
                      type="charge", created=1751000000, currency="usd")
    assert check_entry(charge_entry(txn)).balanced
