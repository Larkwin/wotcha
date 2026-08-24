from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from wotcha.domain.models import (
    ClaimTag,
    Meal,
    MealStatus,
    Member,
    Signal,
    SignalLevel,
    Slot,
    SlotOutcome,
    Suggestion,
    SuggestionKind,
    SuggestionStatus,
    Week,
)


def test_meal_defaults_to_candidate_and_not_takeout():
    m = Meal(meal_id="tacos", name="Tacos", protein="beef", effort_minutes=25)
    assert m.status is MealStatus.CANDIDATE
    assert m.is_takeout is False
    assert m.tags == []


def test_takeout_meals_may_have_no_protein():
    m = Meal(
        meal_id="flat-sushi", name="Flat Sushi Day", protein=None,
        effort_minutes=0, is_takeout=True, status=MealStatus.SAFE,
    )
    assert m.protein is None
    assert m.is_takeout is True


def test_effort_minutes_cannot_be_negative():
    with pytest.raises(ValidationError):
        Meal(meal_id="x", name="X", protein="beef", effort_minutes=-1)


def test_slot_defaults_to_planned_with_no_claims():
    s = Slot(on_date=date(2026, 8, 25), meal_id="tacos", cook_id="alex", rationale="Fast.")
    assert s.outcome is SlotOutcome.PLANNED
    assert s.claims == []


def test_slot_accepts_typed_claims():
    s = Slot(
        on_date=date(2026, 8, 27), meal_id="sausages", cook_id="alex",
        rationale="20 minutes, and no protein repeat this week.",
        claims=[ClaimTag.FITS_TIME_CEILING, ClaimTag.NO_PROTEIN_REPEAT],
    )
    assert ClaimTag.FITS_TIME_CEILING in s.claims


def test_week_is_unpublished_until_published_at_is_set():
    w = Week(household_id="demo", week_start=date(2026, 8, 24), slots=[])
    assert w.published_at is None


def test_week_is_unnotified_until_notified_at_is_set():
    w = Week(household_id="demo", week_start=date(2026, 8, 24), slots=[])
    assert w.notified_at is None


def test_signal_is_scoped_to_one_person():
    sig = Signal(
        household_id="demo", person_id="maya", meal_id="tacos",
        on_date=date(2026, 8, 25), level=SignalLevel.MEH,
    )
    assert sig.person_id == "maya"
    assert sig.level is SignalLevel.MEH


def test_member_requires_at_least_one_contact_route():
    with pytest.raises(ValidationError):
        Member(person_id="ghost", name="Ghost")


def _suggestion(**over) -> dict:
    base = {
        "household_id": "demo",
        "suggestion_id": "cae173d2-66b9-564c-8309-21f858e9fb84",
        "person_id": "riley",
        "text": "can we have poutine on thursday",
        "created_at": datetime(2026, 8, 24, 18, 3, tzinfo=UTC),
    }
    base.update(over)
    return base


def test_a_new_suggestion_is_pending_and_unclassified():
    """Nothing has read it yet. `pending` is the only honest starting state,
    and a missing kind must not read as a confident classification."""
    s = Suggestion(**_suggestion())
    assert s.status is SuggestionStatus.PENDING
    assert s.kind is SuggestionKind.UNKNOWN
    assert s.matched_meal_id is None


def test_a_suggestion_keeps_the_text_verbatim():
    """The agent's read never replaces what was said. The cook is deciding
    about a person's actual words, not a paraphrase of them."""
    said = "can we PLEASE have poutine, it's been ages!!"
    s = Suggestion(**_suggestion(text=said))
    assert s.text == said


def test_an_empty_suggestion_is_refused():
    """A blank text is a row with nothing for the cook to decide about."""
    with pytest.raises(ValidationError):
        Suggestion(**_suggestion(text="   "))


def test_a_suggestion_text_is_capped():
    """Untrusted input sized by whoever is typing. An unbounded row is a
    cheap way to bloat the table and the page."""
    s = Suggestion(**_suggestion(text="x" * 5000))
    assert len(s.text) == 1000


def test_a_matched_suggestion_cannot_also_propose_a_new_name():
    """`matched_meal_id` means "we already cook this". Proposing a new meal
    at the same time asks the cook two contradictory questions."""
    with pytest.raises(ValidationError):
        Suggestion(**_suggestion(
            kind=SuggestionKind.EXISTING_MEAL,
            matched_meal_id="tacos",
            proposed_name="Tacos",
        ))
