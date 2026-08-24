"""When a question handed to the cook stops being open.

`escalate` wrote `resolved: False`, `unresolved_escalations` read it, and
nothing anywhere set it true. The first week the fence could not be satisfied
would have asked the cook a question that stayed open forever -- accumulating
rows, and eventually offering a stale one to a run that failed for some
unrelated reason.

The fix is the same shape as `wotcha.domain.outcomes`: resolution is computed
at read time and never written. The stored row stays a record of what was
asked rather than a status somebody has to remember to update, and the
`ESCALATION#` history stays complete for the reliability corpus -- only the
*open* view narrows. Nothing is deleted.

What makes that possible is that one kind of question already has its answer
sitting in the table. "The fence cannot be satisfied for the week of the
24th" is answered, completely, by a published week of the 24th. Nobody needs
to confirm it; the week is the confirmation.

The other two reasons are deliberately not derived. Publishing a week says
nothing about whether a meal should be retired or whether a new hard
constraint was accepted, and `household-notes.md` is explicit that an agent
should decline to decide things it cannot know. Those stay open until someone
answers them, which needs the inbound channel M2 brings.
"""

# Reasons whose answer is visible in stored data. Deliberately a set of one:
# membership here is a claim that a published week *is* the answer to the
# question, and that is only true of the fence.
_ANSWERED_BY_A_PUBLISHED_WEEK = frozenset({"fence_unsatisfiable"})


def is_resolved(*, row: dict, week_published: bool) -> bool:
    """Whether this escalation should be treated as settled, in precedence
    order.

    1. A stored `resolved` that is true. That is recorded fact -- seeded data
       may carry it, and M2's inbound reply will write it -- and recomputing
       it would replace an answer with a guess.
    2. A `fence_unsatisfiable` question naming a week that has since been
       published. The week is the answer.
    3. Otherwise open. Including reasons this function does not recognise:
       an unknown reason is one whose answer it cannot know, and defaulting
       to closed would silently swallow a question the cook is owed.

    `week_published` is passed in rather than looked up so this stays pure
    and testable without a repository, exactly as `resolve_outcome` takes its
    `stored` and `correction` rather than fetching them. The caller does the
    I/O and knows which week to ask about.
    """
    if row.get("resolved"):
        return True
    if row.get("reason") not in _ANSWERED_BY_A_PUBLISHED_WEEK:
        return False
    # A question that names no week cannot be answered by a week publishing:
    # there is no week to have checked, so `week_published` describes nothing
    # and must not be allowed to close the row on its own.
    if not row.get("week_start"):
        return False
    return week_published
