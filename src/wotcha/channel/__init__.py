import os

from wotcha.channel.base import Channel
from wotcha.channel.console import ConsoleChannel
from wotcha.channel.email import EmailChannel
from wotcha.channel.sms import SmsChannel

__all__ = ["Channel", "ConsoleChannel", "EmailChannel", "SmsChannel", "get_channel"]


def get_channel() -> Channel:
    """Select the delivery channel from configuration. Nothing above this
    function knows which one it got.

    Defaults to console. Reaching a real phone must require a deliberate
    override, never an omission -- this is a standing safety rule."""
    name = os.environ.get("WOTCHA_CHANNEL", "console")
    region = os.environ.get("WOTCHA_AWS_REGION", "ca-central-1")
    if name == "console":
        return ConsoleChannel()
    if name == "sms":
        return SmsChannel(
            origination_id=os.environ["WOTCHA_SMS_ORIGINATION_ID"], region=region
        )
    if name == "email":
        return EmailChannel(from_address=os.environ["WOTCHA_EMAIL_FROM"], region=region)
    raise ValueError(f"unknown WOTCHA_CHANNEL: {name!r}")
