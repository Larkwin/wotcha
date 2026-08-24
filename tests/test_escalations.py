"""An escalation is closed by the thing that answered it, not by a flag
somebody remembered to set.

`escalate` wrote `resolved: False`, `unresolved_escalations` read it, and
nothing in the system ever set it true -- so the first unsatisfiable week
asked the cook a question that stayed open permanently.
"""
from wotcha.domain.escalations import is_resolved


def _row(reason: str = "fence_unsatisfiable", **over) -> dict:
    row = {"record_id": "abc12345", "timestamp": "2026-08-22T09:00:00+00:00",
           "reason": reason, "question": "Which rule should give?",
           "resolved": False, "week_start": "2026-08-24"}
    row.update(over)
    return row


def test_a_fence_question_is_answered_by_the_week_publishing():
    """The whole design. "The fence cannot be satisfied for this week" stops
    being an open question the moment a week exists for it -- the published
    week is the answer, and no human has to confirm what the data already
    shows."""
    assert is_resolved(row=_row(), week_published=True) is True


def test_a_fence_question_stays_open_while_no_week_exists():
    assert is_resolved(row=_row(), week_published=False) is False


def test_a_stored_resolution_wins_over_the_derivation():
    """Same precedence as resolve_outcome: a recorded fact beats a computed
    presumption. Seeded rows may carry it, and M2's inbound reply will write
    it -- recomputing would replace an answer with a guess."""
    assert is_resolved(row=_row(resolved=True), week_published=False) is True


def test_a_retirement_question_is_not_answered_by_a_published_week():
    """Publishing a week says nothing about whether a meal should be retired.
    Nothing in the system can know that was settled, so it stays open rather
    than being closed on a signal that does not mean what it would need to
    mean. Closes in M2, when the cook can actually answer."""
    assert is_resolved(row=_row("retirement"), week_published=True) is False


def test_a_new_constraint_question_is_not_answered_by_a_published_week():
    assert is_resolved(row=_row("new_constraint"), week_published=True) is False


def test_a_retirement_question_can_still_be_closed_explicitly():
    """The stored flag remains the escape hatch for exactly the reasons the
    derivation refuses to guess about."""
    assert is_resolved(row=_row("retirement", resolved=True),
                       week_published=True) is True


def test_an_unknown_reason_is_never_derived_closed():
    """A reason this function does not recognise is one whose answer it
    cannot know. Defaulting to closed would silently swallow a question the
    cook is owed -- the failure mode is worse in that direction, so unknown
    means open."""
    assert is_resolved(row=_row("something_new"), week_published=True) is False


def test_a_row_with_no_week_start_is_not_derived_closed():
    """A fence question that names no week cannot be answered by a week
    publishing -- there is no week to check. `week_published` would be
    meaningless here, so the reason alone must not be enough."""
    assert is_resolved(row=_row(week_start=None), week_published=True) is False


def test_a_missing_resolved_field_reads_as_open():
    """Rows are plain dicts out of DynamoDB, and an older row may predate the
    field entirely. Absent must mean open, never closed."""
    row = _row()
    del row["resolved"]
    assert is_resolved(row=row, week_published=False) is False
