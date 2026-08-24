from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from wotcha import runtime
from wotcha.agents import context as agent_context
from wotcha.agents import planner_tools as pt
from wotcha.channel.console import ConsoleChannel
from wotcha.config import settings
from wotcha.domain.models import Meal, MealStatus, Member, Slot, Week
from wotcha.store.repo import Repository

HID, SECRET, BASE = "demo", "test-secret", "https://example.test"


# --- the household-local-date guard (correction 2) -------------------------

def test_local_today_uses_household_timezone_not_the_runtime_clock():
    """A UTC instant that has already rolled over to the next calendar day
    must still resolve to the household's own (earlier) local date. Toronto
    is UTC-4 in August (EDT): 02:30 UTC on the 21st is 22:30 local on the
    20th -- a different day. Getting this wrong means a late-evening run
    plans and publishes the wrong week with no error."""
    utc_instant = datetime(2026, 8, 21, 2, 30, tzinfo=UTC)
    assert runtime._local_today(utc_instant) == date(2026, 8, 20)


def test_local_today_matches_utc_date_when_the_offset_does_not_cross_midnight():
    utc_instant = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    assert runtime._local_today(utc_instant) == date(2026, 8, 20)


def test_next_monday_from_a_monday_skips_to_the_following_week():
    """Planning always looks ahead -- run on a Monday, the target is next
    Monday, not today."""
    assert runtime._next_monday(date(2026, 8, 24)) == date(2026, 8, 31)


def test_next_monday_from_mid_week():
    assert runtime._next_monday(date(2026, 8, 20)) == date(2026, 8, 24)  # Thu -> Mon


def test_target_week_honours_an_explicit_week_start():
    assert runtime._target_week({"week_start": "2026-09-07"}) == date(2026, 9, 7)


# --- dispatch ----------------------------------------------------------

@pytest.fixture(autouse=True)
def household_env(monkeypatch):
    monkeypatch.setenv("WOTCHA_HOUSEHOLD_ID", HID)
    monkeypatch.setenv("WOTCHA_BASE_URL", BASE)
    monkeypatch.setenv("WOTCHA_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("WOTCHA_AWS_REGION", "us-east-1")
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture
def seeded():
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
        r = Repository(table_name="wotcha-test", region="us-east-1")
        r.put_meal(HID, Meal(meal_id="chili", name="Chili", protein="beef",
                             effort_minutes=45, status=MealStatus.SAFE))
        r.put_member(HID, Member(person_id="alex", name="Alex",
                                 phone="+15195550101", is_cook=True))
        yield r


class FakeAgent:
    """Stands in for the real Strands Agent -- no Bedrock call."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, prompt: str) -> str:
        return "ready"


def test_ping_replies_without_touching_the_store(monkeypatch):
    monkeypatch.setattr(runtime, "Agent", FakeAgent)
    result = runtime.invoke({"action": "ping"})
    assert result == {"ok": True, "action": "ping", "reply": "ready"}


def test_plan_week_delegates_to_the_planner_and_does_not_notify(monkeypatch, seeded):
    calls = []

    def fake_run_planner(**kwargs):
        calls.append(kwargs)
        return {"published": True, "attempts": 1, "text": "Published."}

    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "run_planner", fake_run_planner)

    result = runtime.invoke({"action": "plan_week", "week_start": "2026-08-24"})

    assert result == {"ok": True, "action": "plan_week", "published": True,
                       "attempts": 1, "text": "Published."}
    assert calls[0]["household_id"] == HID
    assert calls[0]["week_start"] == date(2026, 8, 24)


def test_notify_messages_the_family_for_an_existing_week(monkeypatch, seeded):
    week = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        published_at=datetime.now(UTC),
        slots=[Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id="alex",
                    rationale="Cook once, eat twice.")],
    )
    seeded.put_week(week)

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)

    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24"})

    assert result == {"ok": True, "action": "notify", "notified": 1,
                       "week_start": "2026-08-24"}
    assert channel.sent[0][0] == "alex"


def test_notify_payload_force_reaches_notify_week(monkeypatch, seeded):
    """`force` must be reachable from the invoke payload, not just from a
    direct notify_week call -- otherwise a household that is genuinely
    re-notified on purpose (a corrected week) has no way to trigger it
    through the deployed entrypoint."""
    already_notified = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        published_at=datetime.now(UTC), notified_at=datetime.now(UTC),
        slots=[Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id="alex",
                    rationale="Cook once, eat twice.")],
    )
    seeded.put_week(already_notified)

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)

    # Without force: the idempotency guard refuses.
    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24"})
    assert result["notified"] == 0
    assert channel.sent == []

    # With force: it goes through.
    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24",
                             "force": True})
    assert result["notified"] == 1
    assert channel.sent[0][0] == "alex"


def test_notify_force_is_the_correct_retry_and_never_replans(monkeypatch, seeded):
    """README step 10's retry advice for `{"reason": "already_notified"}` is
    `{"action": "notify", "force": true}`, never `plan_and_notify` --
    `plan_and_notify` re-runs the Planner first, and the family would get a
    text about a plan nobody reviewed. This pins the claim: the stored week
    (plan A) is untouched and run_planner is never called, yet the guard is
    still cleared and the family is still messaged."""
    plan_a = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        published_at=datetime.now(UTC), notified_at=datetime.now(UTC),
        slots=[Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id="alex",
                    rationale="Plan A: cook once, eat twice.")],
    )
    seeded.put_week(plan_a)

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)

    def _replans_if_called(**kwargs):
        raise AssertionError("notify+force must not invoke the Planner")

    monkeypatch.setattr(runtime, "run_planner", _replans_if_called)

    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24",
                             "force": True})

    assert result["notified"] == 1
    assert channel.sent[0][0] == "alex"
    # The published week is exactly the one on record before the retry --
    # nothing replanned it.
    stored = seeded.get_week(HID, date(2026, 8, 24))
    assert stored.slots[0].rationale == "Plan A: cook once, eat twice."


class _UnreachableChannel:
    """Every send fails to dispatch -- e.g. a misconfigured channel with no
    verified destinations. Distinct from ConsoleChannel, which always
    succeeds."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, member, body) -> bool:
        return False


def test_notify_distinguishes_already_notified_from_nobody_reachable(
    monkeypatch, seeded,
):
    """{'notified': 0} is ambiguous on its own -- it means both 'the guard
    blocked a repeat' and 'it ran and reached nobody'. The operator running
    the first real send needs to tell these apart, and a run that reaches
    nobody must not permanently lock the week against a later genuine send."""
    week = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        published_at=datetime.now(UTC),
        slots=[Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id="alex",
                    rationale="Cook once, eat twice.")],
    )
    seeded.put_week(week)

    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: _UnreachableChannel())

    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24"})
    assert result["notified"] == 0
    assert result["reason"] == "no_reachable_members"
    # Not stamped -- a run that reached nobody must not block a future
    # genuine send the way an actual "already notified" week does.
    assert seeded.get_week(HID, date(2026, 8, 24)).notified_at is None

    # A later run on a real channel must still be free to go through.
    monkeypatch.setattr(runtime, "get_channel", lambda: ConsoleChannel())
    result2 = runtime.invoke({"action": "notify", "week_start": "2026-08-24"})
    assert result2["notified"] == 1
    assert "reason" not in result2


def test_notify_reports_no_published_week_rather_than_erroring(monkeypatch, seeded):
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: ConsoleChannel())

    result = runtime.invoke({"action": "notify", "week_start": "2026-08-24"})

    assert result == {"ok": False, "action": "notify", "reason": "no_published_week"}


def test_plan_and_notify_runs_both_steps_in_order(monkeypatch, seeded):
    def fake_run_planner(*, repo, household_id, week_start, model_id, region):
        repo.put_week(Week(
            household_id=household_id, week_start=week_start,
            published_at=datetime.now(UTC),
            slots=[Slot(on_date=week_start, meal_id="chili", cook_id="alex",
                        rationale="Cook once, eat twice.")],
        ))
        return {"published": True, "attempts": 1, "text": "Published."}

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", fake_run_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})

    assert result == {"ok": True, "action": "plan_and_notify", "notified": 1,
                       "week_start": "2026-08-24"}
    assert channel.sent[0][0] == "alex"


def _replanning_planner(repo, household_id, week_start, model_id, region):
    """A planner that really republishes through publish_plan -- the actual
    write path, not a hand-rolled put_week that could mimic a fix that isn't
    there. Every invocation composes a fresh (legal) week, exactly as a real
    replan does."""
    agent_context.set_context(repo=repo, household_id=household_id,
                              model_id=model_id)
    pt.publish_plan(week_start.isoformat(), [
        {"on_date": (week_start + timedelta(days=i)).isoformat(),
         "meal_id": "chili", "rationale": "Cook once, eat twice.", "claims": []}
        for i in range(7)
    ])
    published = agent_context.get_context().published
    return {"published": published, "attempts": 1, "text": "Published."}


def test_plan_and_notify_is_idempotent_across_repeated_retries(monkeypatch, seeded):
    """publish_plan builds a brand-new Week each time it runs (notified_at
    defaults to None) and repo.put_week overwrites the stored item wholesale.
    A second plan_and_notify for the same week -- exactly what an
    EventBridge Scheduler retry of a transiently failed target produces, and
    exactly what README step 10 tells the operator to run -- must not re-text
    the family just because the freshly republished week looks
    never-notified.

    Four invocations, not two: a guard that only reads the pre-planner
    snapshot blocks invocation 2 while the *stored* notified_at is being
    wiped underneath it, so invocation 3 sees an unnotified week and texts
    the household all over again. Two invocations cannot tell an idempotent
    guard from an oscillating one; four can."""
    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner",
                        lambda **kw: _replanning_planner(**kw))

    first = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert first["notified"] == 1
    assert len(channel.sent) == 1
    assert seeded.get_week(HID, date(2026, 8, 24)).notified_at is not None

    for n in range(2, 5):
        result = runtime.invoke({"action": "plan_and_notify",
                                 "week_start": "2026-08-24"})
        assert result["notified"] == 0, f"invocation {n} re-texted the household"
        assert result["reason"] == "already_notified", f"invocation {n}"
        assert len(channel.sent) == 1, f"invocation {n} sent an extra message"
        # The stored row must still carry the stamp. If a replan erases it,
        # the guard is merely blocked this time and re-armed for the next.
        stored = seeded.get_week(HID, date(2026, 8, 24))
        assert stored.notified_at is not None, (
            f"invocation {n} erased notified_at; the guard is now re-armed"
        )


def test_plan_and_notify_never_messages_the_family_about_an_unpublished_week(
    monkeypatch, seeded,
):
    """An escalated run must not fall through to notify -- there is nothing
    published to tell anyone about."""

    def fake_run_planner(**kwargs):
        return {"published": False, "attempts": 6, "text": "Escalated."}

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", fake_run_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})

    assert result["ok"] is False
    assert result["reason"] == "not_published"
    assert channel.sent == []


def test_plan_and_notify_sends_an_escalation_to_the_cook_and_nobody_else(
    monkeypatch, seeded,
):
    """The Planner correctly refusing an unsatisfiable fence used to end in
    silence: an ESCALATION# row was written, nothing read it, plan_and_notify
    returned not_published, and the household got no plan and no explanation.
    The question must reach the cook -- and only the cook, per spec section 5
    ("Decision SMS to the cook only")."""
    seeded.put_member(HID, Member(person_id="riley", name="Riley",
                                  phone="+15195550112", is_cook=False))

    def escalating_planner(*, repo, household_id, week_start, model_id, region):
        agent_context.set_context(repo=repo, household_id=household_id,
                                  model_id=model_id, week_start=week_start)
        pt.escalate("fence_unsatisfiable",
                    "Flat Sushi Day and the chicken target collide. Which gives?")
        return {"published": False, "escalated": True, "attempts": 6,
                "text": "Escalated."}

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", escalating_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})

    assert result["ok"] is False
    assert result["reason"] == "not_published"
    assert result["escalated_to"] == 1
    assert [p for p, _ in channel.sent] == ["alex"]  # the cook, not Riley
    body = channel.sent[0][1]
    assert "Which gives?" in body
    assert "Aug 24" in body           # names the week that has no plan
    assert body.isascii()             # one GSM-7 segment budget, not UCS-2


def test_an_escalation_already_sent_is_not_sent_again(monkeypatch, seeded):
    """Schedulers retry. A cook asked the same question on every retry stops
    reading the messages -- the same reasoning as Week.notified_at."""
    def escalating_planner(*, repo, household_id, week_start, model_id, region):
        agent_context.set_context(repo=repo, household_id=household_id,
                                  model_id=model_id, week_start=week_start)
        pt.escalate("fence_unsatisfiable", "Which tradition gives?")
        return {"published": False, "escalated": True, "attempts": 6,
                "text": "Escalated."}

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", escalating_planner)

    first = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert first["escalated_to"] == 1
    assert len(channel.sent) == 1

    # A replan raises the question again as a brand-new row, so "has *this*
    # row been sent" would not catch it. The cook has already been asked
    # about this week and has not answered.
    second = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert second["escalated_to"] == 0
    assert len(channel.sent) == 1, "the cook is being re-asked on every retry"


def test_unknown_action_fails_loudly(seeded, monkeypatch):
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    result = runtime.invoke({"action": "levitate"})
    assert result == {"ok": False, "error": "unknown action: levitate"}


# --- which open question the cook is handed ------------------------------


def _open_row(reason: str, question: str, ts: str, week_start):
    return {"record_id": ts[-4:], "timestamp": ts, "reason": reason,
            "question": question, "resolved": False, "week_start": week_start}


def _failing_planner(*, repo, household_id, week_start, model_id, region):
    """A run that publishes nothing and escalates nothing -- an exhausted
    attempt cap, or a crash inside the agent loop. This is the only path on
    which the fallback below is reachable."""
    return {"published": False, "escalated": False, "attempts": 6, "text": "gave up"}


def test_a_stale_retirement_question_is_not_sent_as_this_weeks(monkeypatch, seeded):
    """Retirement questions stay open permanently by design -- nothing in the
    system can know one was settled. So the fallback that used to reach for
    *any* open row would hand the cook a months-old question about tacos when
    a run failed to publish without escalating, labelled with a week that has
    nothing to do with it."""
    seeded.put_escalation(HID, _open_row("retirement", "Retire tacos?",
                                         "2026-06-01T09:00:00+00:00", "2026-06-01"))
    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", _failing_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert result["escalated_to"] == 0
    assert channel.sent == [], "an old retirement question was sent as this week's"


def test_a_question_naming_no_week_is_still_reachable(monkeypatch, seeded):
    """`escalate` records ctx.week_start, which can be None. Those rows are
    what the fallback exists for -- narrowing it must not silence them, or a
    real question would go nowhere."""
    seeded.put_escalation(HID, _open_row("fence_unsatisfiable", "Which rule gives?",
                                         "2026-08-22T09:00:00+00:00", None))
    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", _failing_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert result["escalated_to"] == 1
    assert len(channel.sent) == 1


def test_this_weeks_question_still_wins_over_a_weekless_one(monkeypatch, seeded):
    """Both kinds open at once: the one about the week being planned is the
    one the cook needs today."""
    seeded.put_escalation(HID, _open_row("fence_unsatisfiable", "Old weekless?",
                                         "2026-08-01T09:00:00+00:00", None))
    seeded.put_escalation(HID, _open_row("fence_unsatisfiable", "This week?",
                                         "2026-08-22T09:00:00+00:00", "2026-08-24"))
    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", _failing_planner)

    runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert "This week?" in channel.sent[0][1]


def test_a_resolved_week_frees_the_cook_from_being_reasked(monkeypatch, seeded):
    """The end-to-end shape. A week escalates and the cook is told; the fence
    is fixed and the week publishes, which answers it; a later unsatisfiable
    week asks a fresh question rather than being suppressed by the old
    already-notified row."""
    seeded.put_escalation(HID, _open_row("fence_unsatisfiable", "Week one?",
                                         "2026-08-15T09:00:00+00:00", "2026-08-17"))
    row = seeded.unresolved_escalations(HID)[0]
    seeded.mark_escalation_notified(HID, row["sk"])
    seeded.put_week(Week(household_id=HID, week_start=date(2026, 8, 17),
                         published_at=datetime(2026, 8, 15, tzinfo=UTC)))
    assert seeded.unresolved_escalations(HID) == []

    def escalating_planner(*, repo, household_id, week_start, model_id, region):
        agent_context.set_context(repo=repo, household_id=household_id,
                                  model_id=model_id, week_start=week_start)
        pt.escalate("fence_unsatisfiable", "Week two?")
        return {"published": False, "escalated": True, "attempts": 6, "text": "no"}

    channel = ConsoleChannel()
    monkeypatch.setattr(runtime, "_repo", lambda: seeded)
    monkeypatch.setattr(runtime, "_secret", lambda: SECRET)
    monkeypatch.setattr(runtime, "get_channel", lambda: channel)
    monkeypatch.setattr(runtime, "run_planner", escalating_planner)

    result = runtime.invoke({"action": "plan_and_notify", "week_start": "2026-08-24"})
    assert result["escalated_to"] == 1
    assert "Week two?" in channel.sent[0][1]
