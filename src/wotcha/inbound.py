"""The inbound half of the family's channel.

`notify.py` composes what goes out; this reads what comes back. Everything
here is untrusted -- the envelope shape is AWS's, but the contents crossed a
carrier network and originated with a person who may be trying to game the
system.

Nothing in this module raises. A malformed record returns None and the caller
skips it, because a consumer that throws on one bad message stops processing
every good message queued behind it -- and SQS will then redeliver the bad one
five times before parking it in the dead-letter queue.
"""
import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class InboundMessage:
    """One text, from one handset, at one moment."""

    from_number: str
    body: str
    message_id: str
    # From the SNS envelope, never `now()`. The SMS payload carries no time
    # field at all, and this value becomes part of the storage key -- so a
    # redelivery has to compute the same one.
    received_at: datetime


def parse_sqs_record(record: dict) -> InboundMessage | None:
    """One SQS record to one message, or None if it is not usable.

    The record's `body` is an SNS notification envelope whose `Message` is the
    End User Messaging payload as a JSON string. That double encoding is what
    `RawMessageDelivery=false` buys, and it is bought deliberately: the
    envelope's `Timestamp` is the only clock in the whole delivery.
    """
    try:
        envelope = json.loads(record.get("body") or "")
        payload = json.loads(envelope["Message"])
        # Z is not an offset Python's fromisoformat accepted before 3.11, and
        # the floor here is 3.12 -- which accepts it natively.
        received_at = datetime.fromisoformat(
            envelope["Timestamp"]
        ).astimezone(UTC)
        from_number = payload["originationNumber"]
        body = payload["messageBody"]
        message_id = payload["inboundMessageId"]
        if not from_number or not body.strip() or not message_id:
            return None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return None
    return InboundMessage(
        from_number=from_number,
        body=body,
        message_id=message_id,
        received_at=received_at,
    )
