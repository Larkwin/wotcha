"""Ambient context for tool functions.

Strands tools are plain functions with introspected signatures, so the
repository cannot be passed as an argument without leaking it into the model's
tool schema. A module-level context keeps the schemas clean and the tests
simple: set it, call tools, assert.
"""
from dataclasses import dataclass
from datetime import date

from wotcha.store.repo import Repository


@dataclass
class Context:
    repo: Repository
    household_id: str
    model_id: str
    attempt: int = 0
    max_attempts: int = 6
    # Set True only by publish_plan's success path. plan_week reads this
    # rather than re-querying the store for a week row: a row can exist from
    # a prior run (yesterday's publish, a stale retry), so its mere presence
    # is not evidence that *this* invocation published anything. Only the
    # tool call itself is.
    published: bool = False
    # Set True only by escalate. Read back by plan_week so the caller can tell
    # "the Planner handed a decision to the cook" apart from "the run failed
    # for some other reason" -- the two need entirely different responses, and
    # only one of them has a question waiting for a human.
    escalated: bool = False
    # The week being planned. Recorded on an escalation so the message to the
    # cook can name the week that has no plan, rather than asking them to
    # infer it.
    week_start: date | None = None


_context: Context | None = None


def set_context(
    repo: Repository,
    household_id: str,
    model_id: str,
    max_attempts: int = 6,
    week_start: date | None = None,
) -> None:
    global _context
    _context = Context(
        repo=repo, household_id=household_id, model_id=model_id,
        max_attempts=max_attempts, week_start=week_start,
    )


def get_context() -> Context:
    if _context is None:
        raise RuntimeError("agent context not set; call set_context() first")
    return _context
