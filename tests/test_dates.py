"""The household calendar, shared by the planner and the page.

These two modules must agree about what day it is. They did not: runtime.py
deliberately used America/Toronto while web/app.py used UTC, so the page
rendered a different week than the text announced.
"""
from datetime import UTC, date, datetime

from wotcha.dates import local_today, monday_of, next_monday


def test_local_today_uses_the_household_timezone_not_the_machines():
    """02:30 UTC on the 21st is 22:30 local on the 20th -- a different day.
    Toronto is UTC-4 in August (EDT)."""
    assert local_today(datetime(2026, 8, 21, 2, 30, tzinfo=UTC)) == date(2026, 8, 20)


def test_local_today_matches_utc_when_the_offset_does_not_cross_midnight():
    assert local_today(datetime(2026, 8, 20, 15, 0, tzinfo=UTC)) == date(2026, 8, 20)


def test_local_today_handles_the_winter_offset_too():
    """EST, not EDT: 03:30 UTC in January is 22:30 the previous day."""
    assert local_today(datetime(2026, 1, 15, 3, 30, tzinfo=UTC)) == date(2026, 1, 14)


def test_monday_of_a_monday_is_that_monday():
    assert monday_of(date(2026, 8, 24)) == date(2026, 8, 24)


def test_monday_of_mid_week_looks_back():
    assert monday_of(date(2026, 8, 27)) == date(2026, 8, 24)  # Thu -> Mon


def test_monday_of_a_sunday_looks_back_six_days():
    """The off-by-one that would silently shift the whole week."""
    assert monday_of(date(2026, 8, 30)) == date(2026, 8, 24)


def test_next_monday_from_a_monday_skips_a_whole_week():
    """Planning always looks ahead: run on a Monday, the target is next
    Monday, not today."""
    assert next_monday(date(2026, 8, 24)) == date(2026, 8, 31)


def test_next_monday_from_a_sunday_is_tomorrow():
    assert next_monday(date(2026, 8, 23)) == date(2026, 8, 24)


def test_the_two_helpers_never_return_the_same_day():
    """monday_of and next_monday must always be a week apart, for every day
    of the week -- the page falls back from one to the other, and if they
    could collide the fallback would be meaningless."""
    for offset in range(14):
        day = date(2026, 8, 17) + (date(2026, 8, 18) - date(2026, 8, 17)) * offset
        assert (next_monday(day) - monday_of(day)).days == 7
