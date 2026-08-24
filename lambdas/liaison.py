"""SQS target: one inbound text becomes one suggestion for the cook.

A thin Lambda, like scheduler.py -- the logic worth testing lives in
`wotcha.inbound` and `wotcha.agents.liaison`, and this wires them to the
store.

Nothing here writes to the roster, the fence, or a published week. The most
this can do is add a row the cook may decline, which is the whole design: the
buck stops with the chef.
"""
import os
import uuid
from datetime import UTC, datetime

from wotcha.agents.liaison import read_message
from wotcha.domain.models import Suggestion
from wotcha.inbound import parse_sqs_record
from wotcha.store.repo import Repository


def handle_record(
    repo: Repository, household_id: str, record: dict, model_id: str, region: str
) -> Suggestion | None:
    """One SQS record to at most one stored suggestion.

    Returns None for anything not worth storing, and never raises for bad
    input: a record that throws would be redelivered five times and then
    dead-lettered, while blocking nothing useful.
    """
    message = parse_sqs_record(record)
    if message is None:
        return None

    # Resolved before the model is asked, deliberately. A dropped message must
    # not cost a Bedrock call -- otherwise anyone who knows the long code can
    # run up the bill by texting it, and the number is not a secret.
    member = repo.member_by_phone(household_id, message.from_number)
    if member is None:
        return None

    # If this inboundMessageId has already been written, this message has
    # already been processed -- return that row unchanged rather than
    # rebuilding one. `Suggestion` defaults a new row's `status` to PENDING,
    # and `put_suggestion` overwrites the item wholesale, so without this
    # check an SQS redelivery (or a DLQ redrive) would silently reset
    # whatever the cook had already decided. That mirrors why publish_plan
    # (agents/planner_tools.py) reads the prior week before writing: a
    # second write from the same at-least-once delivery must never erase a
    # decision the first write's outcome already carries. Checking here also
    # keeps a redelivery from costing a second Bedrock call and a second
    # eval record, for the same reason the sender is resolved before the
    # model is asked at all.
    prior = repo.get_suggestion(household_id, message.received_at.isoformat(),
                                message.message_id)
    if prior is not None:
        return prior

    meals = repo.list_meals(household_id)
    read = read_message(message.body, meals, model_id=model_id, region=region)

    suggestion = Suggestion(
        household_id=household_id,
        suggestion_id=message.message_id,
        person_id=member.person_id,
        text=message.body,
        created_at=message.received_at,
        kind=read.kind,
        matched_meal_id=read.matched_meal_id,
        proposed_name=read.proposed_name,
        proposed_tags=read.proposed_tags,
        note=read.note,
    )
    repo.put_suggestion(household_id, suggestion)

    # The Liaison's contribution to the replay corpus (spec section 13).
    # Written for every read including failed ones: a model that could not be
    # reached is a reliability signal about that model, and a corpus with only
    # the successes overstates every rung in it.
    repo.put_eval_record(household_id, {
        "record_id": uuid.uuid4().hex,
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": "extraction",
        "model_id": model_id,
        "suggestion_id": message.message_id,
        "read_kind": read.kind.value,
        "matched": read.matched_meal_id,
        "proposed_name": read.proposed_name,
    })
    return suggestion


def handler(event, _context) -> dict:
    repo = Repository(
        table_name=os.environ["WOTCHA_TABLE_NAME"],
        region=os.environ["WOTCHA_AWS_REGION"],
    )
    household_id = os.environ["WOTCHA_HOUSEHOLD_ID"]
    model_id = os.environ.get("WOTCHA_LIAISON_MODEL_ID", "ca.amazon.nova-lite-v1:0")
    region = os.environ["WOTCHA_AWS_REGION"]

    stored = 0
    # No per-record try/except here. The event source mapping (Task 7) is
    # configured with batch_size=1, so "the whole batch" this loop can fail
    # is one record -- an unhandled exception fails that one invocation and
    # SQS redelivers it, which is correct: a transient DynamoDB error should
    # be retried, not swallowed. A try/except that logged and continued would
    # trade a retryable failure for a silently dropped message, which is
    # strictly worse than the retry storm it would be avoiding.
    for record in event.get("Records", []):
        if handle_record(repo, household_id, record, model_id, region) is not None:
            stored += 1
    print(f"inbound records={len(event.get('Records', []))} stored={stored}")
    return {"ok": True, "stored": stored}
