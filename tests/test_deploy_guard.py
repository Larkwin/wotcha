"""`make deploy` must refuse to silently un-deploy live infrastructure.

infra/stack.py reads three values from the environment with off-by-default
fallbacks, and every one of them fails quietly when absent: the runtime's
IAM grant is deleted, the scheduler's target is blanked, and the weekly
schedule is turned off -- all with a green exit code. A bare `make deploy`
is therefore a destructive command that looks like a successful one.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VARS = ["WOTCHA_RUNTIME_ARN", "WOTCHA_RUNTIME_ROLE_ARN", "WOTCHA_SCHEDULE_ENABLED"]

# Stated on every deploy below, so a household-id refusal never masks another.
HH = {"WOTCHA_HOUSEHOLD_ID": "a-real-household"}

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")


def _run(**overrides) -> subprocess.CompletedProcess:
    """Run the guard alone -- never `deploy` itself, which would build and
    touch AWS. The guard is a separate target precisely so it can be run,
    and tested, on its own."""
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(overrides)
    return subprocess.run(
        ["make", "check-deploy-env"], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def test_a_bare_deploy_is_refused():
    result = _run()
    assert result.returncode != 0
    # The message has to name what is missing; "invalid configuration" would
    # send the operator back to the source at the worst possible moment.
    for var in VARS:
        assert var in result.stdout


def test_every_variable_is_required_individually():
    """Two of three set is the realistic accident -- an operator who
    remembered the ARNs and forgot the schedule flag."""
    for missing in VARS:
        stated = {v: ("false" if v == "WOTCHA_SCHEDULE_ENABLED" else "arn:aws:x")
                  for v in VARS if v != missing}
        result = _run(**stated)
        assert result.returncode != 0, f"{missing} unset was allowed through"
        assert missing in result.stdout


def test_explicitly_empty_arns_are_allowed():
    """README step 6's first-ever deploy genuinely has no runtime ARN yet --
    the runtime does not exist until step 8. Stating the value as empty is a
    deliberate act; leaving it unset is an accident. The guard distinguishes
    them."""
    result = _run(WOTCHA_RUNTIME_ARN="", WOTCHA_RUNTIME_ROLE_ARN="",
                  WOTCHA_SCHEDULE_ENABLED="false", **HH)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_schedule_value_that_is_neither_true_nor_false_is_refused():
    """`WOTCHA_SCHEDULE_ENABLED=yes` reads as false in stack.py and would
    silently disable the weekly schedule -- the same failure as forgetting
    it, wearing the costume of having remembered."""
    result = _run(WOTCHA_RUNTIME_ARN="arn:aws:x", WOTCHA_RUNTIME_ROLE_ARN="arn:aws:y",
                  WOTCHA_SCHEDULE_ENABLED="yes", **HH)
    assert result.returncode != 0
    assert "true or false" in result.stdout


def test_the_guard_runs_before_anything_is_built():
    """Listed first among deploy's prerequisites. If bootstrap-infra or
    build-lambda ran first, a refused deploy would still have spent a minute
    and rewritten build/web."""
    makefile = (ROOT / "Makefile").read_text()
    deps = next(line for line in makefile.splitlines()
                if line.startswith("deploy:")).split(":", 1)[1].split()
    assert deps[0] == "check-deploy-env"


def test_a_deploy_that_leaves_the_household_id_at_its_default_is_refused():
    """`data/household.json` is the public pseudonymous household, and the
    Makefile default matches it. Deploying at that default points the runtime
    at a tenant the family does not live in: the planner finds no meals and
    the family page is empty, with a green exit code and no error anywhere."""
    result = _run(WOTCHA_RUNTIME_ARN="arn:aws:x", WOTCHA_RUNTIME_ROLE_ARN="arn:aws:y",
                  WOTCHA_SCHEDULE_ENABLED="false")
    assert result.returncode != 0
    assert "WOTCHA_HOUSEHOLD_ID" in result.stdout


def test_a_stated_household_id_is_allowed():
    result = _run(WOTCHA_RUNTIME_ARN="arn:aws:x", WOTCHA_RUNTIME_ROLE_ARN="arn:aws:y",
                  WOTCHA_SCHEDULE_ENABLED="false", **HH)
    assert result.returncode == 0, result.stdout + result.stderr
