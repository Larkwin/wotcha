"""Resolving what actually happened on a night.

The household is never asked. A past night with nothing delivered is a night
that went as planned -- derived at read time, never written. The only write
that ever happens is someone volunteering that reality differed.
"""
from datetime import date

import pytest

from wotcha.domain.models import (
    Outcome,
    SlotOutcome,
    Substitute,
)
from wotcha.domain.outcomes import resolve_outcome

TODAY = date(2026, 8, 26)  # a Wednesday


def _slot_outcome(stored=SlotOutcome.PLANNED):
    return stored


def test_a_future_night_is_still_planned():
    assert resolve_outcome(
        on_date=date(2026, 8, 28), stored=SlotOutcome.PLANNED,
        correction=None, today=TODAY,
    ) == SlotOutcome.PLANNED


def test_tonight_is_not_yet_made():
    """The night is not over. Presuming dinner happened at breakfast would put
    a meal in the history that nobody has eaten."""
    assert resolve_outcome(
        on_date=TODAY, stored=SlotOutcome.PLANNED, correction=None, today=TODAY,
    ) == SlotOutcome.PLANNED


def test_a_past_night_with_nothing_delivered_is_presumed_made():
    assert resolve_outcome(
        on_date=date(2026, 8, 25), stored=SlotOutcome.PLANNED,
        correction=None, today=TODAY,
    ) == SlotOutcome.MADE


def test_a_delivered_correction_wins():
    c = Outcome(on_date=date(2026, 8, 25), outcome=SlotOutcome.TAKEOUT)
    assert resolve_outcome(
        on_date=date(2026, 8, 25), stored=SlotOutcome.PLANNED,
        correction=c, today=TODAY,
    ) == SlotOutcome.TAKEOUT


def test_a_correction_wins_even_on_a_future_night():
    """Plans change before the day arrives -- someone knows on Monday that
    Friday is a night out. Refusing that would make the page argue with them."""
    c = Outcome(on_date=date(2026, 8, 28), outcome=SlotOutcome.TAKEOUT)
    assert resolve_outcome(
        on_date=date(2026, 8, 28), stored=SlotOutcome.PLANNED,
        correction=c, today=TODAY,
    ) == SlotOutcome.TAKEOUT


def test_backfilled_history_is_trusted_over_the_date_default():
    """Seeded weeks carry outcome='made' already. That is a recorded fact, not
    a presumption, and it must not be recomputed."""
    assert resolve_outcome(
        on_date=date(2026, 3, 2), stored=SlotOutcome.MADE,
        correction=None, today=TODAY,
    ) == SlotOutcome.MADE


def test_an_unspecified_substitute_is_not_the_same_as_off_list():
    """'Nobody said' and 'it was not one of ours' both carry no meal id. The
    whole point of recording off-list nights is that they are countable, which
    they stop being the moment they are indistinguishable from silence."""
    silent = Outcome(on_date=TODAY, outcome=SlotOutcome.SWAPPED)
    offlist = Outcome(on_date=TODAY, outcome=SlotOutcome.SWAPPED,
                      substitute=Substitute.OFF_LIST)
    assert silent.substitute is Substitute.UNSPECIFIED
    assert offlist.substitute is Substitute.OFF_LIST
    assert silent.substitute != offlist.substitute


def test_a_known_substitute_carries_its_meal_id():
    o = Outcome(on_date=TODAY, outcome=SlotOutcome.SWAPPED,
                substitute=Substitute.KNOWN, substitute_meal_id="chili")
    assert o.substitute_meal_id == "chili"


def test_a_known_substitute_without_a_meal_id_is_rejected():
    """`known` with no id is the same information as `unspecified` wearing a
    label that says otherwise."""
    with pytest.raises(ValueError):
        Outcome(on_date=TODAY, outcome=SlotOutcome.SWAPPED,
                substitute=Substitute.KNOWN)


def test_an_off_list_substitute_cannot_also_name_a_meal():
    with pytest.raises(ValueError):
        Outcome(on_date=TODAY, outcome=SlotOutcome.SWAPPED,
                substitute=Substitute.OFF_LIST, substitute_meal_id="chili")
