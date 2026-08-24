"""The inbound consumer: envelope to row.

The agent is faked throughout. What matters here is the pipeline around it --
who gets dropped, what gets written, and that a redelivery does not double up.
"""
import json

import boto3
import liaison as consumer
import pytest
from moto import mock_aws

import wotcha.agents.liaison as agent_liaison
from wotcha.agents.liaison import LiaisonRead
from wotcha.domain.models import Meal, MealStatus, Member, SuggestionKind, SuggestionStatus
from wotcha.store.repo import Repository

HID = "demo"


@pytest.fixture
def seeded(monkeypatch):
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
        r.put_member(HID, Member(person_id="riley", name="Riley",
                                 phone="+15195550112"))
        r.put_meal(HID, Meal(meal_id="tacos", name="Tacos", effort_minutes=30,
                             status=MealStatus.SAFE))
        monkeypatch.setattr(
            consumer, "read_message",
            lambda *a, **k: LiaisonRead(kind=SuggestionKind.NEW_MEAL,
                                        proposed_name="Poutine"),
        )
        yield r


def _record(number="+15195550112", body="can we have poutine",
            mid="m1", ts="2026-08-24T18:03:00.000Z") -> dict:
    payload = {"originationNumber": number, "destinationNumber": "+15195550199",
               "messageBody": body, "inboundMessageId": mid}
    return {"body": json.dumps({"Timestamp": ts, "Message": json.dumps(payload)})}


def test_a_known_sender_gets_a_suggestion_row(seeded):
    consumer.handle_record(seeded, HID, _record(), "test-model", "us-east-1")
    rows = seeded.list_suggestions(HID)
    assert len(rows) == 1
    assert rows[0].person_id == "riley"
    assert rows[0].proposed_name == "Poutine"


def test_the_stored_text_is_what_was_actually_sent(seeded):
    """The agent proposed "Poutine". The row must still carry the words the
    person typed -- the cook is deciding about those, not about a paraphrase."""
    consumer.handle_record(seeded, HID, _record(body="can we PLEASE have poutine"),
                           "test-model", "us-east-1")
    assert seeded.list_suggestions(HID)[0].text == "can we PLEASE have poutine"


def test_an_unknown_number_writes_nothing(seeded):
    """Anyone who knows the long code can text it. No member means no
    identity, and an identity-less row is not one the household can act on."""
    result = consumer.handle_record(seeded, HID, _record(number="+15195550999"),
                                    "test-model", "us-east-1")
    assert result is None
    assert seeded.list_suggestions(HID) == []


def test_a_malformed_record_writes_nothing_and_does_not_raise(seeded):
    assert consumer.handle_record(seeded, HID, {"body": "junk"},
                                  "test-model", "us-east-1") is None
    assert seeded.list_suggestions(HID) == []


def test_a_redelivery_does_not_double_up(seeded):
    """SQS Standard is at-least-once. The family must see one card."""
    rec = _record()
    consumer.handle_record(seeded, HID, rec, "test-model", "us-east-1")
    consumer.handle_record(seeded, HID, rec, "test-model", "us-east-1")
    assert len(seeded.list_suggestions(HID)) == 1


def test_every_read_is_logged_for_the_eval_corpus(seeded):
    """Only the Planner writes eval records today, so the Minimum Viable
    Model study has one task type in it. The Liaison is structurally
    different work and the corpus needs it from the first message."""
    consumer.handle_record(seeded, HID, _record(), "test-model", "us-east-1")
    records = seeded._query_prefix(HID, "EVAL#")
    assert len(records) == 1
    assert records[0]["kind"] == "extraction"
    assert records[0]["model_id"] == "test-model"


def test_the_agent_is_not_asked_about_an_unknown_sender(seeded, monkeypatch):
    """A dropped message must not cost a Bedrock call. Otherwise anyone with
    the number can run up the bill by texting it."""
    called = []
    monkeypatch.setattr(consumer, "read_message",
                        lambda *a, **k: called.append(1))
    consumer.handle_record(seeded, HID, _record(number="+15195550999"),
                           "test-model", "us-east-1")
    assert called == []


def test_a_redelivery_after_a_status_change_leaves_it_untouched(seeded, monkeypatch):
    """Nothing writes APPROVED yet -- Task 6 does -- but a DLQ redrive must
    not be able to un-decide something the cook already decided, and must
    not cost a second Bedrock call for a message already on file."""
    rec = _record()
    consumer.handle_record(seeded, HID, rec, "test-model", "us-east-1")
    approved = seeded.list_suggestions(HID)[0].model_copy(
        update={"status": SuggestionStatus.APPROVED})
    seeded.put_suggestion(HID, approved)

    called = []
    monkeypatch.setattr(consumer, "read_message",
                        lambda *a, **k: called.append(1))
    consumer.handle_record(seeded, HID, rec, "test-model", "us-east-1")

    assert seeded.list_suggestions(HID)[0].status == SuggestionStatus.APPROVED
    assert called == []


def test_a_match_with_a_name_reaches_the_eval_corpus_as_existing_meal(seeded, monkeypatch):
    """M1: agents.liaison.read_message normalises a valid match plus a
    proposed_name by dropping proposed_name and (as of this fix) setting
    kind=EXISTING_MEAL. Before the fix, kind was left as whatever the model
    said -- so a NEW_MEAL classification survived untouched while
    proposed_name was cleared, and handle_record's put_eval_record wrote
    read_kind="new_meal" beside matched="tacos" and proposed_name=None into
    the eval corpus extraction_accuracy is measured from: neither what the
    model said (which also had a name) nor what was actually stored (no
    name). This exercises the real read_message, not the module-level fake
    the `seeded` fixture installs, so the normalisation logic actually runs.
    """
    monkeypatch.setattr(consumer, "read_message", agent_liaison.read_message)
    monkeypatch.setattr(
        agent_liaison, "_structured_read",
        lambda *a, **k: LiaisonRead(kind=SuggestionKind.NEW_MEAL,
                                    matched_meal_id="tacos",
                                    proposed_name="Tacos Supreme", note="x"),
    )

    consumer.handle_record(seeded, HID, _record(body="tacos please"),
                           "test-model", "us-east-1")

    rows = seeded.list_suggestions(HID)
    assert rows[0].kind == SuggestionKind.EXISTING_MEAL
    assert rows[0].matched_meal_id == "tacos"
    assert rows[0].proposed_name is None

    records = seeded._query_prefix(HID, "EVAL#")
    assert records[0]["read_kind"] == "existing_meal"
    assert records[0]["matched"] == "tacos"
    assert records[0]["proposed_name"] is None


def test_a_failed_read_still_stores_the_text_and_logs_an_eval_record(seeded, monkeypatch):
    """read_message never raises -- it degrades to UNKNOWN on a model
    failure -- but that degraded read must still produce a row and an eval
    record, because an unreachable model is itself a reliability signal."""
    monkeypatch.setattr(
        consumer, "read_message",
        lambda *a, **k: LiaisonRead(
            kind=SuggestionKind.UNKNOWN,
            note="This message could not be read automatically (Exception).",
        ),
    )
    consumer.handle_record(seeded, HID, _record(body="can we have poutine"),
                           "test-model", "us-east-1")

    rows = seeded.list_suggestions(HID)
    assert len(rows) == 1
    assert rows[0].text == "can we have poutine"
    assert rows[0].kind == SuggestionKind.UNKNOWN

    records = seeded._query_prefix(HID, "EVAL#")
    assert len(records) == 1
    assert records[0]["kind"] == "extraction"
