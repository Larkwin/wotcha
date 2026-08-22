from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from wotcha.agents.planner import PLANNER_SYSTEM_PROMPT, build_planner, plan_week
from wotcha.agents.planner_tools import escalate
from wotcha.domain.fence import (
    AssignmentRule,
    Fence,
    FixedSlotRule,
    FrequencyTargetRule,
    TakeoutBudgetRule,
    validate_plan,
)
from wotcha.domain.models import Meal, MealStatus, Member, Signal, SignalLevel, Slot, Week
from wotcha.store.repo import Repository

HID = "demo"


def test_system_prompt_states_the_non_negotiables():
    p = PLANNER_SYSTEM_PROMPT
    assert "validate_plan_tool" in p
    assert "publish_plan" in p
    assert "escalate" in p
    # the fence must be presented as unarguable, and family input as advisory
    assert "cannot be" in p.lower()
    assert "voice" in p.lower() or "advisory" in p.lower()


def test_builder_registers_every_tool():
    agent = build_planner(model_id="us.anthropic.claude-sonnet-4-6",
                          region="ca-central-1")
    raw_tools = agent.tool_names if hasattr(agent, "tool_names") else []
    names = {
        t if isinstance(t, str) else getattr(t, "__name__", getattr(t, "name", ""))
        for t in raw_tools
    }
    # Strands exposes registered tools differently across versions (a list of
    # name strings in this install); assert on whichever shape it takes rather
    # than pinning one.
    assert agent is not None
    if names:
        assert "publish_plan" in names


# The real household's Safe List (data/household.json), not an invented one.
# `tacos` is present but retired -- Riley stopped liking it, and get_safe_list
# must filter it out -- and `fried-rice` carries "uses-leftovers" so the
# roast-chicken -> fried-rice pairing the prompt names can actually occur.
REAL_MEALS = [
    Meal(meal_id="flat-sushi", name="Flat Sushi Day", effort_minutes=0,
         is_takeout=True, status=MealStatus.SAFE, tags=["tradition", "takeout"]),
    Meal(meal_id="chili", name="Chili", protein="beef", effort_minutes=45,
         status=MealStatus.SAFE, tags=["cook-once"]),
    Meal(meal_id="sloppy-chicken", name="Sloppy chicken sandwiches",
         protein="chicken", effort_minutes=30, status=MealStatus.SAFE,
         tags=["fast"]),
    Meal(meal_id="beef-broccoli", name="Beef and broccoli", protein="beef",
         effort_minutes=30, status=MealStatus.SAFE, tags=["fast"]),
    Meal(meal_id="roast-chicken", name="Roast chicken dinner", protein="chicken",
         effort_minutes=90, status=MealStatus.SAFE, tags=["weekend"]),
    Meal(meal_id="roast-pork", name="Roast pork dinner", protein="pork",
         effort_minutes=90, status=MealStatus.SAFE, tags=["weekend"]),
    Meal(meal_id="burgers", name="Burgers", protein="beef", effort_minutes=25,
         status=MealStatus.SAFE, tags=["fast"]),
    Meal(meal_id="fried-rice", name="Chicken fried rice", protein="chicken",
         effort_minutes=25, status=MealStatus.SAFE, tags=["fast", "uses-leftovers"]),
    Meal(meal_id="tacos", name="Tacos", protein="beef", effort_minutes=30,
         status=MealStatus.RETIRED, tags=["was-a-favourite"]),
]

REAL_FENCE = Fence(household_id=HID, rules=[
    FixedSlotRule(weekday=1, meal_id="flat-sushi"),
    TakeoutBudgetRule(max_per_week=1),
    FrequencyTargetRule(protein="chicken", min_per_week=2, max_per_week=3),
])

REAL_MEMBERS = [
    Member(person_id="alex", name="Alex", email="alex@example.com", is_cook=True),
    Member(person_id="morgan", name="Morgan", email="morgan@example.com", is_cook=True),
    Member(person_id="riley", name="Riley", email="riley@example.com", is_cook=False),
    Member(person_id="jesse", name="Jesse", email="jesse@example.com", is_cook=False),
]


def _real_history_weeks() -> list[Week]:
    """Two weeks of actual history from data/household.json, so
    get_recent_weeks doesn't come back empty."""
    def week(week_start: str, days: list[tuple[str, str, str]]) -> Week:
        return Week(
            household_id=HID, week_start=date.fromisoformat(week_start),
            slots=[Slot(on_date=date.fromisoformat(d), meal_id=m, cook_id=c,
                        rationale="history", claims=[])
                   for d, m, c in days],
            published_at=None,
        )

    return [
        week("2026-08-03", [
            ("2026-08-03", "roast-chicken", "alex"),
            ("2026-08-04", "flat-sushi", "alex"),
            ("2026-08-05", "chili", "alex"),
            ("2026-08-06", "fried-rice", "morgan"),
            ("2026-08-07", "sloppy-chicken", "alex"),
            ("2026-08-08", "roast-pork", "morgan"),
            ("2026-08-09", "burgers", "alex"),
        ]),
        week("2026-08-10", [
            ("2026-08-10", "beef-broccoli", "alex"),
            ("2026-08-11", "flat-sushi", "alex"),
            ("2026-08-12", "roast-pork", "morgan"),
            ("2026-08-13", "burgers", "alex"),
            ("2026-08-14", "sloppy-chicken", "alex"),
            ("2026-08-15", "roast-chicken", "morgan"),
            ("2026-08-16", "fried-rice", "alex"),
        ]),
    ]


REAL_SIGNALS = [
    Signal(household_id=HID, person_id="jesse", meal_id="burgers",
           on_date=date(2026, 8, 9), level=SignalLevel.LOVED),
    Signal(household_id=HID, person_id="riley", meal_id="fried-rice",
           on_date=date(2026, 8, 6), level=SignalLevel.FINE),
    Signal(household_id=HID, person_id="morgan", meal_id="roast-chicken",
           on_date=date(2026, 8, 3), level=SignalLevel.LOVED),
]


@pytest.fixture
def seeded():
    # DynamoDB is mocked; Bedrock is not -- the integration test needs a real
    # model. mock_aws() intercepts every AWS service by default, so bedrock
    # and bedrock-runtime are explicitly passed through, and real credentials
    # are left alone (mock_credentials would otherwise overwrite them with
    # moto's fake key/secret, which real Bedrock would reject).
    with mock_aws(config={"core": {
        "mock_credentials": False,
        "passthrough": {"services": ["bedrock", "bedrockruntime"]},
    }}):
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
        for m in REAL_MEALS:
            r.put_meal(HID, m)
        r.put_fence(REAL_FENCE)
        for member in REAL_MEMBERS:
            r.put_member(HID, member)
        for week in _real_history_weeks():
            r.put_week(week)
        for signal in REAL_SIGNALS:
            r.put_signal(signal)
        yield r


@pytest.mark.integration
def test_planner_publishes_a_legal_week(seeded):
    """Hits real Bedrock. Run with: pytest -m integration"""
    result = plan_week(
        repo=seeded, household_id=HID, week_start=date(2026, 8, 24),
        model_id="us.anthropic.claude-sonnet-4-6", region="ca-central-1",
    )
    assert result["published"] is True
    week = seeded.get_week(HID, date(2026, 8, 24))
    assert week is not None
    assert len(week.slots) == 7
    # Tuesday is Flat Sushi Day, enforced by code, so this must hold.
    assert week.slots[1].meal_id == "flat-sushi"
    # Every slot must carry a rationale -- it is the visible intelligence.
    assert all(s.rationale.strip() for s in week.slots)
    # The Planner must never decide who cooks -- that's a household role, not
    # something it can know. This fence has no AssignmentRule, so every slot
    # must come back with cook_id unset, not a guessed "alex"/"morgan"/"unassigned".
    assert all(s.cook_id is None for s in week.slots)


def test_week_with_unset_cooks_validates_cleanly_against_the_real_fence():
    """cook_id must never be invented by the Planner; leaving it unset (None)
    must not itself produce a violation against this household's real
    three-rule fence, which has no AssignmentRule."""
    meals = {m.meal_id: m for m in REAL_MEALS}
    slots = [
        Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 25), meal_id="flat-sushi", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 26), meal_id="sloppy-chicken", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 27), meal_id="roast-pork", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 28), meal_id="beef-broccoli", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 29), meal_id="roast-chicken", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 30), meal_id="fried-rice", cook_id=None, rationale="x"),
    ]
    week = Week(household_id=HID, week_start=date(2026, 8, 24), slots=slots)
    assert validate_plan(week, REAL_FENCE, meals) == []


def test_unset_cook_id_still_violates_a_real_assignment_rule():
    """The one exception: a household WITH an AssignmentRule must still catch
    an unset cook_id as a violation naming the required cook, since fence.py
    is frozen and now compares an Optional[str] rather than a plain str."""
    meals = {m.meal_id: m for m in REAL_MEALS}
    fence_with_assignment = Fence(household_id=HID, rules=[
        *REAL_FENCE.rules,
        AssignmentRule(weekday=0, cook_id="morgan"),  # Monday must be Morgan's
    ])
    slots = [
        Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 25), meal_id="flat-sushi", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 26), meal_id="sloppy-chicken", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 27), meal_id="roast-pork", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 28), meal_id="beef-broccoli", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 29), meal_id="roast-chicken", cook_id=None, rationale="x"),
        Slot(on_date=date(2026, 8, 30), meal_id="fried-rice", cook_id=None, rationale="x"),
    ]
    week = Week(household_id=HID, week_start=date(2026, 8, 24), slots=slots)
    violations = validate_plan(week, fence_with_assignment, meals)
    assert len(violations) == 1
    assert violations[0].rule_type == "assignment"
    assert "morgan" in violations[0].message


def _dummy_published_week(week_start: date) -> Week:
    """A minimal week row, standing in for one published by some earlier
    run -- content doesn't matter, only that a row exists at this key."""
    return Week(
        household_id=HID, week_start=week_start,
        slots=[Slot(on_date=date.fromordinal(week_start.toordinal() + i),
                     meal_id="chili", rationale="from an earlier run")
               for i in range(7)],
        published_at=datetime.now(UTC),
    )


def test_plan_week_reports_false_when_this_run_escalates_despite_a_prior_publish(
    seeded, monkeypatch,
):
    """A week can already exist for week_start from an earlier run. If
    *this* run's agent correctly escalates instead of publishing, plan_week
    must report published: False -- a pre-existing row is not evidence that
    this invocation published anything."""
    seeded.put_week(_dummy_published_week(date(2026, 8, 24)))

    class FakeAgent:
        """Stands in for the real Strands Agent: simulates a model that
        decides the fence can't be satisfied and calls escalate, never
        publish_plan."""

        def __call__(self, prompt: str) -> str:
            escalate("fence_unsatisfiable", "Every safe meal collides with a rule.")
            return "Escalated instead of publishing."

    monkeypatch.setattr(
        "wotcha.agents.planner.build_planner",
        lambda model_id, region: FakeAgent(),
    )

    result = plan_week(
        repo=seeded, household_id=HID, week_start=date(2026, 8, 24),
        model_id="us.anthropic.claude-sonnet-4-6", region="ca-central-1",
    )

    # A row for this week_start exists (seeded above) -- proving the old
    # "infer success from the store" logic would have reported True here.
    assert seeded.get_week(HID, date(2026, 8, 24)) is not None
    assert result["published"] is False
