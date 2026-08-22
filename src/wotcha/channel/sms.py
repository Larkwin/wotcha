import boto3

from wotcha.domain.models import Member


class SmsChannel:
    """Sends messages via AWS End User Messaging (pinpoint-sms-voice-v2)."""

    def __init__(self, origination_id: str, region: str) -> None:
        self._origination_id = origination_id
        self._region = region
        self._client = None

    def _lazy_client(self):
        """Create the boto3 client lazily to avoid requiring credentials at
        import time."""
        if self._client is None:
            self._client = boto3.client("pinpoint-sms-voice-v2",
                                        region_name=self._region)
        return self._client

    def _send_raw(self, number: str, body: str) -> None:
        """Low-level send to AWS. Wrapped so tests can inject a fake."""
        self._lazy_client().send_text_message(
            DestinationPhoneNumber=number,
            OriginationIdentity=self._origination_id,
            MessageBody=body,
            MessageType="TRANSACTIONAL",
        )

    def send(self, member: Member, body: str) -> bool:
        """Send an SMS. Returns True if sent, False if the member has no phone
        number."""
        if not member.phone:
            return False
        self._send_raw(member.phone, body)
        return True
