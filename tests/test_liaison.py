"""The Liaison's read of one message.

The agent is faked here, as the Planner's is in tests. What is worth testing
is the contract around it: what it is given, what shape it must return, and
that a model failure degrades to a row the cook can still see rather than a
lost message.
"""
from datetime import UTC, datetime

from wotcha.agents import liaison
from wotcha.domain.models import Meal, MealStatus, Suggestion, SuggestionKind

MEALS = [
    Meal(meal_id="tacos", name="Tacos", protein="beef", effort_minutes=30,
         status=MealStatus.SAFE),
    Meal(meal_id="chili", name="Chili", protein="beef", effort_minutes=45,
         status=MealStatus.SAFE),
    Meal(meal_id="old-thing", name="Old Thing", effort_minutes=20,
         status=MealStatus.RETIRED),
]


def test_the_roster_offered_to_the_model_excludes_retired_meals():
    """Mirrors get_safe_list. A retired meal must be as unknown here as one
    that never existed, or the agent matches a request against something the
    household deliberately stopped cooking."""
    rendered = liaison.render_roster(MEALS)
    assert "Tacos" in rendered
    assert "Old Thing" not in rendered


def test_an_empty_roster_still_renders_something_usable():
    """A brand-new household has no meals. The prompt must not become
    malformed, and the model must not be handed a dangling header."""
    assert liaison.render_roster([]).strip() != ""


def test_a_model_failure_becomes_an_unknown_read_not_an_exception(monkeypatch):
    """The message is already in the queue and the person already sent it. A
    Bedrock outage must cost the cook context, never the request itself --
    the row still gets written, wearing an honest UNKNOWN."""
    def explodes(*_a, **_k):
        raise RuntimeError("bedrock is having a day")

    monkeypatch.setattr(liaison, "_structured_read", explodes)
    read = liaison.read_message("can we have poutine", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.kind is SuggestionKind.UNKNOWN
    assert read.proposed_name is None
    assert "could not be read" in (read.note or "")


def test_a_read_naming_an_unknown_meal_is_refused(monkeypatch):
    """The model returns a meal_id as free text and can invent one. A
    matched_meal_id nothing recognises would render as a match to a meal that
    does not exist."""
    monkeypatch.setattr(liaison, "_structured_read", lambda *a, **k: liaison.LiaisonRead(
        kind=SuggestionKind.EXISTING_MEAL, matched_meal_id="invented", note="x"))
    read = liaison.read_message("whatever", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.matched_meal_id is None
    assert read.kind is SuggestionKind.UNKNOWN


def test_a_match_against_a_real_meal_survives(monkeypatch):
    monkeypatch.setattr(liaison, "_structured_read", lambda *a, **k: liaison.LiaisonRead(
        kind=SuggestionKind.EXISTING_MEAL, matched_meal_id="tacos", note="x"))
    read = liaison.read_message("can we have tacos", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.matched_meal_id == "tacos"
    assert read.kind is SuggestionKind.EXISTING_MEAL


def test_a_valid_match_with_a_proposed_name_keeps_the_match(monkeypatch):
    """"Can we have tacos" plausibly produces both a match and a name -- the
    model was not asked to choose. Suggestion refuses a row asserting both
    (matched_meal_id and proposed_name ask the cook two contradictory
    questions), so read_message must resolve the collision itself rather
    than hand the caller a read that blows up downstream. The match wins:
    it was checked against the real roster, while proposed_name is free
    text the model invented and has nothing to lose."""
    monkeypatch.setattr(liaison, "_structured_read", lambda *a, **k: liaison.LiaisonRead(
        kind=SuggestionKind.EXISTING_MEAL, matched_meal_id="tacos",
        proposed_name="Tacos", note="x"))
    read = liaison.read_message("can we have tacos", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.matched_meal_id == "tacos"
    assert read.proposed_name is None
    # The assertion that actually pins the hazard shut: the normalised read
    # must be usable to build the real downstream row without raising.
    Suggestion(
        household_id="demo", suggestion_id="msg-1", person_id="kid",
        text="can we have tacos", created_at=datetime.now(UTC),
        kind=read.kind, matched_meal_id=read.matched_meal_id,
        proposed_name=read.proposed_name, note=read.note,
    )


def test_a_valid_match_overrides_a_disagreeing_new_meal_classification(monkeypatch):
    """M1: the model can classify a message as NEW_MEAL and still return a
    matched_meal_id that checks out against the real roster (an over-helpful
    reply, same as "can we have tacos" above). Dropping proposed_name here
    without also correcting kind would leave a read that says both
    kind=new_meal and matched_meal_id=tacos -- neither what the model said
    (it also said proposed_name) nor what actually gets stored (no name).
    kind must follow the field that was actually kept."""
    monkeypatch.setattr(liaison, "_structured_read", lambda *a, **k: liaison.LiaisonRead(
        kind=SuggestionKind.NEW_MEAL, matched_meal_id="tacos",
        proposed_name="Tacos Supreme", note="x"))
    read = liaison.read_message("tacos please", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.matched_meal_id == "tacos"
    assert read.proposed_name is None
    assert read.kind is SuggestionKind.EXISTING_MEAL


def test_an_invented_match_with_a_proposed_name_keeps_the_name(monkeypatch):
    """The unknown-id drop must run before the match/name collision check.
    Otherwise an invented matched_meal_id would take a perfectly good
    proposed_name down with it, when dropping the bad id alone already
    leaves a clean, usable read."""
    monkeypatch.setattr(liaison, "_structured_read", lambda *a, **k: liaison.LiaisonRead(
        kind=SuggestionKind.EXISTING_MEAL, matched_meal_id="invented",
        proposed_name="Poutine", note="x"))
    read = liaison.read_message("can we have poutine", MEALS,
                                model_id="test-model", region="us-east-1")
    assert read.matched_meal_id is None
    assert read.proposed_name == "Poutine"
    assert read.kind is SuggestionKind.UNKNOWN
