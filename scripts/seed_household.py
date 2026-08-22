"""Load data/household.json into DynamoDB. Safe to re-run after editing the
file, which you will do repeatedly as more history comes back to you.

It refuses, before writing anything, to overwrite a week the household
actually lived (one with a published_at) or to change a phone number
already in the table -- both of which this script used to do silently,
replacing real rationales and outcomes with "backfilled from memory" and
resetting the notified_at stamp. `--force` overrides both; it is meant to
be typed deliberately and rarely.

Re-running is otherwise safe, but only for value edits: changing a meal's
protein, a fence threshold, adding a history week. Renaming an identity
field (meal_id, person_id, case_id) does NOT overwrite the old row, because
that id is embedded in the sort key -- it writes a new row and leaves the old one behind. An orphaned
meal keeps status "safe" and the Planner will keep scheduling it. Delete
orphans by hand (aws dynamodb delete-item) after a rename; this script does
not detect or clean them up.
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from wotcha.config import settings
from wotcha.domain.fence import Fence
from wotcha.domain.models import Meal, Member, Signal, Slot, Week
from wotcha.store import keys
from wotcha.store.repo import Repository

DATA = Path(__file__).resolve().parent.parent / "data" / "household.json"


def validate_household_data(raw: dict) -> None:
    """Raise ValueError, naming the offending value, if `raw` is internally
    inconsistent. Runs before any write to the table.

    This file is meant to be hand-edited many times as more real history
    comes back to the owner. A typo in an id currently succeeds silently
    (DynamoDB does not enforce foreign keys) and surfaces much later as
    confusing behaviour -- a meal that never shows up, a signal nobody can
    trace. Catching it here, at edit time, is cheap; catching it later is not.
    """
    meal_ids = {m["meal_id"] for m in raw["meals"]}
    person_ids = {p["person_id"] for p in raw["members"]}

    for rule in raw["fence"]:
        if rule.get("type") == "fixed_slot" and rule["meal_id"] not in meal_ids:
            raise ValueError(
                f"fence fixed_slot rule references unknown meal_id "
                f"{rule['meal_id']!r} -- not in data['meals']"
            )

    for week in raw["history"]:
        week_start = date.fromisoformat(week["week_start"])
        expected_dates = [week_start + timedelta(days=i) for i in range(7)]
        actual_dates = sorted(date.fromisoformat(s["on_date"]) for s in week["slots"])
        if actual_dates != expected_dates:
            raise ValueError(
                f"history week {week['week_start']!r} must contain exactly "
                f"seven consecutive days starting on its week_start "
                f"({week['week_start']!r}); got "
                f"{[d.isoformat() for d in actual_dates]}"
            )
        for slot in week["slots"]:
            if slot["meal_id"] not in meal_ids:
                raise ValueError(
                    f"history week {week['week_start']!r}, slot "
                    f"{slot['on_date']!r} references unknown meal_id "
                    f"{slot['meal_id']!r} -- not in data['meals']"
                )
            if slot["cook_id"] not in person_ids:
                raise ValueError(
                    f"history week {week['week_start']!r}, slot "
                    f"{slot['on_date']!r} references unknown cook_id "
                    f"{slot['cook_id']!r} -- not in data['members']"
                )

    for sig in raw["signals"]:
        if sig["meal_id"] not in meal_ids:
            raise ValueError(
                f"signal on {sig['on_date']!r} references unknown meal_id "
                f"{sig['meal_id']!r} -- not in data['meals']"
            )
        if sig["person_id"] not in person_ids:
            raise ValueError(
                f"signal on {sig['on_date']!r} references unknown person_id "
                f"{sig['person_id']!r} -- not in data['members']"
            )

    for case in raw["drift_cases"]:
        if case["meal_id"] not in meal_ids:
            raise ValueError(
                f"drift case {case['case_id']!r} references unknown meal_id "
                f"{case['meal_id']!r} -- not in data['meals']"
            )
        if case["person_id"] not in person_ids:
            raise ValueError(
                f"drift case {case['case_id']!r} references unknown person_id "
                f"{case['person_id']!r} -- not in data['members']"
            )


def check_no_live_week_is_overwritten(repo: Repository, hid: str, raw: dict) -> None:
    """Raise, naming the week, if seeding would overwrite a week the
    household actually lived.

    put_week overwrites the item wholesale, so re-seeding a week that has
    been published replaces its real rationales and outcomes with
    "backfilled from memory", and resets notified_at -- which both loses
    hand-entered history that cannot be regenerated and re-arms the guard
    that stops the family being texted twice. The ledger actively instructs
    the owner to extend the backfill through August, which is exactly the
    edit that would do this.
    """
    for week in raw["history"]:
        week_start = date.fromisoformat(week["week_start"])
        existing = repo.get_week(hid, week_start)
        if existing is not None and existing.published_at is not None:
            raise ValueError(
                f"refusing to overwrite the live week {week['week_start']!r}: it "
                f"was published on {existing.published_at.isoformat()} and "
                f"re-seeding would replace its rationales and outcomes with "
                f"'backfilled from memory'"
                + (" and erase its notified_at stamp"
                   if existing.notified_at is not None else "")
                + f". Remove {week['week_start']!r} from the history in "
                f"data/household.json, or re-run with --force if you really "
                f"mean to replace a week the household lived."
            )


def check_no_phone_number_is_erased(repo: Repository, hid: str, raw: dict) -> None:
    """Raise, naming the person, if seeding would change or erase a phone
    number already in the table.

    A number added out of band -- typed into the console, or landed there by
    the SMS verification flow -- is the only thing that makes a member
    reachable, and this file has carried no phone fields at all. Re-seeding
    would silently un-reach them, and nothing would report it: notify_week
    counts a member with no address as skipped, not failed.

    Deliberately narrower than "refuse any member who has a phone": the
    documented setup flow puts the numbers *into* this file and re-seeds, so
    a blanket refusal would make --force part of the routine -- and the same
    --force disarms the week guard above, which protects history that cannot
    be regenerated. A flag the owner types every time is not a guard. This
    fires on exactly the harm: an incoming record that would change or drop
    a number already stored.
    """
    stored = {m.person_id: m for m in repo.list_members(hid)}
    for member in raw["members"]:
        existing = stored.get(member["person_id"])
        if existing is None or not existing.phone:
            continue
        if member.get("phone") != existing.phone:
            incoming = member.get("phone")
            raise ValueError(
                f"refusing to {'change' if incoming else 'erase'} the phone "
                f"number stored for member {member['person_id']!r}: the table "
                f"has one and data/household.json "
                + ("has a different one" if incoming else "has none")
                + f". Put the stored number in data/household.json for "
                f"{member['person_id']!r}, or re-run with --force."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load data/household.json into DynamoDB.")
    parser.add_argument(
        "--force", action="store_true",
        help=("overwrite weeks the household actually lived, and phone numbers "
              "already in the table. Destroys hand-entered history that cannot "
              "be regenerated -- type it deliberately, never habitually."),
    )
    args = parser.parse_args(argv)
    s = settings()
    raw = json.loads(DATA.read_text())
    hid = raw["household_id"]

    validate_household_data(raw)

    repo = Repository(table_name=s.table_name, region=s.aws_region)

    # Both checks run against the live table before anything is written, so a
    # refusal leaves the table exactly as it was rather than half-seeded.
    if not args.force:
        check_no_live_week_is_overwritten(repo, hid, raw)
        check_no_phone_number_is_erased(repo, hid, raw)

    for m in raw["members"]:
        repo.put_member(hid, Member(**m))
    for m in raw["meals"]:
        repo.put_meal(hid, Meal(**m))

    repo.put_fence(Fence(household_id=hid, rules=raw["fence"]))

    for w in raw["history"]:
        repo.put_week(Week(
            household_id=hid,
            week_start=date.fromisoformat(w["week_start"]),
            slots=[Slot(**{**slot, "rationale": "backfilled from memory",
                           "outcome": "made"}) for slot in w["slots"]],
        ))

    for sig in raw["signals"]:
        repo.put_signal(Signal(household_id=hid, **sig))

    # Drift cases are ground truth, not domain data -- they are written raw.
    # The computed key is spread last so it always wins, matching
    # Repository.put_eval_record's convention: a caller's payload must never
    # be able to decide where its own row lands in the table.
    for case in raw["drift_cases"]:
        repo._table.put_item(
            Item={**case, **keys.drift_case_key(hid, case["case_id"])}
        )

    print(f"Seeded {hid}: {len(raw['members'])} members, {len(raw['meals'])} meals, "
          f"{len(raw['history'])} weeks, {len(raw['signals'])} signals, "
          f"{len(raw['drift_cases'])} labelled drift cases.")
    if len(raw["drift_cases"]) == 0:
        print("WARNING: no drift cases labelled. The Curator scorecard "
              "(spec section 13) will have no ground truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
