"""Single-table key construction.

Every partition key carries the household id so multi-tenancy stays a config
change rather than a migration. Sort keys are prefixed by entity type and
ordered so that range queries work: signals sort by date, evals by timestamp.
"""
from datetime import date, datetime


def hh_pk(household_id: str) -> str:
    return f"HH#{household_id}"


def meal_key(household_id: str, meal_id: str) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": f"MEAL#{meal_id}"}


def fence_key(household_id: str) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": "FENCE"}


def member_key(household_id: str, person_id: str) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": f"MEMBER#{person_id}"}


def week_key(household_id: str, week_start: date) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": f"WEEK#{week_start.isoformat()}"}


def signal_key(
    household_id: str, on_date: date, person_id: str, meal_id: str
) -> dict[str, str]:
    return {
        "pk": hh_pk(household_id),
        "sk": f"SIGNAL#{on_date.isoformat()}#{person_id}#{meal_id}",
    }


def outcome_key(household_id: str, on_date: date) -> dict[str, str]:
    """One row per night, keyed by date alone.

    Deliberately not keyed by person, unlike `signal_key`: what the household
    ate is a fact about the house, not an opinion held by one member, so a
    second delivery corrects the night instead of adding a competing account
    of it. Date-prefixed so a week is a range query.
    """
    return {"pk": hh_pk(household_id), "sk": f"OUTCOME#{on_date.isoformat()}"}


def eval_key(household_id: str, iso_timestamp: str, unique: str) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": f"EVAL#{iso_timestamp}#{unique}"}


def escalation_key(household_id: str, iso_timestamp: str, unique: str) -> dict[str, str]:
    """A decision handed to the cook. Timestamp-ordered like the eval log, so
    "the newest one" is a range query rather than a scan-and-compare."""
    return {"pk": hh_pk(household_id), "sk": f"ESCALATION#{iso_timestamp}#{unique}"}


def suggestion_key(
    household_id: str, iso_timestamp: str, suggestion_id: str
) -> dict[str, str]:
    """Something a family member asked for. Timestamp-ordered like the eval
    log and escalations, so "the newest ones" is a range query.

    The timestamp comes from the SNS envelope rather than the moment of
    writing, which is what makes this key deterministic: SQS redelivers, and
    a redelivery must land on the same row rather than beside it.

    Parsed and re-emitted rather than interpolated as given: `Suggestion`'s
    `created_at` reaches this function two ways for the same instant --
    `datetime.isoformat()` (`+00:00`) when a caller builds the key from the
    model, and pydantic's `model_dump(mode="json")` (`Z`) when a caller reads
    the offset back off the stored item. Those two strings differ, so
    interpolating verbatim would let `put_suggestion` and `get_suggestion`
    silently address different rows for the same suggestion. Normalising
    here, once, means every caller converges on one key by construction --
    not by whichever format happened to be at hand.
    """
    canonical = datetime.fromisoformat(iso_timestamp).isoformat()
    return {
        "pk": hh_pk(household_id),
        "sk": f"SUGGESTION#{canonical}#{suggestion_id}",
    }


def drift_case_key(household_id: str, case_id: str) -> dict[str, str]:
    """Ground truth for the Curator scorecard (spec section 13): a meal a
    specific person quietly went off, and roughly when."""
    return {"pk": hh_pk(household_id), "sk": f"DRIFTCASE#{case_id}"}
