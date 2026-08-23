"""DynamoDB persistence, single table.

Pydantic handles serialisation via `model_dump(mode="json")`, which turns dates
into ISO strings -- exactly what the sort keys already assume, so ranges over
dates are lexicographic ranges over strings.
"""
from datetime import UTC, date, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key

from wotcha.domain.fence import Fence
from wotcha.domain.models import Meal, MealStatus, Member, Outcome, Signal, Week
from wotcha.store import keys


class Repository:
    def __init__(self, table_name: str, region: str) -> None:
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    # --- internal helpers -------------------------------------------------
    def _put(self, key: dict[str, str], model) -> None:
        self._table.put_item(Item={**key, **model.model_dump(mode="json")})

    def _query_prefix(self, household_id: str, prefix: str) -> list[dict]:
        items, kwargs = [], {}
        while True:
            resp = self._table.query(
                KeyConditionExpression=Key("pk").eq(keys.hh_pk(household_id))
                & Key("sk").begins_with(prefix),
                **kwargs,
            )
            items.extend(resp["Items"])
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    @staticmethod
    def _strip(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in ("pk", "sk")}

    # --- meals ------------------------------------------------------------
    def put_meal(self, household_id: str, meal: Meal) -> None:
        self._put(keys.meal_key(household_id, meal.meal_id), meal)

    def list_meals(
        self, household_id: str, status: MealStatus | None = None
    ) -> list[Meal]:
        meals = [Meal(**self._strip(i)) for i in self._query_prefix(household_id, "MEAL#")]
        return [m for m in meals if status is None or m.status is status]

    # --- fence ------------------------------------------------------------
    def put_fence(self, fence: Fence) -> None:
        self._put(keys.fence_key(fence.household_id), fence)

    def get_fence(self, household_id: str) -> Fence:
        resp = self._table.get_item(Key=keys.fence_key(household_id))
        if "Item" not in resp:
            # An absent fence is an empty fence, not an error: a brand new
            # household simply has no standing rules yet.
            return Fence(household_id=household_id, rules=[])
        return Fence(**self._strip(resp["Item"]))

    # --- members ----------------------------------------------------------
    def put_member(self, household_id: str, member: Member) -> None:
        self._put(keys.member_key(household_id, member.person_id), member)

    def list_members(self, household_id: str) -> list[Member]:
        return [Member(**self._strip(i))
                for i in self._query_prefix(household_id, "MEMBER#")]

    # --- weeks ------------------------------------------------------------
    def put_week(self, week: Week) -> None:
        self._put(keys.week_key(week.household_id, week.week_start), week)

    def get_week(self, household_id: str, week_start: date) -> Week | None:
        resp = self._table.get_item(Key=keys.week_key(household_id, week_start))
        return Week(**self._strip(resp["Item"])) if "Item" in resp else None

    def recent_weeks(self, household_id: str, limit: int = 4) -> list[Week]:
        items = self._query_prefix(household_id, "WEEK#")
        items.sort(key=lambda i: i["sk"], reverse=True)
        return [Week(**self._strip(i)) for i in items[:limit]]

    # --- signals ----------------------------------------------------------
    def put_signal(self, signal: Signal) -> None:
        self._put(
            keys.signal_key(signal.household_id, signal.on_date,
                            signal.person_id, signal.meal_id),
            signal,
        )

    def signals_since(self, household_id: str, since: date) -> list[Signal]:
        cutoff = f"SIGNAL#{since.isoformat()}"
        return [
            Signal(**self._strip(i))
            for i in self._query_prefix(household_id, "SIGNAL#")
            if i["sk"] >= cutoff
        ]

    # --- outcomes ---------------------------------------------------------
    def put_outcome(self, household_id: str, outcome: Outcome) -> None:
        """Record that a night went differently from the plan.

        Written only when someone volunteers it -- nothing asks and nothing
        confirms, so the absence of a row is the household saying the plan
        held. A repeat delivery for the same night overwrites: one night, one
        answer, always re-correctable.

        Kept out of the Week item on purpose. `put_week` rewrites the item
        whole, so two people correcting different nights would lose an update,
        and a published week is history the seeder's guards already treat as
        not-ours-to-rewrite.
        """
        self._table.put_item(Item={
            **outcome.model_dump(mode="json"),
            **keys.outcome_key(household_id, outcome.on_date),
        })

    def outcomes_for_week(
        self, household_id: str, week_start: date
    ) -> dict[date, Outcome]:
        """Every delivered outcome for the seven nights from `week_start`,
        keyed by date so a caller can look up a slot without scanning.

        Range-queried rather than filtered in Python: the page renders one
        week and should not pay for the whole history to do it.
        """
        end = week_start + timedelta(days=6)
        items = self._table.query(
            KeyConditionExpression=Key("pk").eq(keys.hh_pk(household_id))
            & Key("sk").between(
                f"OUTCOME#{week_start.isoformat()}", f"OUTCOME#{end.isoformat()}"
            ),
        ).get("Items", [])
        out = [Outcome.model_validate(i) for i in items]
        return {o.on_date: o for o in out}

    # --- escalations ------------------------------------------------------
    def put_escalation(self, household_id: str, record: dict) -> None:
        """Record a decision the Planner is handing to the cook."""
        for field in ("timestamp", "record_id"):
            if field not in record:
                raise ValueError(f"escalation is missing required field {field!r}")
        # Computed key spread last, as in put_eval_record: a caller's payload
        # must never decide where its own row lands in the table.
        self._table.put_item(Item={
            **record,
            **keys.escalation_key(household_id, record["timestamp"],
                                  record["record_id"]),
        })

    def unresolved_escalations(self, household_id: str) -> list[dict]:
        """Every escalation nobody has settled yet, newest first.

        Without a reader, `escalate` was a write-only endpoint: the Planner
        correctly refused to publish an impossible week and the household
        simply got no plan and no explanation. Sorted explicitly rather than
        trusting query order, exactly like recent_weeks.
        """
        items = [i for i in self._query_prefix(household_id, "ESCALATION#")
                 if not i.get("resolved")]
        items.sort(key=lambda i: i["sk"], reverse=True)
        return items

    def latest_unresolved_escalation(self, household_id: str) -> dict | None:
        """The newest escalation nobody has settled yet, or None."""
        open_rows = self.unresolved_escalations(household_id)
        return open_rows[0] if open_rows else None

    def mark_escalation_notified(self, household_id: str, sk: str) -> None:
        """Stamp an escalation as having actually reached a cook. Same reason
        as Week.notified_at: the cook must hear the question once, not once
        per retry."""
        self._table.update_item(
            Key={"pk": keys.hh_pk(household_id), "sk": sk},
            UpdateExpression="SET notified_at = :t",
            ExpressionAttributeValues={":t": datetime.now(UTC).isoformat()},
        )

    # --- eval log ---------------------------------------------------------
    def put_eval_record(self, household_id: str, record: dict) -> None:
        """Append one proposal/validation record. This is the replay corpus for
        the Minimum Viable Model study (spec section 13) -- it must be written
        from the very first real week or the corpus has a hole in it."""
        for field in ("timestamp", "record_id"):
            if field not in record:
                raise ValueError(f"eval record is missing required field {field!r}")
        # The computed key is spread last so it always wins: a caller's payload
        # must never be able to decide where its own row lands in the table.
        self._table.put_item(Item={
            **record,
            **keys.eval_key(household_id, record["timestamp"], record["record_id"]),
        })
