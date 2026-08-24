"""AgentCore Runtime entrypoint.

One container, dispatched on payload["action"]:
  ping              health check
  plan_week         run the Planner for the coming week
  notify            message the family about a published week
  plan_and_notify   both, in order -- what the weekly schedule invokes
"""
from datetime import date

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

from wotcha.agents.planner import plan_week as run_planner
from wotcha.channel import get_channel
from wotcha.config import settings
from wotcha.dates import local_today, next_monday
from wotcha.notify import notify_escalation, notify_week
from wotcha.store.repo import Repository

app = BedrockAgentCoreApp()

# The household calendar lives in wotcha.dates, shared with the web page --
# the module that decides which week to PLAN and the module that decides
# which week to RENDER must agree about what day it is, and they did not.
# Kept as module-level names because that is how this module already refers
# to them.
_local_today = local_today
_next_monday = next_monday


def _repo() -> Repository:
    s = settings()
    return Repository(table_name=s.table_name, region=s.aws_region)


def _secret() -> str:
    s = settings()
    client = boto3.client("secretsmanager", region_name=s.aws_region)
    return client.get_secret_value(SecretId=s.link_secret_name)["SecretString"]


def _target_week(payload: dict) -> date:
    given = payload.get("week_start")
    return date.fromisoformat(given) if given else _next_monday(_local_today())


def _tell_the_cook(repo: Repository, household_id: str, week_start: date) -> int:
    """Send the newest unsettled decision to the cooks. Returns how many
    messages went out -- 0 when there is nothing waiting, or when the cook
    has already been asked about this week.

    Keyed off the stored rows, never off the run's own `escalated` flag: a
    question raised by an earlier run and still unanswered is exactly as
    unanswered today, and this is the only moment anyone looks.

    A replan raises the question again, as a fresh row, every single time it
    runs -- so "has this row been sent" is not enough of a guard. If any open
    escalation about this same week has already gone out, the cook has been
    asked and has not yet answered; asking again on every scheduler retry is
    how a person learns to ignore the messages.
    """
    open_rows = repo.unresolved_escalations(household_id)
    same_week = [r for r in open_rows if r.get("week_start") == week_start.isoformat()]
    # The fallback is for rows that name no week at all -- `escalate` records
    # `ctx.week_start`, which can be None -- and only those. It used to fall
    # back to every open row, which was harmless while a published week left
    # its question open anyway. It is not harmless now: `retirement` and
    # `new_constraint` questions stay open permanently by design (nothing can
    # know they were settled), so the old fallback would hand the cook a
    # months-old retirement question on any run that failed to publish
    # without escalating -- an exhausted attempt cap, a crash -- labelled with
    # the wrong week.
    weekless = [r for r in open_rows if not r.get("week_start")]
    rows = same_week or weekless
    if not rows or any(r.get("notified_at") for r in rows):
        return 0
    return notify_escalation(repo, household_id, rows[0], get_channel())


@app.entrypoint
def invoke(payload: dict) -> dict:
    s = settings()
    action = payload.get("action", "ping")

    if action == "ping":
        agent = Agent(
            model=BedrockModel(model_id=s.bedrock_model_id, region_name=s.aws_region),
            system_prompt="You are Wotcha, a household dinner agent.",
        )
        return {"ok": True, "action": "ping", "reply": str(agent("Reply: ready")).strip()}

    repo, week_start = _repo(), _target_week(payload)
    force = bool(payload.get("force"))

    # Captured *before* the Planner runs. publish_plan builds a brand new
    # Week (notified_at defaults to None) and repo.put_week overwrites the
    # stored item wholesale, so re-fetching only afterward would read back a
    # week that looks never-notified even when it was -- erasing this task's
    # own idempotency guard on exactly the retry it exists to survive
    # (EventBridge Scheduler retries a failed target by default, and
    # plan_and_notify is what the weekly schedule invokes).
    prior = repo.get_week(s.household_id, week_start)

    if action in ("plan_week", "plan_and_notify"):
        result = run_planner(
            repo=repo, household_id=s.household_id, week_start=week_start,
            model_id=s.bedrock_model_id, region=s.aws_region,
        )
        if action == "plan_week":
            return {"ok": True, "action": action, **result}
        if not result["published"]:
            # Never message the family about a week that does not exist -- but
            # somebody does have to be told. A Planner that correctly refuses
            # an unsatisfiable fence used to end here: an ESCALATION# row was
            # written, nothing ever read it, and the household simply got no
            # plan and no explanation. The question goes to the cooks, on the
            # same channel, per spec section 5 ("Decision SMS to the cook
            # only").
            told = _tell_the_cook(repo, s.household_id, week_start)
            return {"ok": False, "action": action, "reason": "not_published",
                    **result, "escalated_to": told}

    if action in ("notify", "plan_and_notify"):
        week = repo.get_week(s.household_id, week_start)
        if week is None:
            return {"ok": False, "action": action, "reason": "no_published_week"}
        if prior is not None and prior.notified_at is not None and not force:
            # The pre-planner snapshot is the only trustworthy record of
            # whether this week was already sent -- the freshly republished
            # `week` above always looks unnotified, guard or no guard.
            return {"ok": True, "action": action, "notified": 0,
                    "reason": "already_notified", "week_start": week_start.isoformat()}
        count = notify_week(repo, s.household_id, week, get_channel(),
                            s.base_url, _secret(), force=force)
        result = {"ok": True, "action": action, "notified": count,
                  "week_start": week_start.isoformat()}
        if count == 0:
            # Distinguish "blocked by the guard" (above, reason
            # already_notified) from "ran, but nobody was reachable" -- the
            # operator running the first real send needs to tell these apart.
            result["reason"] = "no_reachable_members"
        return result

    return {"ok": False, "error": f"unknown action: {action}"}


if __name__ == "__main__":
    app.run()
