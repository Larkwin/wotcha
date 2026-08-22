"""Single-table key construction.

Every partition key carries the household id so multi-tenancy stays a config
change rather than a migration. Sort keys are prefixed by entity type and
ordered so that range queries work: signals sort by date, evals by timestamp.
"""
from datetime import date


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


def eval_key(household_id: str, iso_timestamp: str, unique: str) -> dict[str, str]:
    return {"pk": hh_pk(household_id), "sk": f"EVAL#{iso_timestamp}#{unique}"}


def escalation_key(household_id: str, iso_timestamp: str, unique: str) -> dict[str, str]:
    """A decision handed to the cook. Timestamp-ordered like the eval log, so
    "the newest one" is a range query rather than a scan-and-compare."""
    return {"pk": hh_pk(household_id), "sk": f"ESCALATION#{iso_timestamp}#{unique}"}


def drift_case_key(household_id: str, case_id: str) -> dict[str, str]:
    """Ground truth for the Curator scorecard (spec section 13): a meal a
    specific person quietly went off, and roughly when."""
    return {"pk": hh_pk(household_id), "sk": f"DRIFTCASE#{case_id}"}
