"""Tests for scripts/seed_household.py's pre-write validation and its
refusal to destroy live data.

Uses a small inline fixture rather than the real data/household.json --
that file is meant to be hand-edited by the owner repeatedly, and the test
suite must not depend on its contents.
"""
import copy
import json
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws
from seed_household import main, validate_household_data

from wotcha.config import settings
from wotcha.domain.models import Member, Slot, Week
from wotcha.store.repo import Repository

HID = "test"


def F(path, *extra) -> list[str]:
    """argv for a seed of `path`. --file is required, so every call states it."""
    return ["--file", str(path), *extra]


def _valid_raw() -> dict:
    return {
        "household_id": "test",
        "members": [
            {"person_id": "a", "name": "A", "email": "a@example.com", "is_cook": True},
        ],
        "meals": [
            {"meal_id": "m1", "name": "Meal One", "protein": None,
             "effort_minutes": 10, "status": "safe"},
        ],
        "fence": [
            {"type": "fixed_slot", "weekday": 0, "meal_id": "m1"},
        ],
        "history": [
            {"week_start": "2026-08-03",
             "slots": [
                 {"on_date": f"2026-08-{3 + i:02d}", "meal_id": "m1", "cook_id": "a"}
                 for i in range(7)
             ]},
        ],
        "signals": [
            {"person_id": "a", "meal_id": "m1", "on_date": "2026-08-03", "level": "loved"},
        ],
        "drift_cases": [
            {"case_id": "c1", "person_id": "a", "meal_id": "m1",
             "went_off_around": "2026-01-01", "note": "n"},
        ],
    }


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A table that already holds live data, plus a temp household file. The
    seeder takes its path as a required argument, so the owner's real file is
    never reachable from a test."""
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", HID)
    monkeypatch.setenv("WOTCHA_TABLE_NAME", "wotcha-test")
    monkeypatch.setenv("WOTCHA_AWS_REGION", "us-east-1")
    settings.cache_clear()
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
        repo = Repository(table_name="wotcha-test", region="us-east-1")
        path = tmp_path / "household.json"
        path.write_text(json.dumps(_valid_raw()))
        yield repo, path
    settings.cache_clear()


def _a_real_lived_week() -> Week:
    """A week the household actually lived: real rationales, real outcomes,
    and the notified_at stamp that stops them being re-texted. None of it
    can be regenerated."""
    start = date(2026, 8, 3)
    return Week(
        household_id=HID, week_start=start,
        published_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
        notified_at=datetime(2026, 8, 1, 13, 5, tzinfo=UTC),
        slots=[Slot(on_date=date.fromordinal(start.toordinal() + i), meal_id="m1",
                    rationale="Morgan loved this on Jul 20.", outcome="made")
               for i in range(7)],
    )


def test_seeding_refuses_to_overwrite_a_published_week(live):
    """The ledger actively instructs the owner to extend the backfill through
    August. Doing that against the live table would silently destroy real
    logged weeks: real rationales, real outcomes, and the notified_at stamp."""
    repo, path = live
    repo.put_week(_a_real_lived_week())

    with pytest.raises(ValueError, match="2026-08-03"):
        main(F(path))

    after = repo.get_week(HID, date(2026, 8, 3))
    assert after.slots[0].rationale == "Morgan loved this on Jul 20."
    assert after.notified_at is not None


def test_force_allows_replacing_a_published_week(live):
    """The escape hatch has to exist -- but it has to be typed."""
    repo, path = live
    repo.put_week(_a_real_lived_week())

    assert main(F(path, "--force")) == 0
    assert repo.get_week(HID, date(2026, 8, 3)).slots[0].rationale == \
        "backfilled from memory"


def test_seeding_a_week_that_was_never_published_is_fine(live):
    """A backfilled history week carries no published_at -- re-seeding those
    is the whole point of the script being safe to re-run."""
    repo, path = live
    assert main(F(path, "--new-household")) == 0  # first seed creates it
    assert main(F(path)) == 0  # re-seeding needs no flag, and is idempotent
    assert repo.get_week(HID, date(2026, 8, 3)) is not None


def test_seeding_refuses_to_erase_a_phone_number_added_out_of_band(live):
    """A phone number typed into the console, or added to the table by the
    verification flow, is the only thing that makes a member reachable. The
    file has no phone for this member, so re-seeding would silently
    un-reach them."""
    repo, path = live
    repo.put_member(HID, Member(person_id="a", name="A", email="a@example.com",
                                phone="+15195550101", is_cook=True))

    with pytest.raises(ValueError, match="'a'"):
        main(F(path))

    assert repo.list_members(HID)[0].phone == "+15195550101"


def test_seeding_a_matching_phone_number_is_fine(live):
    """Once the number is in the file, re-seeding must not need --force --
    a flag the owner types routinely is not a guard, and the same flag
    disarms the week protection above."""
    repo, path = live
    raw = json.loads(path.read_text())
    raw["members"][0]["phone"] = "+15195550101"
    path.write_text(json.dumps(raw))
    repo.put_member(HID, Member(person_id="a", name="A", email="a@example.com",
                                phone="+15195550101", is_cook=True))

    assert main(F(path)) == 0
    assert repo.list_members(HID)[0].phone == "+15195550101"


def test_force_allows_changing_a_phone_number(live):
    repo, path = live
    repo.put_member(HID, Member(person_id="a", name="A", email="a@example.com",
                                phone="+15195550101", is_cook=True))
    assert main(F(path, "--force")) == 0
    assert repo.list_members(HID)[0].phone is None


def test_a_good_file_passes():
    validate_household_data(_valid_raw())  # must not raise


def test_dangling_meal_id_in_history_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["history"][0]["slots"][0]["meal_id"] = "does-not-exist"
    with pytest.raises(ValueError, match="does-not-exist"):
        validate_household_data(raw)


def test_dangling_cook_id_in_history_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["history"][0]["slots"][0]["cook_id"] = "ghost"
    with pytest.raises(ValueError, match="ghost"):
        validate_household_data(raw)


def test_dangling_meal_id_in_fixed_slot_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["fence"][0]["meal_id"] = "typo-meal"
    with pytest.raises(ValueError, match="typo-meal"):
        validate_household_data(raw)


def test_dangling_meal_id_in_signal_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["signals"][0]["meal_id"] = "no-such-meal"
    with pytest.raises(ValueError, match="no-such-meal"):
        validate_household_data(raw)


def test_dangling_person_id_in_signal_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["signals"][0]["person_id"] = "stranger"
    with pytest.raises(ValueError, match="stranger"):
        validate_household_data(raw)


def test_dangling_meal_id_in_drift_case_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["drift_cases"][0]["meal_id"] = "missing-meal"
    with pytest.raises(ValueError, match="missing-meal"):
        validate_household_data(raw)


def test_dangling_person_id_in_drift_case_raises_with_bad_id_named():
    raw = _valid_raw()
    raw["drift_cases"][0]["person_id"] = "nobody"
    with pytest.raises(ValueError, match="nobody"):
        validate_household_data(raw)


def test_history_week_not_seven_consecutive_days_raises():
    raw = _valid_raw()
    raw["history"][0]["slots"] = raw["history"][0]["slots"][:6]  # only six days
    with pytest.raises(ValueError, match="2026-08-03"):
        validate_household_data(raw)


def test_valid_fixture_is_not_mutated_by_a_failing_copy():
    # sanity: a deep copy used for a failing case doesn't taint the original
    original = _valid_raw()
    tampered = copy.deepcopy(original)
    tampered["history"][0]["slots"][0]["meal_id"] = "bogus"
    with pytest.raises(ValueError):
        validate_household_data(tampered)
    validate_household_data(original)  # still valid


def test_seeding_requires_an_explicit_file():
    """The file used to be a hardcoded constant, so the household seeded was
    whatever `data/household.json` happened to contain. That file is now the
    public pseudonymous household; the real one lives untracked beside it.
    Defaulting to either is a silent choice of tenant, so there is no default."""
    with pytest.raises(SystemExit):
        main([])


def test_seeding_refuses_to_create_a_household_that_does_not_exist(live):
    """The failure this exists to stop: seed a file whose household_id does
    not match the live one and nothing refuses, because every existing guard
    keys off ids that are absent. The real household is left untouched while a
    parallel one is created, and the run reports success."""
    repo, path = live
    raw = _valid_raw()
    raw["household_id"] = "not-the-live-one"
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="not-the-live-one"):
        main(["--file", str(path)])

    assert repo.list_meals("not-the-live-one") == []


def test_creating_a_household_is_allowed_when_stated(live):
    """A first seed genuinely does create a household. Stating it is a
    deliberate act; the guard only stops the silent case."""
    repo, path = live
    raw = _valid_raw()
    raw["household_id"] = "brand-new"
    path.write_text(json.dumps(raw))

    assert main(["--file", str(path), "--new-household"]) == 0
    assert [m.meal_id for m in repo.list_meals("brand-new")] == ["m1"]
