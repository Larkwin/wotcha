"""Turning an SQS record into something the rest of the system can use.

Everything here is untrusted: the shape is AWS's, but the contents came off a
carrier network and ultimately from a person. A malformed record must produce
a skip, never an exception -- a consumer that raises on one bad message stops
processing the good ones behind it.
"""
import json
from datetime import UTC, datetime

from wotcha.inbound import parse_sqs_record

PAYLOAD = {
    "originationNumber": "+15195550111",
    "destinationNumber": "+15195550199",
    "messageKeyword": "KEYWORD_None",
    "messageBody": "can we have poutine on thursday",
    "inboundMessageId": "cae173d2-66b9-564c-8309-21f858e9fb84",
    "previousPublishedMessageId": "abc",
}


def _record(payload=None, timestamp="2026-08-24T18:03:00.000Z") -> dict:
    envelope = {
        "Type": "Notification",
        "MessageId": "sns-envelope-id",
        "Timestamp": timestamp,
        "Message": json.dumps(PAYLOAD if payload is None else payload),
    }
    return {"body": json.dumps(envelope)}


def test_a_well_formed_record_parses():
    msg = parse_sqs_record(_record())
    assert msg.from_number == "+15195550111"
    assert msg.body == "can we have poutine on thursday"
    assert msg.message_id == "cae173d2-66b9-564c-8309-21f858e9fb84"


def test_the_timestamp_comes_from_the_sns_envelope():
    """The SMS payload has no time field of its own -- that is the whole
    reason RawMessageDelivery is off. The envelope timestamp is what makes
    the storage key deterministic across redeliveries."""
    msg = parse_sqs_record(_record())
    assert msg.received_at == datetime(2026, 8, 24, 18, 3, tzinfo=UTC)


def test_a_record_that_is_not_json_is_skipped_not_raised():
    assert parse_sqs_record({"body": "not json at all"}) is None


def test_a_raw_delivery_record_is_skipped_not_raised():
    """If RawMessageDelivery is ever switched on, records arrive without the
    envelope. That is a misconfiguration, and it must show up as skipped
    messages rather than a Lambda that crashes on every single one."""
    assert parse_sqs_record({"body": json.dumps(PAYLOAD)}) is None


def test_a_record_missing_the_sender_is_skipped():
    payload = {k: v for k, v in PAYLOAD.items() if k != "originationNumber"}
    assert parse_sqs_record(_record(payload)) is None


def test_a_record_with_an_empty_body_is_skipped():
    assert parse_sqs_record(_record({**PAYLOAD, "messageBody": "   "})) is None


def test_a_record_with_an_unparseable_timestamp_is_skipped():
    assert parse_sqs_record(_record(timestamp="not-a-time")) is None


def test_a_record_whose_body_is_not_a_string_is_skipped():
    """messageBody arriving as a JSON number or null is malformed, not a
    message. It must skip like any other bad record -- a consumer that raises
    on one stops processing every good message queued behind it."""
    assert parse_sqs_record(_record({**PAYLOAD, "messageBody": 42})) is None
