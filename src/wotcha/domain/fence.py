"""The fence: standing rules as a deterministic validator.

The model proposes a week; this module judges it. Nothing here consults a model,
and nothing here can be argued with -- which is what makes the guardrail real
rather than persuasive (spec section 7).

Violation messages are written for two readers: a human debugging a plan, and a
model deciding what to change. They therefore always say what is wrong AND what
would fix it.
"""
from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from wotcha.domain.models import Meal, Week

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]


class RuleType(StrEnum):
    FIXED_SLOT = "fixed_slot"
    TAKEOUT_BUDGET = "takeout_budget"
    FREQUENCY_TARGET = "frequency_target"
    TIME_CEILING = "time_ceiling"
    ASSIGNMENT = "assignment"
    STRUCTURE = "structure"  # the week itself is malformed


class FixedSlotRule(BaseModel):
    type: Literal[RuleType.FIXED_SLOT] = RuleType.FIXED_SLOT
    weekday: int = Field(ge=0, le=6)
    meal_id: str


class TakeoutBudgetRule(BaseModel):
    type: Literal[RuleType.TAKEOUT_BUDGET] = RuleType.TAKEOUT_BUDGET
    max_per_week: int = Field(ge=0)


class FrequencyTargetRule(BaseModel):
    """A target, not merely a cap -- chicken twice a week is a floor and a
    ceiling at the same time (spec section 8)."""

    type: Literal[RuleType.FREQUENCY_TARGET] = RuleType.FREQUENCY_TARGET
    protein: str
    min_per_week: int = Field(ge=0)
    max_per_week: int = Field(ge=0)


class TimeCeilingRule(BaseModel):
    type: Literal[RuleType.TIME_CEILING] = RuleType.TIME_CEILING
    weekday: int = Field(ge=0, le=6)
    max_minutes: int = Field(ge=0)


class AssignmentRule(BaseModel):
    type: Literal[RuleType.ASSIGNMENT] = RuleType.ASSIGNMENT
    weekday: int = Field(ge=0, le=6)
    cook_id: str


FenceRule = Annotated[
    FixedSlotRule | TakeoutBudgetRule | FrequencyTargetRule
    | TimeCeilingRule | AssignmentRule,
    Field(discriminator="type"),
]


class Fence(BaseModel):
    household_id: str
    rules: list[FenceRule] = Field(default_factory=list)


class Violation(BaseModel):
    rule_type: RuleType
    message: str
    on_date: date | None = None


def validate_plan(week: Week, fence: Fence, meals: dict[str, Meal]) -> list[Violation]:
    """Return every way `week` breaks `fence`. An empty list means it is legal.

    All violations are returned together, not one at a time: the agent revises
    from the full list, and drip-feeding them would inflate the number of
    revision rounds the eval measures.
    """
    violations: list[Violation] = []

    # --- structural checks first; later checks assume a well-formed week ---
    expected = [week.week_start + timedelta(days=i) for i in range(7)]
    actual = [s.on_date for s in week.slots]
    if sorted(actual) != expected:
        violations.append(Violation(
            rule_type=RuleType.STRUCTURE,
            message=(
                f"A week must contain seven consecutive slots starting "
                f"{week.week_start.isoformat()}. Got {len(week.slots)} "
                f"({', '.join(d.isoformat() for d in actual) or 'none'}). "
                f"Provide exactly one slot for each of: "
                f"{', '.join(d.isoformat() for d in expected)}."
            ),
        ))
        return violations  # nothing below is meaningful on a malformed week

    unknown = [s.meal_id for s in week.slots if s.meal_id not in meals]
    if unknown:
        violations.append(Violation(
            rule_type=RuleType.STRUCTURE,
            message=(
                f"Unknown meal ids: {', '.join(sorted(set(unknown)))}. "
                f"Every slot must use a meal_id returned by get_safe_list."
            ),
        ))
        return violations

    by_weekday = {s.on_date.weekday(): s for s in week.slots}

    for rule in fence.rules:
        if isinstance(rule, FixedSlotRule):
            slot = by_weekday[rule.weekday]
            if slot.meal_id != rule.meal_id:
                violations.append(Violation(
                    rule_type=RuleType.FIXED_SLOT,
                    on_date=slot.on_date,
                    message=(
                        f"{WEEKDAY_NAMES[rule.weekday]} is fixed to "
                        f"'{rule.meal_id}' but '{slot.meal_id}' is planned. "
                        f"Set {slot.on_date.isoformat()} to '{rule.meal_id}'."
                    ),
                ))

        elif isinstance(rule, TakeoutBudgetRule):
            takeout = [s for s in week.slots if meals[s.meal_id].is_takeout]
            if len(takeout) > rule.max_per_week:
                names = ", ".join(
                    f"{s.on_date.isoformat()} ({s.meal_id})" for s in takeout
                )
                violations.append(Violation(
                    rule_type=RuleType.TAKEOUT_BUDGET,
                    message=(
                        f"{len(takeout)} takeout nights planned but at most "
                        f"{rule.max_per_week} is allowed per week: {names}. "
                        f"Note that fixed takeout nights spend this budget too. "
                        f"Replace "
                        f"{len(takeout) - rule.max_per_week} of them with a "
                        f"cooked meal."
                    ),
                ))

        elif isinstance(rule, FrequencyTargetRule):
            count = sum(
                1 for s in week.slots if meals[s.meal_id].protein == rule.protein
            )
            if count < rule.min_per_week:
                violations.append(Violation(
                    rule_type=RuleType.FREQUENCY_TARGET,
                    message=(
                        f"'{rule.protein}' appears {count} times but is wanted "
                        f"at least {rule.min_per_week} times per week. Add "
                        f"{rule.min_per_week - count} more '{rule.protein}' meal(s)."
                    ),
                ))
            elif count > rule.max_per_week:
                violations.append(Violation(
                    rule_type=RuleType.FREQUENCY_TARGET,
                    message=(
                        f"'{rule.protein}' appears {count} times but is wanted "
                        f"at most {rule.max_per_week} times per week. Replace "
                        f"{count - rule.max_per_week} of them with another protein."
                    ),
                ))

        elif isinstance(rule, TimeCeilingRule):
            slot = by_weekday[rule.weekday]
            effort = meals[slot.meal_id].effort_minutes
            if effort > rule.max_minutes:
                violations.append(Violation(
                    rule_type=RuleType.TIME_CEILING,
                    on_date=slot.on_date,
                    message=(
                        f"{WEEKDAY_NAMES[rule.weekday]} allows at most "
                        f"{rule.max_minutes} minutes but '{slot.meal_id}' takes "
                        f"{effort}. Choose a meal of {rule.max_minutes} minutes "
                        f"or less for {slot.on_date.isoformat()}."
                    ),
                ))

        elif isinstance(rule, AssignmentRule):
            slot = by_weekday[rule.weekday]
            if slot.cook_id != rule.cook_id:
                violations.append(Violation(
                    rule_type=RuleType.ASSIGNMENT,
                    on_date=slot.on_date,
                    message=(
                        f"{WEEKDAY_NAMES[rule.weekday]} is cooked by "
                        f"'{rule.cook_id}' but '{slot.cook_id}' is assigned. "
                        f"Set cook_id to '{rule.cook_id}' for "
                        f"{slot.on_date.isoformat()}."
                    ),
                ))

        else:
            # A rule type with no branch here would store, round-trip through
            # the repository, appear in get_fence, be shown to the model as a
            # standing rule -- and never be checked. The household would be
            # told its rule was in force while nothing enforced it, and no
            # test or log would say otherwise. Loud beats silent: the fence
            # is the one part of this system that is not allowed to be
            # persuasive rather than real.
            raise TypeError(
                f"fence rule type {getattr(rule, 'type', type(rule).__name__)!r} "
                f"is not enforced by validate_plan. Every rule the household "
                f"can write must have a branch here; add one rather than "
                f"letting the rule be stored and silently ignored."
            )

    return violations
