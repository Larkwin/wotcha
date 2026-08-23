from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from wotcha.agents import context
from wotcha.agents import planner_tools as pt
from wotcha.domain.fence import Fence, FixedSlotRule, TakeoutBudgetRule
from wotcha.domain.models import (
    Meal,
    MealStatus,
    Outcome,
    Signal,
    SignalLevel,
    Slot,
    SlotOutcome,
    Week,
)
from wotcha.store.repo import Repository

HID = "demo"
MONDAY = "2026-08-24"

MEALS = [
    Meal(meal_id="flat-sushi", name="Flat Sushi Day", effort_minutes=0,
         is_takeout=True, status=MealStatus.SAFE),
    Meal(meal_id="chili", name="Chili", protein="beef", effort_minutes=45,
         status=MealStatus.SAFE),
    Meal(meal_id="pasta", name="Pasta", effort_minutes=25, status=MealStatus.SAFE),
    Meal(meal_id="retired-thing", name="Old", effort_minutes=20,
         status=MealStatus.RETIRED),
    # An auditioning meal is plannable, exactly like a safe one. That status
    # is filtered in two places that must stay in sync -- get_safe_list, which
    # decides what the model may choose from, and _check, which decides what
    # the fence recognises as a known meal. If they ever drift apart the model
    # is offered a meal it is then refused for using, and the only symptom is
    # a Planner that mysteriously cannot converge.
    Meal(meal_id="audition-thing", name="New Thing", protein="pork",
         effort_minutes=20, status=MealStatus.AUDITIONING),
]


def slots(meal_ids: list[str]) -> list[dict]:
    return [
        {"on_date": date.fromordinal(date(2026, 8, 24).toordinal() + i).isoformat(),
         "meal_id": mid, "cook_id": "alex", "rationale": "because", "claims": []}
        for i, mid in enumerate(meal_ids)
    ]


LEGAL = ["chili", "flat-sushi", "pasta", "pasta", "chili", "pasta", "chili"]


@pytest.fixture
def repo():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="wotcha-test",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        r = Repository(table_name="wotcha-test", region="us-east-1")
        for m in MEALS:
            r.put_meal(HID, m)
        r.put_fence(Fence(household_id=HID, rules=[
            FixedSlotRule(weekday=1, meal_id="flat-sushi"),
            TakeoutBudgetRule(max_per_week=1),
        ]))
        context.set_context(repo=r, household_id=HID, model_id="test-model")
        yield r


def test_safe_list_excludes_retired_meals(repo):
    ids = [m["meal_id"] for m in pt.get_safe_list()]
    assert "retired-thing" not in ids
    assert "chili" in ids


def test_safe_list_includes_auditioning_meals(repo):
    """Auditioning meals are the whole point of auditioning them -- a meal
    nobody is ever offered cannot earn its place on the Safe List."""
    ids = [m["meal_id"] for m in pt.get_safe_list()]
    assert "audition-thing" in ids


def test_a_week_using_an_auditioning_meal_validates_and_publishes(repo):
    """The other half of the same filter. get_safe_list offering a meal that
    _check does not recognise would refuse the model for taking exactly the
    option it was given."""
    week = ["audition-thing", "flat-sushi", "pasta", "chili", "pasta",
            "chili", "pasta"]
    assert pt.validate_plan_tool(MONDAY, slots(week))["valid"] is True
    assert pt.publish_plan(MONDAY, slots(week))["published"] is True
    assert repo.get_week(HID, date(2026, 8, 24)).slots[0].meal_id == "audition-thing"


def test_safe_list_withholds_effort_minutes(repo):
    # The Planner must never reason about cook time or how busy a night is --
    # withholding the number, not merely instructing the model to ignore it,
    # is what makes that reliable.
    meals = pt.get_safe_list()
    assert meals  # sanity: the fixture actually has meals to check
    assert all("effort_minutes" not in m for m in meals)


def test_validate_plan_tool_enforces_the_attempt_cap(repo):
    """The prompt's stated attempt limit must be a real cap, not just
    advisory text -- a model that never converges must be stopped and told
    to escalate, not left free to loop indefinitely against a real bill."""
    context.get_context().max_attempts = 2
    pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))  # attempt 1, illegal
    pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))  # attempt 2, illegal
    # attempt 3 exceeds the cap -- even a LEGAL week must be refused here,
    # since the point is to stop validating, not to get lucky on this call.
    result = pt.validate_plan_tool(MONDAY, slots(LEGAL))
    assert result["valid"] is False
    assert result["attempts_exhausted"] is True
    assert "escalate" in result["note"].lower()

    # The cap-hit attempt is still logged -- it's a reliability signal too.
    records = repo._query_prefix(HID, "EVAL#")
    assert len(records) == 3
    assert records[-1]["attempt"] == 3
    assert records[-1]["attempts_exhausted"] is True


def test_validate_returns_violations_for_an_illegal_week(repo):
    result = pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))
    assert result["valid"] is False
    assert any("flat-sushi" in v["message"] for v in result["violations"])


def test_validate_accepts_a_legal_week(repo):
    assert pt.validate_plan_tool(MONDAY, slots(LEGAL))["valid"] is True


def test_every_validation_attempt_is_logged(repo):
    pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))
    pt.validate_plan_tool(MONDAY, slots(LEGAL))
    records = repo._query_prefix(HID, "EVAL#")
    assert len(records) == 2
    assert records[0]["model_id"] == "test-model"
    assert {r["attempt"] for r in records} == {1, 2}
    # Guards the corpus: if validate_plan_tool ever wrote the wrong kind (say,
    # by copy-pasting publish_plan's "publish_refusal"), this suite would
    # otherwise still pass.
    assert {r["kind"] for r in records} == {"validation"}


def test_publish_refuses_an_invalid_plan(repo):
    result = pt.publish_plan(MONDAY, slots(["chili"] * 7))
    assert result["published"] is False
    assert result["violations"]
    assert repo.get_week(HID, date(2026, 8, 24)) is None


def test_publish_writes_a_legal_plan(repo):
    result = pt.publish_plan(MONDAY, slots(LEGAL))
    assert result["published"] is True
    week = repo.get_week(HID, date(2026, 8, 24))
    assert week is not None and week.published_at is not None


def test_get_signals_returns_only_recent_per_person_reactions(repo):
    # Hazard: get_signals() windows off date.today(), so seed relative to it
    # rather than hard-coding calendar dates -- otherwise this test would pass
    # today and start failing in a month as "recent" drifts out of the window.
    today = datetime.now(UTC).date()
    repo.put_signal(Signal(household_id=HID, person_id="riley", meal_id="chili",
                            on_date=today - timedelta(days=5),
                            level=SignalLevel.LOVED))
    repo.put_signal(Signal(household_id=HID, person_id="jesse", meal_id="pasta",
                            on_date=today - timedelta(days=100),
                            level=SignalLevel.REFUSED))

    result = pt.get_signals(days=60)

    pairs = {(s["person_id"], s["meal_id"]) for s in result}
    assert ("riley", "chili") in pairs
    assert ("jesse", "pasta") not in pairs  # outside the 60-day window


def test_escalate_records_a_decision_for_the_cook(repo):
    out = pt.escalate("fence_unsatisfiable", "Two traditions collide. Which gives?")
    assert out["escalated"] is True
    assert repo._query_prefix(HID, "ESCALATION#")


def test_escalate_marks_the_context_so_the_run_can_report_it(repo):
    """The row alone told nobody: writing an ESCALATION# item that nothing
    ever reads is how a household ends up with no plan and no explanation."""
    assert context.get_context().escalated is False
    pt.escalate("fence_unsatisfiable", "Two traditions collide. Which gives?")
    assert context.get_context().escalated is True


def test_escalation_row_is_readable_by_the_repository_reader(repo):
    """Written through put_escalation, not straight at the table, so the
    reader the runtime depends on can actually find it."""
    context.set_context(repo=repo, household_id=HID, model_id="test-model",
                        week_start=date(2026, 8, 24))
    pt.escalate("fence_unsatisfiable", "Two traditions collide. Which gives?")
    row = repo.latest_unresolved_escalation(HID)
    assert row is not None
    assert row["question"] == "Two traditions collide. Which gives?"
    assert row["week_start"] == "2026-08-24"


def test_publish_is_refused_once_the_attempt_cap_is_exhausted(repo):
    """The cap has to bind at publish time too. A model that ignores the
    validation cap can otherwise loop on publish_plan instead -- a real fence
    check and a real DynamoDB write per call, against a real bill."""
    context.get_context().max_attempts = 2
    pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))  # attempt 1
    pt.validate_plan_tool(MONDAY, slots(["chili"] * 7))  # attempt 2
    pt.validate_plan_tool(MONDAY, slots(LEGAL))          # attempt 3, over cap

    # Even a LEGAL week is refused: the point is to stop, not to get lucky.
    result = pt.publish_plan(MONDAY, slots(LEGAL))

    assert result["published"] is False
    assert result["attempts_exhausted"] is True
    assert "escalate" in result["note"].lower()
    assert repo.get_week(HID, date(2026, 8, 24)) is None


def test_publish_refusals_alone_eventually_exhaust_the_cap(repo):
    """A model that never calls validate_plan_tool sat at attempt 0 forever,
    so no cap could ever bite it. A refused publish is an attempt at a legal
    week and costs exactly what a validation costs."""
    context.get_context().max_attempts = 2
    for _ in range(3):
        assert pt.publish_plan(MONDAY, slots(["chili"] * 7)).get(
            "attempts_exhausted") is None
    assert pt.publish_plan(MONDAY, slots(["chili"] * 7))["attempts_exhausted"] is True


def test_publish_refusal_is_logged_as_a_publish_refusal_record(repo):
    pt.publish_plan(MONDAY, slots(["chili"] * 7))
    records = repo._query_prefix(HID, "EVAL#")
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "publish_refusal"
    assert record["model_id"] == "test-model"
    assert record["week_start"] == MONDAY
    assert record["violations"]
    assert record["attempt"] == 0  # publish_plan was called with no prior validation


def test_publish_preserves_the_notified_stamp_of_the_week_it_replaces(repo):
    """put_week overwrites the stored item wholesale, and publish_plan builds
    a brand-new Week whose notified_at defaults to None. Republishing the
    same week -- a scheduler retry, a second `plan_and_notify` -- must carry
    the stored row's notified_at forward, or the idempotency guard that stops
    the family being texted twice is erased by the very replan it exists to
    survive.

    This is not merely bookkeeping: a replan publishes a *different* legal
    week, so the family would be holding a text describing plan A while the
    page shows plan B, and the next run would text them all over again."""
    pt.publish_plan(MONDAY, slots(LEGAL))
    stored = repo.get_week(HID, date(2026, 8, 24))
    stamp = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    stored.notified_at = stamp
    repo.put_week(stored)

    # A different, also-legal week -- what a real replan produces.
    replan = ["pasta", "flat-sushi", "chili", "pasta", "chili", "pasta", "chili"]
    assert pt.publish_plan(MONDAY, replan and slots(replan))["published"] is True

    after = repo.get_week(HID, date(2026, 8, 24))
    assert after.slots[0].meal_id == "pasta"      # the replan really landed
    assert after.notified_at == stamp             # ...without erasing the guard
    assert after.published_at is not None


def test_publish_leaves_notified_at_unset_for_a_week_never_notified(repo):
    """The carry-forward must not invent a stamp: a first publish of a week
    nobody has been told about is still un-notified, and notify must be free
    to run."""
    pt.publish_plan(MONDAY, slots(LEGAL))
    assert repo.get_week(HID, date(2026, 8, 24)).notified_at is None


def test_publish_success_writes_no_eval_record(repo):
    pt.publish_plan(MONDAY, slots(LEGAL))
    assert repo._query_prefix(HID, "EVAL#") == []


WEEK_WITH_RETIRED_MEAL = [
    "chili", "flat-sushi", "retired-thing", "pasta", "chili", "pasta", "chili"
]


def test_validate_rejects_a_retired_meal(repo):
    result = pt.validate_plan_tool(MONDAY, slots(WEEK_WITH_RETIRED_MEAL))
    assert result["valid"] is False
    assert any("retired-thing" in v["message"] for v in result["violations"])


def test_publish_refuses_a_week_with_a_retired_meal(repo):
    result = pt.publish_plan(MONDAY, slots(WEEK_WITH_RETIRED_MEAL))
    assert result["published"] is False
    assert any("retired-thing" in v["message"] for v in result["violations"])
    assert repo.get_week(HID, date(2026, 8, 24)) is None


def test_validate_handles_a_malformed_week_start_without_crashing(repo):
    result = pt.validate_plan_tool("not-a-date", slots(LEGAL))
    assert result["valid"] is False
    assert result["violations"]
    assert result["violations"][0]["rule_type"] == "structure"


def test_validate_handles_a_malformed_slot_without_crashing(repo):
    bad_slots = slots(LEGAL)
    del bad_slots[0]["rationale"]  # a required Slot field, missing
    result = pt.validate_plan_tool(MONDAY, bad_slots)
    assert result["valid"] is False
    assert result["violations"]


def test_attempt_counter_and_eval_records_stay_consistent_across_a_crash(repo):
    pt.validate_plan_tool("not-a-date", slots(LEGAL))  # attempt 1, malformed
    pt.validate_plan_tool(MONDAY, slots(LEGAL))         # attempt 2, valid
    records = repo._query_prefix(HID, "EVAL#")
    assert len(records) == 2  # one record per attempt, including the crash
    assert {r["attempt"] for r in records} == {1, 2}


def test_publish_handles_a_malformed_input_without_crashing(repo):
    result = pt.publish_plan("not-a-date", slots(LEGAL))
    assert result["published"] is False
    assert result["violations"]
    records = repo._query_prefix(HID, "EVAL#")
    assert len(records) == 1
    assert records[0]["kind"] == "publish_refusal"


# --- history the Planner reads ------------------------------------------
#
# The other half of outcome capture. A live week is stored with every slot
# `planned` and nothing ever rewrites it, so history read straight off the
# row says the household ate nothing -- and a corrected night reads as eaten
# anyway. These pin the three precedence branches of resolve_outcome as the
# Planner actually sees them.

HISTORY_MONDAY = date(2026, 8, 10)


def _put_history(repo, *, stored=SlotOutcome.PLANNED, monday=HISTORY_MONDAY):
    """One week of seven chili nights, all carrying the same stored outcome."""
    repo.put_week(Week(
        household_id=HID, week_start=monday,
        published_at=datetime.now(UTC),
        slots=[Slot(on_date=monday + timedelta(days=i), meal_id="chili",
                    rationale="because", outcome=stored)
               for i in range(7)],
    ))


def _outcomes(repo, monkeypatch, today: date) -> list[str]:
    monkeypatch.setattr(pt, "local_today", lambda: today)
    weeks = pt.get_recent_weeks()
    return [s["outcome"] for s in weeks[0]["slots"]]


def test_a_finished_night_nobody_corrected_reads_as_made(repo, monkeypatch):
    """The stored value stays `planned` forever. Without resolution the
    Planner reads a whole eaten week as never having happened, and cheerfully
    repeats every meal in it."""
    _put_history(repo)
    assert _outcomes(repo, monkeypatch, HISTORY_MONDAY + timedelta(days=7)) == \
        ["made"] * 7


def test_a_delivered_correction_beats_the_presumption(repo, monkeypatch):
    """The whole point of outcome capture: a swapped night was not eaten, so
    that meal is still fresh."""
    _put_history(repo)
    repo.put_outcome(HID, Outcome(on_date=HISTORY_MONDAY,
                                  outcome=SlotOutcome.TAKEOUT))
    got = _outcomes(repo, monkeypatch, HISTORY_MONDAY + timedelta(days=7))
    assert got[0] == "takeout"
    assert got[1:] == ["made"] * 6


def test_a_correction_dated_in_the_future_still_wins(repo, monkeypatch):
    """Knowing on Monday that Friday is a night out is ordinary. This is what
    separates resolve_outcome from a plain date comparison."""
    _put_history(repo)
    friday = HISTORY_MONDAY + timedelta(days=4)
    repo.put_outcome(HID, Outcome(on_date=friday, outcome=SlotOutcome.SKIPPED))
    got = _outcomes(repo, monkeypatch, HISTORY_MONDAY)
    assert got[4] == "skipped"
    assert got[0] == "planned"  # today itself is not yet made


def test_a_live_week_stays_planned_from_today_onward(repo, monkeypatch):
    _put_history(repo)
    got = _outcomes(repo, monkeypatch, HISTORY_MONDAY + timedelta(days=3))
    assert got == ["made"] * 3 + ["planned"] * 4


def test_a_stored_outcome_from_seeded_backfill_is_never_recomputed(repo, monkeypatch):
    """Seeded history carries MADE already, and drift cases will carry more
    than that. Recomputing it would replace a recorded fact with a guess."""
    _put_history(repo, stored=SlotOutcome.SWAPPED)
    assert _outcomes(repo, monkeypatch, HISTORY_MONDAY + timedelta(days=7)) == \
        ["swapped"] * 7


def test_resolution_is_not_written_back_to_the_week(repo, monkeypatch):
    """outcomes.py's contract is that the presumption is computed at read time
    and never stored. A tool that resolved onto the model and then anything
    calling put_week would quietly turn the guess into a fact."""
    _put_history(repo)
    _outcomes(repo, monkeypatch, HISTORY_MONDAY + timedelta(days=7))
    stored = repo.get_week(HID, HISTORY_MONDAY)
    assert [s.outcome for s in stored.slots] == [SlotOutcome.PLANNED] * 7


def test_each_week_reads_its_own_corrections(repo, monkeypatch):
    """Two weeks, one correction. Resolving per week rather than over one span
    is what keeps a neighbouring week's night out of this week's answer."""
    earlier = HISTORY_MONDAY - timedelta(days=7)
    _put_history(repo, monday=earlier)
    _put_history(repo)
    repo.put_outcome(HID, Outcome(on_date=earlier, outcome=SlotOutcome.SKIPPED))
    monkeypatch.setattr(pt, "local_today",
                        lambda: HISTORY_MONDAY + timedelta(days=7))
    weeks = pt.get_recent_weeks()
    assert weeks[0]["week_start"] == HISTORY_MONDAY.isoformat()  # newest first
    assert [s["outcome"] for s in weeks[0]["slots"]] == ["made"] * 7
    assert weeks[1]["slots"][0]["outcome"] == "skipped"
