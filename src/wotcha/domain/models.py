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


class SuggestionStatus(StrEnum):
    """Where a suggestion stands with the cook.

    Stored rather than derived, unlike `SlotOutcome` and escalation
    resolution. Those work because the answer is already in the table -- a
    past night nobody corrected went as planned; a published week answers a
    fence question. Nothing except the cook can produce the fact that the
    cook decided.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class SuggestionKind(StrEnum):
    """What the Liaison believes a message to be.

    Recorded, never acted on beyond NEW_MEAL and EXISTING_MEAL. v1 writes
    only suggestions -- but labelling the rest is what makes
    `extraction_accuracy` (spec section 13) measurable before the extraction
    is trusted enough to write signals and outcomes.

    UNKNOWN is the default so an unread or unreadable message never arrives
    wearing a confident classification.
    """

    UNKNOWN = "unknown"
    NEW_MEAL = "new_meal"
    EXISTING_MEAL = "existing_meal"
    # Read as a report about a night, not a request. v1 does not write
    # outcomes; the label is the whole point.
    REPORT = "report"
    # A reaction to a meal. Same -- v1 does not write signals.
    REACTION = "reaction"
    # Recognisably none of the above, including an attempt to instruct the
    # system. A row the cook can see is better than a silent drop.
    OTHER = "other"


# One SMS is at most a few hundred characters, but nothing stops a determined
# teenager pasting an essay. The cap bounds the row and the page without
# discarding anything a person would plausibly mean to send.
SUGGESTION_TEXT_MAX = 1000


class Suggestion(BaseModel):
    """Something a family member asked for, waiting on the cook.

    The Liaison enriches this and never decides it. Every proposed field is
    the agent's read and is editable by the cook before approval; `text` is
    what the person actually sent and is never replaced by the agent's read --
    the cook is deciding about their words, not a paraphrase of them. That is
    a distinct question from length: `text` is still capped at
    `SUGGESTION_TEXT_MAX`, so the row and the page stay bounded against a
    pathologically long message. Truncating is not un-verbatim-ing it; the
    cap only ever discards a tail no cook would plausibly need to read to
    decide.
    """

    household_id: str
    # The inbound message id from the SMS payload, so a redelivery overwrites
    # its own row. SQS Standard is at-least-once: the same text will
    # eventually arrive twice, and the family must not see it twice.
    suggestion_id: str
    person_id: str
    text: str
    created_at: datetime
    status: SuggestionStatus = SuggestionStatus.PENDING

    # --- the agent's read, all of it editable -------------------------
    kind: SuggestionKind = SuggestionKind.UNKNOWN
    matched_meal_id: str | None = None
    proposed_name: str | None = None
    proposed_tags: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _cap_text(cls, data):
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            data = {**data, "text": data["text"][:SUGGESTION_TEXT_MAX]}
        return data

    @model_validator(mode="after")
    def _text_is_not_blank(self) -> "Suggestion":
        if not self.text.strip():
            raise ValueError("a suggestion with no text gives the cook nothing to decide")
        return self

    @model_validator(mode="after")
    def _a_match_does_not_also_propose(self) -> "Suggestion":
        if self.matched_meal_id and self.proposed_name:
            raise ValueError(
                f"matched_meal_id={self.matched_meal_id!r} says the household already "
                f"cooks this, so proposed_name={self.proposed_name!r} asks the cook a "
                f"second, contradictory question"
            )
        return self


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
