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
