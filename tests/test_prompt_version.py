"""The prompt fingerprint stamped on every eval record.

What matters: it is stable for the same text, it moves when anything the
model reads moves -- tool docstrings included -- and every eval-writing path
actually carries it.
"""
import json

from wotcha.agents import liaison, planner
from wotcha.agents.planner_tools import PLANNER_TOOLS
from wotcha.agents.prompt_version import fingerprint


def test_the_same_text_always_gives_the_same_version():
    """A version that drifted between cold starts would split one prompt
    across several buckets on replay."""
    assert fingerprint("a", "b") == fingerprint("a", "b")
    assert len(fingerprint("a")) == 12


def test_any_change_to_the_text_moves_the_version():
    assert fingerprint("Plan the week.") != fingerprint("Plan the week!")


def test_parts_are_not_simply_concatenated():
    """Moving text between the system prompt and the kickoff is a change,
    and must not hash the same as the arrangement it replaced."""
    assert fingerprint("ab", "c") != fingerprint("a", "bc")


def test_a_tool_docstring_edit_moves_the_planner_version():
    """Tool descriptions are instructions the model reads. An edit to one
    that left the version alone would recreate the unmarked boundary this
    exists to prevent."""
    specs = [t.tool_spec for t in PLANNER_TOOLS]
    edited = json.loads(json.dumps(specs))
    edited[0]["description"] += " Also, be brief."
    kickoff = planner.PLANNER_KICKOFF.format(
        week_start="{week_start}", max_attempts=planner.MAX_ATTEMPTS)
    original = fingerprint(planner.PLANNER_SYSTEM_PROMPT, kickoff,
                           json.dumps(specs, sort_keys=True))
    assert original == planner.PLANNER_PROMPT_VERSION
    assert original != fingerprint(planner.PLANNER_SYSTEM_PROMPT, kickoff,
                                   json.dumps(edited, sort_keys=True))


def test_the_two_agents_do_not_share_a_version():
    assert planner.PLANNER_PROMPT_VERSION != liaison.LIAISON_PROMPT_VERSION
