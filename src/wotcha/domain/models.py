"""The vocabulary of the system.

A Meal is a dish, not a recipe -- no ingredients and no instructions, both of
which are explicitly out of scope (spec section 16). Signals are always scoped
to one person: a household average would hide the exact drift the product
exists to detect.
"""
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class MealStatus(StrEnum):
    SAFE = "safe"
    AUDITIONING = "auditioning"
    RETIRED = "retired"
    CANDIDATE = "candidate"


class Meal(BaseModel):
    meal_id: str
    name: str
    # None is legitimate for takeout and meatless nights; the fence's frequency
    # rules simply never match it.
    protein: str | None = None
    effort_minutes: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)
    status: MealStatus = MealStatus.CANDIDATE
    is_takeout: bool = False


class SignalLevel(StrEnum):
    LOVED = "loved"
    FINE = "fine"
    MEH = "meh"
    REFUSED = "refused"


EMOJI_TO_LEVEL = {
    "\U0001F60D": SignalLevel.LOVED,
    "\U0001F44D": SignalLevel.FINE,
    "\U0001F610": SignalLevel.MEH,
    "\U0001F645": SignalLevel.REFUSED,
}


class Signal(BaseModel):
    household_id: str
    person_id: str
    meal_id: str
    on_date: date
    level: SignalLevel
    note: str | None = None


class Suggestion(BaseModel):
    household_id: str
    person_id: str
    text: str
    created_at: datetime


class SlotOutcome(StrEnum):
    """What became of a planned night.

    `PLANNED` is the stored default and means "nothing is known yet" -- it is
    never a claim that the night did not happen. `MADE` is normally derived
    rather than written; the three below it are the only states a household
    ever delivers.
    """

    PLANNED = "planned"
    MADE = "made"
    SWAPPED = "swapped"
    TAKEOUT = "takeout"
    # Nobody cooked and it was not bought either: leftovers, everyone
    # scattered, a lazy night. Distinct from TAKEOUT because it costs the
    # takeout budget nothing.
    SKIPPED = "skipped"


class Substitute(StrEnum):
    """What replaced a swapped meal.

    Three states, not a nullable meal id, because "nobody said" and "it was
    not one of ours" both carry no id and mean opposite things. Off-list
    nights are only countable -- and they are the roster's own health metric,
    the evidence behind a Curator proposing an audition -- while they remain
    distinguishable from silence.
    """

    UNSPECIFIED = "unspecified"
    KNOWN = "known"
    OFF_LIST = "off_list"


class Outcome(BaseModel):
    """A night the household says went differently from the plan.

    Only ever written when someone volunteers it. Nothing asks, nothing
    confirms, and a night with no Outcome is resolved by `resolve_outcome`
    rather than stored -- see wotcha.domain.outcomes.

    Household-level, unlike `Signal`: what was eaten is a fact about the
    house, not a personal opinion, so any member may deliver it. Blast radius
    is one night and it is re-correctable.
    """

    on_date: date
    outcome: SlotOutcome
    substitute: Substitute = Substitute.UNSPECIFIED
    substitute_meal_id: str | None = None

    @model_validator(mode="after")
    def _substitute_agrees_with_its_meal_id(self) -> "Outcome":
        if self.substitute is Substitute.KNOWN and not self.substitute_meal_id:
            raise ValueError(
                "substitute='known' needs a substitute_meal_id: without one it "
                "is 'unspecified' wearing a label that says otherwise"
            )
        if self.substitute is not Substitute.KNOWN and self.substitute_meal_id:
            raise ValueError(
                f"substitute={self.substitute.value!r} cannot name a meal; "
                f"got substitute_meal_id={self.substitute_meal_id!r}"
            )
        return self


class ClaimTag(StrEnum):
    """Machine-verifiable assertions the Planner attaches to a slot's rationale.

    Only these are scored by `rationale_faithfulness` (spec section 13). Free
    text in the rationale is deliberately not graded -- that needs a judge,
    which is out of scope.
    """

    FITS_TIME_CEILING = "fits_time_ceiling"
    NO_PROTEIN_REPEAT = "no_protein_repeat"
    RESPECTS_ASSIGNMENT = "respects_assignment"
    WITHIN_TAKEOUT_BUDGET = "within_takeout_budget"
    IS_AUDITION = "is_audition"


class Slot(BaseModel):
    on_date: date
    meal_id: str
    # None means "not decided by the Planner" -- who cooks on a given night is
    # a household role, not a fact the agent can know, and the Planner must
    # never invent it. It is set only when the fence's AssignmentRule names a
    # cook for that weekday, which is the household's own decision, not the
    # Planner's.
    cook_id: str | None = None
    rationale: str
    claims: list[ClaimTag] = Field(default_factory=list)
    outcome: SlotOutcome = SlotOutcome.PLANNED


class Week(BaseModel):
    household_id: str
    week_start: date  # always a Monday
    slots: list[Slot] = Field(default_factory=list)
    published_at: datetime | None = None
    # Stamped once the family has been messaged. This is the idempotency guard:
    # schedulers retry, dry runs happen, and nobody should be texted twice about
    # the same week.
    notified_at: datetime | None = None


class Member(BaseModel):
    person_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    is_cook: bool = False

    @model_validator(mode="after")
    def require_a_contact_route(self) -> "Member":
        if not self.phone and not self.email:
            raise ValueError("a member needs a phone or an email to be reachable")
        return self
