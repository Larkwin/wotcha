from datetime import date

import pytest
from pydantic import BaseModel

from wotcha.domain.fence import (
    AssignmentRule,
    Fence,
    FixedSlotRule,
    FrequencyTargetRule,
    RuleType,
    TakeoutBudgetRule,
    TimeCeilingRule,
    validate_plan,
)
from wotcha.domain.models import Meal, MealStatus, Slot, Week

MONDAY = date(2026, 8, 24)  # week_start for every test below

MEALS = {
    "flat-sushi": Meal(meal_id="flat-sushi", name="Flat Sushi Day", protein=None,
                       effort_minutes=0, is_takeout=True, status=MealStatus.SAFE),
    "pizza-out": Meal(meal_id="pizza-out", name="Pizza takeout", protein=None,
                      effort_minutes=0, is_takeout=True, status=MealStatus.SAFE),
    "roast-chicken": Meal(meal_id="roast-chicken", name="Roast chicken", protein="chicken",
                          effort_minutes=90, status=MealStatus.SAFE),
    "chicken-rice": Meal(meal_id="chicken-rice", name="Chicken and rice", protein="chicken",
                         effort_minutes=30, status=MealStatus.SAFE),
    "sausages": Meal(meal_id="sausages", name="Sheet-pan sausages", protein="pork",
                     effort_minutes=20, status=MealStatus.SAFE),
    "chili": Meal(meal_id="chili", name="Chili", protein="beef",
                  effort_minutes=45, status=MealStatus.SAFE),
    "pasta": Meal(meal_id="pasta", name="Pasta", protein=None,
                  effort_minutes=25, status=MealStatus.SAFE),
    "fish-tacos": Meal(meal_id="fish-tacos", name="Fish tacos", protein="fish",
                       effort_minutes=35, status=MealStatus.SAFE),
}


def week(meal_ids: list[str], cooks: list[str] | None = None) -> Week:
    """Build a seven-day week starting Monday from a list of meal ids."""
    cooks = cooks or ["alex"] * 7
    return Week(
        household_id="demo",
        week_start=MONDAY,
        slots=[
            Slot(on_date=date.fromordinal(MONDAY.toordinal() + i),
                 meal_id=mid, cook_id=cooks[i], rationale="because")
            for i, mid in enumerate(meal_ids)
        ],
    )


LEGAL = ["chili", "flat-sushi", "chicken-rice", "sausages", "pasta",
         "roast-chicken", "fish-tacos"]


def test_an_unenforced_rule_type_raises_instead_of_being_ignored():
    """A rule type with no branch in validate_plan would store, round-trip,
    appear in get_fence, be shown to the model as a standing rule -- and
    never be checked. The household would be told its rule was in force
    while nothing enforced it, and nothing anywhere would say otherwise.
    Loud beats silent: the fence is the one part of this system that is not
    allowed to be persuasive rather than real.

    model_construct bypasses the discriminated union, which is the only way
    to hold a rule pydantic does not know about -- and is exactly the state
    the code is in halfway through adding a new rule type."""

    class NotYetEnforcedRule(BaseModel):
        type: str = "seasonal_swap"

    fence = Fence.model_construct(household_id="demo",
                                  rules=[NotYetEnforcedRule()])
    with pytest.raises(TypeError, match="seasonal_swap"):
        validate_plan(week(LEGAL), fence, MEALS)


def test_a_legal_week_produces_no_violations():
    fence = Fence(household_id="demo", rules=[
        FixedSlotRule(weekday=1, meal_id="flat-sushi"),
        TakeoutBudgetRule(max_per_week=1),
        FrequencyTargetRule(protein="chicken", min_per_week=2, max_per_week=2),
    ])
    assert validate_plan(week(LEGAL), fence, MEALS) == []


def test_week_must_have_seven_consecutive_days():
    fence = Fence(household_id="demo", rules=[])
    short = week(["chili", "pasta", "sausages"])
    v = validate_plan(short, fence, MEALS)
    assert len(v) == 1
    assert "seven" in v[0].message.lower()


def test_unknown_meal_id_is_a_violation():
    fence = Fence(household_id="demo", rules=[])
    plan = week(["chili", "flat-sushi", "chicken-rice", "sausages",
                 "pasta", "roast-chicken", "unicorn-stew"])
    v = validate_plan(plan, fence, MEALS)
    assert any("unicorn-stew" in x.message for x in v)


def test_fixed_slot_must_be_honoured():
    fence = Fence(household_id="demo",
                  rules=[FixedSlotRule(weekday=1, meal_id="flat-sushi")])
    plan = week(["chili", "chili", "chicken-rice", "sausages",
                 "pasta", "roast-chicken", "fish-tacos"])
    v = validate_plan(plan, fence, MEALS)
    assert len(v) == 1
    assert v[0].rule_type is RuleType.FIXED_SLOT
    assert v[0].on_date == date(2026, 8, 25)  # the Tuesday
    assert "flat-sushi" in v[0].message
    assert "Tuesday" in v[0].message


def test_flat_sushi_day_spends_the_takeout_budget():
    """The rule interaction called out in spec section 8: Tuesday is takeout,
    so a Friday takeout is the *second* one and must be rejected."""
    fence = Fence(household_id="demo", rules=[
        FixedSlotRule(weekday=1, meal_id="flat-sushi"),
        TakeoutBudgetRule(max_per_week=1),
    ])
    plan = week(["chili", "flat-sushi", "chicken-rice", "sausages",
                 "pizza-out", "roast-chicken", "fish-tacos"])
    v = validate_plan(plan, fence, MEALS)
    assert len(v) == 1
    assert v[0].rule_type is RuleType.TAKEOUT_BUDGET
    assert "2" in v[0].message and "1" in v[0].message


def test_frequency_target_is_a_floor_as_well_as_a_ceiling():
    fence = Fence(household_id="demo", rules=[
        FrequencyTargetRule(protein="chicken", min_per_week=2, max_per_week=2),
    ])
    too_few = week(["chili", "flat-sushi", "pasta", "sausages",
                    "pasta", "roast-chicken", "fish-tacos"])
    v = validate_plan(too_few, fence, MEALS)
    assert len(v) == 1 and "at least 2" in v[0].message

    too_many = week(["chicken-rice", "flat-sushi", "chicken-rice", "sausages",
                     "pasta", "roast-chicken", "fish-tacos"])
    v = validate_plan(too_many, fence, MEALS)
    assert len(v) == 1 and "at most 2" in v[0].message


def test_time_ceiling_is_per_weekday():
    fence = Fence(household_id="demo",
                  rules=[TimeCeilingRule(weekday=3, max_minutes=25)])
    plan = week(["chili", "flat-sushi", "chicken-rice", "roast-chicken",
                 "pasta", "sausages", "fish-tacos"])
    v = validate_plan(plan, fence, MEALS)
    assert len(v) == 1
    assert v[0].rule_type is RuleType.TIME_CEILING
    assert v[0].on_date == date(2026, 8, 27)  # the Thursday
    assert "90" in v[0].message and "25" in v[0].message
    assert "Thursday" in v[0].message


def test_assignment_rule_checks_the_cook():
    fence = Fence(household_id="demo",
                  rules=[AssignmentRule(weekday=5, cook_id="dana")])
    cooks = ["alex"] * 7
    plan = week(LEGAL, cooks=cooks)
    v = validate_plan(plan, fence, MEALS)
    assert len(v) == 1
    assert v[0].rule_type is RuleType.ASSIGNMENT
    assert v[0].on_date == date(2026, 8, 29)  # the Saturday
    assert "dana" in v[0].message
    assert "Saturday" in v[0].message


def test_all_violations_are_reported_not_just_the_first():
    """The agent revises from the full list; returning one at a time would
    force needless extra rounds and inflate revisions_to_valid."""
    fence = Fence(household_id="demo", rules=[
        FixedSlotRule(weekday=1, meal_id="flat-sushi"),
        TakeoutBudgetRule(max_per_week=0),
        TimeCeilingRule(weekday=3, max_minutes=10),
    ])
    plan = week(["chili", "pizza-out", "chicken-rice", "roast-chicken",
                 "pasta", "sausages", "fish-tacos"])
    v = validate_plan(plan, fence, MEALS)
    assert len(v) >= 3
    assert len({x.rule_type for x in v}) >= 3


def test_slot_order_does_not_matter_for_structural_validity():
    """Seven correct, unique, consecutive dates are a legal week regardless of
    the order Slot objects were listed in -- by_weekday is keyed by weekday,
    not list position, and a model revising a plan may not preserve order."""
    fence = Fence(household_id="demo", rules=[
        FixedSlotRule(weekday=1, meal_id="flat-sushi"),
        TakeoutBudgetRule(max_per_week=1),
        FrequencyTargetRule(protein="chicken", min_per_week=2, max_per_week=2),
    ])
    ordered = week(LEGAL)
    shuffled_slots = [ordered.slots[i] for i in [6, 2, 0, 5, 1, 4, 3]]
    shuffled = ordered.model_copy(update={"slots": shuffled_slots})

    v = validate_plan(shuffled, fence, MEALS)

    assert not any(x.rule_type is RuleType.STRUCTURE for x in v)
    assert v == []
