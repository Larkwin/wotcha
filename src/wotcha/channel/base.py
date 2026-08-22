from typing import Protocol

from wotcha.domain.models import Member


class Channel(Protocol):
    def send(self, member: Member, body: str) -> bool:
        """Deliver one message. Returns True if a message was dispatched, False if
        the member has no address on this channel.

        Silently skipping a member who has no address on this channel is correct:
        the family is reachable by different routes and a missing phone number or
        email address is a fact, not a failure."""
        ...
