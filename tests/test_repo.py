from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from wotcha.domain.fence import Fence, FixedSlotRule, TakeoutBudgetRule
from wotcha.domain.models import (
    Meal,
    MealStatus,
    Member,
    Signal,
    SignalLevel,
    Slot,
    Week,
)
from wotcha.store import keys
from wotcha.store.repo import Repository

HID = "demo"


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
        yield Repository(table_name="wotcha-test", region="us-east-1")


def test_meal_round_trips(repo):
    repo.put_meal(HID, Meal(meal_id="tacos", name="Tacos", protein="beef",
                            effort_minutes=25, status=MealStatus.SAFE))
    meals = repo.list_meals(HID)
    assert len(meals) == 1
    assert meals[0].name == "Tacos"
    assert meals[0].status is MealStatus.SAFE


def test_list_meals_filters_by_status(repo):
    repo.put_meal(HID, Meal(meal_id="a", name="A", effort_minutes=10,
                            status=MealStatus.SAFE))
    repo.put_meal(HID, Meal(meal_id="b", name="B", effort_minutes=10,
                            status=MealStatus.RETIRED))
    safe = repo.list_meals(HID, status=MealStatus.SAFE)
    assert [m.meal_id for m in safe] == ["a"]


def test_fence_round_trips_with_discriminated_rules(repo):
    repo.put_fence(Fence(household_id=HID, rules=[
        FixedSlotRule(weekday=1, meal_id="flat-sushi"),
        TakeoutBudgetRule(max_per_week=1),
    ]))
    fence = repo.get_fence(HID)
    assert len(fence.rules) == 2
    assert isinstance(fence.rules[0], FixedSlotRule)
    assert fence.rules[0].meal_id == "flat-sushi"


def test_missing_fence_returns_an_empty_fence(repo):
    assert repo.get_fence(HID).rules == []


def test_week_round_trips(repo):
    w = Week(household_id=HID, week_start=date(2026, 8, 24), slots=[
        Slot(on_date=date(2026, 8, 24), meal_id="tacos", cook_id="alex",
             rationale="Monday is easy."),
    ])
    repo.put_week(w)
    got = repo.get_week(HID, date(2026, 8, 24))
    assert got is not None
    assert got.slots[0].rationale == "Monday is easy."


def test_recent_weeks_returns_newest_first(repo):
    for d in (date(2026, 8, 10), date(2026, 8, 17), date(2026, 8, 24)):
        repo.put_week(Week(household_id=HID, week_start=d, slots=[]))
    weeks = repo.recent_weeks(HID, limit=2)
    assert [w.week_start for w in weeks] == [date(2026, 8, 24), date(2026, 8, 17)]


def test_signals_are_stored_per_person(repo):
    for person in ("maya", "sam"):
        repo.put_signal(Signal(household_id=HID, person_id=person, meal_id="tacos",
                               on_date=date(2026, 8, 25), level=SignalLevel.MEH))
    got = repo.signals_since(HID, date(2026, 8, 1))
    assert sorted(s.person_id for s in got) == ["maya", "sam"]


def test_signals_since_excludes_older_signals(repo):
    repo.put_signal(Signal(household_id=HID, person_id="maya", meal_id="tacos",
                           on_date=date(2026, 1, 1), level=SignalLevel.LOVED))
    repo.put_signal(Signal(household_id=HID, person_id="maya", meal_id="tacos",
                           on_date=date(2026, 8, 25), level=SignalLevel.MEH))
    got = repo.signals_since(HID, date(2026, 8, 1))
    assert len(got) == 1
    assert got[0].level is SignalLevel.MEH


def test_members_round_trip(repo):
    repo.put_member(HID, Member(person_id="alex", name="Alex",
                                phone="+15195550123", is_cook=True))
    members = repo.list_members(HID)
    assert members[0].is_cook is True


def test_week_notified_at_round_trips(repo):
    # notified_at is the idempotency guard that stops a scheduler retry from
    # texting a family twice about the same week -- it must persist.
    w = Week(household_id=HID, week_start=date(2026, 8, 24), slots=[],
             notified_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC))
    repo.put_week(w)
    got = repo.get_week(HID, date(2026, 8, 24))
    assert got is not None
    assert got.notified_at == w.notified_at


def test_household_data_is_isolated(repo):
    other = "other-household"
    repo.put_meal(HID, Meal(meal_id="tacos", name="Tacos", effort_minutes=10,
                            status=MealStatus.SAFE))
    repo.put_meal(other, Meal(meal_id="pizza", name="Pizza", effort_minutes=10,
                              status=MealStatus.SAFE))
    repo.put_week(Week(household_id=HID, week_start=date(2026, 8, 24), slots=[]))
    repo.put_week(Week(household_id=other, week_start=date(2026, 8, 24), slots=[]))
    repo.put_signal(Signal(household_id=HID, person_id="alex", meal_id="tacos",
                           on_date=date(2026, 8, 25), level=SignalLevel.MEH))
    repo.put_signal(Signal(household_id=other, person_id="sam", meal_id="pizza",
                           on_date=date(2026, 8, 25), level=SignalLevel.LOVED))

    assert [m.meal_id for m in repo.list_meals(HID)] == ["tacos"]
    assert [m.meal_id for m in repo.list_meals(other)] == ["pizza"]

    assert repo.get_week(HID, date(2026, 8, 24)).household_id == HID
    assert repo.get_week(other, date(2026, 8, 24)).household_id == other

    hid_signals = repo.signals_since(HID, date(2026, 8, 1))
    other_signals = repo.signals_since(other, date(2026, 8, 1))
    assert [s.person_id for s in hid_signals] == ["alex"]
    assert [s.person_id for s in other_signals] == ["sam"]


def _escalation(question: str, ts: str, resolved: bool = False) -> dict:
    return {"record_id": ts[-4:], "timestamp": ts, "reason": "fence_unsatisfiable",
            "question": question, "resolved": resolved, "week_start": "2026-08-24"}


def test_latest_unresolved_escalation_returns_the_newest_one(repo):
    repo.put_escalation(HID, _escalation("older?", "2026-08-18T09:00:00+00:00"))
    repo.put_escalation(HID, _escalation("newest?", "2026-08-20T09:00:00+00:00"))
    repo.put_escalation(HID, _escalation("middle?", "2026-08-19T09:00:00+00:00"))
    assert repo.latest_unresolved_escalation(HID)["question"] == "newest?"


def test_latest_unresolved_escalation_skips_settled_ones(repo):
    repo.put_escalation(HID, _escalation("open?", "2026-08-18T09:00:00+00:00"))
    repo.put_escalation(HID, _escalation("settled?", "2026-08-20T09:00:00+00:00",
                                          resolved=True))
    assert repo.latest_unresolved_escalation(HID)["question"] == "open?"


def test_latest_unresolved_escalation_is_none_when_there_are_none(repo):
    assert repo.latest_unresolved_escalation(HID) is None


def test_mark_escalation_notified_stamps_the_row(repo):
    repo.put_escalation(HID, _escalation("open?", "2026-08-18T09:00:00+00:00"))
    row = repo.latest_unresolved_escalation(HID)
    assert row.get("notified_at") is None
    repo.mark_escalation_notified(HID, row["sk"])
    assert repo.latest_unresolved_escalation(HID)["notified_at"]


def test_eval_record_round_trips_under_eval_prefix(repo):
    record = {"timestamp": "2026-08-24T10:00:00", "record_id": "r1", "score": 1}
    repo.put_eval_record(HID, record)
    items = repo._query_prefix(HID, "EVAL#")
    assert len(items) == 1
    assert items[0]["record_id"] == "r1"
    assert items[0]["score"] == 1


def test_eval_record_key_is_not_clobbered_by_caller_supplied_pk_sk(repo):
    # A caller-supplied pk/sk inside the record must never win over the
    # computed key -- otherwise the record is misfiled with no complaint.
    record = {
        "timestamp": "2026-08-24T10:00:00",
        "record_id": "r1",
        "pk": "HH#someone-else",
        "sk": "BOGUS",
    }
    repo.put_eval_record(HID, record)

    expected_key = keys.eval_key(HID, "2026-08-24T10:00:00", "r1")
    resp = repo._table.get_item(Key=expected_key)
    assert "Item" in resp
    assert resp["Item"]["pk"] == expected_key["pk"]
    assert resp["Item"]["sk"] == expected_key["sk"]

    # And nothing was written under the caller-supplied bogus key.
    bogus_resp = repo._table.get_item(Key={"pk": "HH#someone-else", "sk": "BOGUS"})
    assert "Item" not in bogus_resp


def test_eval_record_missing_timestamp_raises_clear_error(repo):
    with pytest.raises(ValueError, match="timestamp"):
        repo.put_eval_record(HID, {"record_id": "r1"})


def test_eval_record_missing_record_id_raises_clear_error(repo):
    with pytest.raises(ValueError, match="record_id"):
        repo.put_eval_record(HID, {"timestamp": "2026-08-24T10:00:00"})
