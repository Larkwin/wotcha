import pytest

from wotcha.channel import get_channel
from wotcha.channel.console import ConsoleChannel
from wotcha.channel.email import EmailChannel
from wotcha.channel.sms import SmsChannel
from wotcha.domain.models import Member

TEXTER = Member(person_id="maya", name="Maya", phone="+15195550111")
EMAILER = Member(person_id="sam", name="Sam", email="sam@example.com")


def test_console_channel_records_what_it_would_send():
    ch = ConsoleChannel()
    result = ch.send(TEXTER, "Week's up.")
    assert result is True
    assert ch.sent == [("maya", "Week's up.")]


def test_sms_channel_returns_true_when_sending():
    sent = []
    ch = SmsChannel(origination_id="x", region="us-east-1")
    ch._send_raw = lambda number, body: sent.append((number, body))
    result = ch.send(TEXTER, "hello")
    assert result is True
    assert sent == [("+15195550111", "hello")]


def test_sms_channel_returns_false_and_skips_members_without_a_phone():
    sent = []
    ch = SmsChannel(origination_id="x", region="us-east-1")
    ch._send_raw = lambda number, body: sent.append((number, body))
    result = ch.send(EMAILER, "hello")
    assert result is False
    assert sent == []


def test_email_channel_returns_true_when_sending():
    sent = []
    ch = EmailChannel(from_address="wotcha@example.com", region="us-east-1")
    ch._send_raw = lambda address, body: sent.append((address, body))
    result = ch.send(EMAILER, "hello")
    assert result is True
    assert sent == [("sam@example.com", "hello")]


def test_email_channel_returns_false_and_skips_members_without_an_email():
    sent = []
    ch = EmailChannel(from_address="wotcha@example.com", region="us-east-1")
    ch._send_raw = lambda address, body: sent.append((address, body))
    result = ch.send(TEXTER, "hello")
    assert result is False
    assert sent == []


def test_channel_selection_is_configuration_not_code(monkeypatch):
    monkeypatch.setenv("WOTCHA_CHANNEL", "console")
    assert isinstance(get_channel(), ConsoleChannel)
    monkeypatch.setenv("WOTCHA_CHANNEL", "sms")
    monkeypatch.setenv("WOTCHA_SMS_ORIGINATION_ID", "abc")
    assert isinstance(get_channel(), SmsChannel)
    monkeypatch.setenv("WOTCHA_CHANNEL", "email")
    monkeypatch.setenv("WOTCHA_EMAIL_FROM", "wotcha@example.com")
    assert isinstance(get_channel(), EmailChannel)


def test_unknown_channel_fails_loudly(monkeypatch):
    monkeypatch.setenv("WOTCHA_CHANNEL", "carrier-pigeon")
    with pytest.raises(ValueError):
        get_channel()


def test_get_channel_defaults_to_console_when_unset(monkeypatch):
    """Safety requirement: reaching a real phone requires a deliberate override,
    never an omission. An accidental run must not text anybody."""
    monkeypatch.delenv("WOTCHA_CHANNEL", raising=False)
    assert isinstance(get_channel(), ConsoleChannel)


def test_empty_string_channel_fails_loudly(monkeypatch):
    """Empty string is not a valid channel name and must fail, not silently
    become a live channel."""
    monkeypatch.setenv("WOTCHA_CHANNEL", "")
    with pytest.raises(ValueError):
        get_channel()
