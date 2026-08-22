"""`make runtime-deploy` must refuse an sms-plus-localhost invocation.

README step 8's `runtime-deploy` command carries `WOTCHA_BASE_URL`; step
10's did not. Omitting it does not fail -- the Makefile's
`WOTCHA_BASE_URL ?= http://localhost:8080` default silently fills in, the
redeploy succeeds, and the very next `plan_and_notify` puts
`http://localhost:8080/w/<token>` in every family member's first real text.
A real send with a localhost link is never intentional, so the guard
refuses it structurally rather than trusting the README to be read
correctly every time.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD_VARS = ["WOTCHA_CHANNEL", "WOTCHA_BASE_URL"]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")


def _run(**overrides) -> subprocess.CompletedProcess:
    """Run the guard alone -- never `runtime-deploy` itself, which would
    invoke the real `agentcore` CLI."""
    env = {k: v for k, v in os.environ.items() if k not in GUARD_VARS}
    env.update(overrides)
    return subprocess.run(
        ["make", "check-runtime-deploy-env"], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def test_sms_with_the_localhost_default_is_refused():
    """This is the exact defect: WOTCHA_CHANNEL=sms with WOTCHA_BASE_URL
    left unstated. The Makefile's default fills the gap silently, so the
    guard is the only thing standing between an operator's typo and a dead
    link on the family's phones."""
    result = _run(WOTCHA_CHANNEL="sms")
    assert result.returncode != 0
    # The message has to name the variable and show the fix, not just fail.
    assert "WOTCHA_BASE_URL" in result.stdout
    assert "localhost" in result.stdout
    assert "make runtime-deploy" in result.stdout


def test_sms_with_an_explicit_localhost_url_is_also_refused():
    """Stating localhost explicitly is exactly as wrong as leaving it to
    the default -- both end with the same dead link on the phone."""
    result = _run(WOTCHA_CHANNEL="sms", WOTCHA_BASE_URL="http://localhost:8080")
    assert result.returncode != 0
    assert "WOTCHA_BASE_URL" in result.stdout


def test_sms_with_a_real_base_url_is_allowed():
    result = _run(WOTCHA_CHANNEL="sms",
                  WOTCHA_BASE_URL="https://abc123.lambda-url.ca-central-1.on.aws")
    assert result.returncode == 0, result.stdout + result.stderr


def test_console_with_the_localhost_default_is_allowed():
    """Console runs legitimately use localhost -- there is no phone to
    reach, so the guard must not fire for them."""
    result = _run(WOTCHA_CHANNEL="console")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_bare_default_channel_is_allowed():
    """Neither variable stated at all: WOTCHA_CHANNEL defaults to console in
    the Makefile, so this is the safe, ordinary case and must pass."""
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_deploy_depends_on_the_guard():
    """Listed as a prerequisite so the refusal happens before `agentcore
    deploy` is ever invoked."""
    makefile = (ROOT / "Makefile").read_text()
    deps = next(line for line in makefile.splitlines()
                if line.startswith("runtime-deploy:")).split(":", 1)[1].split()
    assert "check-runtime-deploy-env" in deps
