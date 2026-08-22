import boto3

from wotcha.domain.models import Member


class EmailChannel:
    """The fallback when carriers filter SMS (spec section 12). Same interface,
    same message body -- switching is a configuration change."""

    def __init__(self, from_address: str, region: str) -> None:
        self._from = from_address
        self._region = region
        self._client = None

    def _lazy_client(self):
        """Create the boto3 client lazily to avoid requiring credentials at
        import time."""
        if self._client is None:
            self._client = boto3.client("ses", region_name=self._region)
        return self._client

    def _send_raw(self, address: str, body: str) -> None:
        """Low-level send to AWS. Wrapped so tests can inject a fake."""
        self._lazy_client().send_email(
            Source=self._from,
            Destination={"ToAddresses": [address]},
            Message={"Subject": {"Data": "Wotcha"},
                     "Body": {"Text": {"Data": body}}},
        )

    def send(self, member: Member, body: str) -> bool:
        """Send an email. Returns True if sent, False if the member has no email
        address."""
        if not member.email:
            return False
        self._send_raw(member.email, body)
        return True
