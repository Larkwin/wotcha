"""What actually happened on a night, resolved rather than recorded.

The household is never asked how dinner went. A nightly "rate your dinner"
ping is a nag that spec section 6 considered and rejected, and section 9's
whole model is that voluntary feedback is sparse -- people report at the
extremes and go silent across the middle. A prompt asking "did you make
this?" would collect that same silence and then be unable to read it.

So the default costs nothing: a past night nobody corrected is a night that
went as planned, computed here at read time and never written. The only write
that ever happens is someone volunteering that reality differed.

That is deliberately not the same as storing `MADE`. `household-notes.md`
records the principle it would break: "an agent should decline to decide
things it cannot know... A blank cook field is honest. A confidently wrong
one erodes trust in every other line on the plan." Nobody confirmed dinner,
so nothing claims they did -- the presumption stays a presumption, and a
consumer that needs to weigh it differently still can.
"""
from datetime import date

from wotcha.domain.models import Outcome, SlotOutcome


def resolve_outcome(
    *,
    on_date: date,
    stored: SlotOutcome,
    correction: Outcome | None,
    today: date,
) -> SlotOutcome:
    """What this night should be treated as, in precedence order.

    1. A delivered correction, whatever it says. It wins even for a future
       date: someone knowing on Monday that Friday is a night out is ordinary,
       and refusing it would make the page argue with the household.
    2. A stored outcome that is not `PLANNED`. That is recorded history --
       seeded backfill carries `MADE` already -- and recomputing it would
       replace a fact with a guess.
    3. Otherwise the date decides: a night that has finished is presumed
       `MADE`, and anything today or later stays `PLANNED`.

    Today is not yet made. Presuming dinner at breakfast would put a meal in
    the history that nobody has eaten.
    """
    if correction is not None:
        return correction.outcome
    if stored is not SlotOutcome.PLANNED:
        return stored
    return SlotOutcome.MADE if on_date < today else SlotOutcome.PLANNED
