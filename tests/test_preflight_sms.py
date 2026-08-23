"""scripts/preflight_sms.py -- the only script that can put a real text on a
real phone. Nothing here touches AWS; these cover the wiring around it.
"""
import preflight_sms
import pytest


def test_origination_id_comes_from_the_environment(monkeypatch):
    """Never a literal in the repo: an origination id is an account-specific
    AWS resource identifier, and this repo does not carry one."""
    monkeypatch.setenv("WOTCHA_SMS_ORIGINATION_ID", "phone-example")
    assert preflight_sms.origination_id() == "phone-example"


def test_missing_origination_id_says_how_to_find_it(monkeypatch):
    monkeypatch.delenv("WOTCHA_SMS_ORIGINATION_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        preflight_sms.origination_id()
    assert "WOTCHA_SMS_ORIGINATION_ID" in str(exc.value)
    assert "describe-phone-numbers" in str(exc.value)


def test_the_read_only_commands_do_not_need_an_origination_id(monkeypatch):
    """`list`, `verify` and `confirm` send nothing from our number, so a
    module-level env read would have broken them all for no reason."""
    monkeypatch.delenv("WOTCHA_SMS_ORIGINATION_ID", raising=False)
    calls = []
    monkeypatch.setattr(preflight_sms, "cmd_list", lambda: calls.append("list"))
    monkeypatch.setattr(preflight_sms, "COMMANDS", {"list": preflight_sms.cmd_list})
    monkeypatch.setattr("sys.argv", ["preflight_sms.py", "list"])
    assert preflight_sms.main() == 0
    assert calls == ["list"]


def test_an_unknown_command_is_named_not_just_met_with_usage(monkeypatch, capsys):
    """A bare `main() == 1` would pass just as well against the no-argument
    usage dump -- the opposite of what this test's name claims. The mistyped
    command itself has to appear in the output, not just an exit code."""
    monkeypatch.setattr("sys.argv", ["preflight_sms.py", "sned"])
    assert preflight_sms.main() == 1
    assert "sned" in capsys.readouterr().out


def test_an_internal_keyerror_is_not_disguised_as_a_usage_error(monkeypatch):
    """The dispatch used to be `{...}[cmd](*args)` inside `except KeyError`,
    so a KeyError raised *inside* a command -- a missing key in an AWS
    response -- printed the usage text as if the operator had mistyped the
    command, hiding the real failure entirely."""
    def explodes():
        raise KeyError("PhoneNumbers")

    monkeypatch.setattr(preflight_sms, "COMMANDS", {"list": explodes})
    monkeypatch.setattr("sys.argv", ["preflight_sms.py", "list"])
    with pytest.raises(KeyError, match="PhoneNumbers"):
        preflight_sms.main()


# --- the spend ceiling --------------------------------------------------
#
# $1.00/month, and in the sandbox that is the maximum rather than a default:
# a Service Quotas increase on TextMessageMonthlySpend is refused while the
# account is sandboxed, so the ceiling is not raisable without production
# access. Hitting it stops sends within minutes. These cover the judgement
# `spend` makes about the numbers, not the AWS calls that fetch them.


def test_no_datapoints_is_not_reported_as_zero_spent():
    """The failure this whole item exists to prevent, one level up. The
    metric needs a service-linked role and does not publish until the first
    message of the account's life, so silence means either "nothing sent" or
    "this alarm can never fire" -- and printing $0.00 would show the reassuring
    one."""
    spent, note = preflight_sms.read_spend([])
    assert spent is None
    assert "NOT $0.00" in note
    assert "INSUFFICIENT_DATA" in note


def test_spend_is_the_running_maximum_not_a_sum():
    """TextMessageMonthlySpend is a month-to-date cumulative gauge. Summing
    the hourly datapoints would add the running total to itself and report a
    ceiling breach that never happened."""
    spent, _ = preflight_sms.read_spend(
        [{"Maximum": 0.02}, {"Maximum": 0.11}, {"Maximum": 0.07}]
    )
    assert spent == 0.11


def test_quiet_spending_says_it_is_under_the_threshold():
    spent, note = preflight_sms.read_spend([{"Maximum": 0.12}])
    assert spent == 0.12
    assert "under" in note


def test_crossing_the_alarm_threshold_is_called_out():
    _, note = preflight_sms.read_spend([{"Maximum": preflight_sms.SPEND_ALARM_USD}])
    assert "past" in note
    assert f"{preflight_sms.SPEND_ALARM_USD:.2f}" in note


def test_the_ceiling_says_sends_are_stopping_and_it_cannot_be_raised():
    """An operator reading this is mid-incident. "Request a quota increase"
    is the obvious next move and it does not work from the sandbox -- so the
    message has to say that, and say what the real lever costs."""
    _, note = preflight_sms.read_spend([{"Maximum": preflight_sms.SPEND_CEILING_USD}])
    assert "CEILING" in note
    assert "cannot be raised" in note
    assert "verified-destination" in note


def test_the_alarm_threshold_leaves_room_to_act():
    """Half the ceiling, not 95% of it. The weekly send is the unit of
    spending here, so the warning has to arrive with at least one whole cycle
    left or it is a notification of a fait accompli."""
    assert preflight_sms.SPEND_ALARM_USD <= preflight_sms.SPEND_CEILING_USD / 2


def test_the_script_and_the_stack_agree_on_the_numbers():
    """The constants are duplicated -- stack.py runs under infra/.venv with
    the CDK installed and this script does not, so there is no import path
    between them. Duplication that nothing checks is duplication that drifts,
    and the direction it drifts is an alarm threshold above a ceiling that
    already stopped the sends."""
    from pathlib import Path
    stack = (Path(__file__).resolve().parent.parent / "infra" / "stack.py").read_text()
    assert f"sms_spend_ceiling_usd = {preflight_sms.SPEND_CEILING_USD:.2f}" in stack
    assert "sms_spend_alarm_usd = sms_spend_ceiling_usd / 2" in stack
    assert f'alarm_name="{preflight_sms.ALARM_NAME}"' in stack
    assert f'topic_name="{preflight_sms.ALARMS_TOPIC}"' in stack


def test_the_alarm_actually_notifies_the_topic():
    """The load-bearing line, and the one an edit could drop while leaving
    every other assertion here passing. A CloudWatch alarm with no action is
    a red square in a console nobody opens -- which is the failure this whole
    item exists to close, rebuilt one level up."""
    from pathlib import Path
    stack = (Path(__file__).resolve().parent.parent / "infra" / "stack.py").read_text()
    assert "add_alarm_action(cw_actions.SnsAction(alarms))" in stack


def test_the_alarm_retains_state_across_the_quiet_days():
    """The metric only publishes around a send, so six days in seven have no
    datapoints. Under TreatMissingData.MISSING an empty evaluation range goes
    to INSUFFICIENT_DATA, so an alarm that had gone red would fall back out of
    ALARM on the next quiet day -- a spend warning that erases itself. IGNORE
    retains the current state instead."""
    from pathlib import Path
    stack = (Path(__file__).resolve().parent.parent / "infra" / "stack.py").read_text()
    assert "TreatMissingData.IGNORE" in stack
    assert "TreatMissingData.MISSING" not in stack


def test_the_spend_alarm_lives_in_the_product_region():
    """AWS's own walkthrough for spending alarms says to switch to
    us-east-1. That is true for AWS/Billing and wrong for AWS/SMSVoice, which
    is regional -- an alarm built in us-east-1 would watch a metric that is
    never published there and stay green through a ceiling breach."""
    from pathlib import Path
    stack = (Path(__file__).resolve().parent.parent / "infra" / "stack.py").read_text()
    assert 'namespace="AWS/SMSVoice"' in stack
    assert preflight_sms.REGION == "ca-central-1"
