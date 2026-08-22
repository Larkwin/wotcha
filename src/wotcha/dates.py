"""One calendar, shared by everything that has to answer "which week?".

The household lives in Toronto; nothing that runs this code does. An
AgentCore Runtime clock is UTC and so is a Lambda's, and a UTC clock is
already tomorrow for a Toronto household after 8pm local -- so "today", and
therefore "which Monday", must be computed on the household's own calendar
date or a Saturday-evening run silently reasons about the wrong week with no
error anywhere.

This module exists because that reasoning was duplicated: `runtime.py`
deliberately used America/Toronto to decide which week to plan, while
`web/app.py` used UTC to decide which week to render. Two modules that must
agree about what day it is cannot each keep their own answer.
"""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

HOUSEHOLD_TZ = ZoneInfo("America/Toronto")


def local_today(now: datetime | None = None) -> date:
    """The household's current calendar date, independent of the timezone of
    whatever machine this happens to be running on. `now` is injectable for
    tests; production calls pass nothing and get the real instant."""
    instant = now if now is not None else datetime.now(UTC)
    return instant.astimezone(HOUSEHOLD_TZ).date()


def monday_of(day: date) -> date:
    """The Monday of the week `day` falls in -- `day` itself if it is a
    Monday."""
    return day - timedelta(days=day.weekday())


def next_monday(day: date) -> date:
    """The Monday of the *following* week. Planning always looks ahead, so a
    run on a Monday targets next Monday, not today.

    7 - weekday() is 1..7 for weekday 0..6, so it is never zero and the jump
    is always forward -- no same-day case exists to guard against.
    """
    return day + timedelta(days=7 - day.weekday())
