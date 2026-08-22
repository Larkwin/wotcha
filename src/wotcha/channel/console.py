from wotcha.domain.models import Member


class ConsoleChannel:
    """Prints instead of sending. Used in tests and for dry runs before a real
    week goes out to real phones."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, member: Member, body: str) -> bool:
        """Record the message to sent list and print it. Always returns True since
        console output is always available."""
        self.sent.append((member.person_id, body))
        print(f"--- to {member.name} ({member.person_id}) ---\n{body}\n")
        return True
