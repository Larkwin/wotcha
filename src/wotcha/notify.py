"""The one scheduled message per person, per week.

This is the only push the system makes. Everything else is pull, on the page.
Keeping that true is what stops Wotcha becoming another app that nags (spec
section 5).
"""
from datetime import UTC, date, datetime

from wotcha.channel.base import Channel
from wotcha.domain.models import Meal, Member, Week
from wotcha.store.repo import Repository
from wotcha.web.tokens import make_token

SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# SMS fragments across carriers and reads badly once it does. The week itself
# lives on the page; the text is a pointer, not a document -- so when the
# full message would blow the cap, the fix is to drop the day-by-day line and
# let the page carry it, never to raise the cap.
#
# 306 is not a round number, it is two concatenated GSM-7 segments: a
# single SMS holds 160 septets, and concatenation spends 7 of them per part
# on the UDH header, leaving 153. The old 320 permitted a silent third
# segment. Every message this module renders must therefore be GSM-7 -- one
# character outside that alphabet (an em dash, a "middle dot" separator)
# re-encodes the *whole* body as UCS-2 at 67 characters per segment, and a
# 198-character message becomes three parts instead of two.
#
# Every character in every template here is in the GSM-7 *basic* set, never
# the extension table (^{}\\[~]|), whose characters are legal but cost two
# septets each -- which would make a character cap quietly wrong.
#
# 160 was considered and does not fit: the fallback form, measured with this
# household's real ca-central-1 Function URL and a real 40-character token,
# is 198-200 characters, of which the link alone is 123. Getting under 160
# would mean cutting the reaction prompt, which is the only thing that makes
# the page two-way.
SMS_LENGTH_CAP = 306


# Typographic characters a model reaches for by habit and a keyboard offers
# without being asked. None of them exist in GSM-7, and a single one of them
# re-encodes the whole message as UCS-2 at 67 characters per segment -- the
# exact fragmentation SMS_LENGTH_CAP exists to prevent. Message templates in
# this module are written in ASCII directly; this map exists for the one part
# of a message that is not ours to write, the model-authored escalation
# question.
_TYPOGRAPHIC = {
    "\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": ",",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2026": "...", "\u00b7": "/", "\u2022": "*",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ",
    "\u2032": "'", "\u2033": '"',
}


def plain(text: str) -> str:
    """Fold the typography a model produces down to what a GSM-7 SMS can
    carry. Deliberately not a full transliteration: an accented letter in a
    name is meaning, and mangling it to save a segment is the wrong trade.
    Only punctuation that has an exact ASCII equivalent is folded."""
    for fancy, flat in _TYPOGRAPHIC.items():
        text = text.replace(fancy, flat)
    return text


def _meal_name(meals: dict[str, Meal], meal_id: str) -> str:
    return meals[meal_id].name if meal_id in meals else meal_id


def weekly_message(
    member: Member, week: Week, meals: dict[str, Meal], base_url: str, token: str
) -> str:
    header = (
        # ASCII hyphen, not an em dash: see SMS_LENGTH_CAP.
        f"Wotcha, {member.name} - week of "
        # %-d is glibc-only and raises on macOS/BSD; build the day separately.
        f"{week.week_start.strftime('%b')} {week.week_start.day}."
    )
    link = f"Your page: {base_url}/w/{token}"
    footer = "Anything from last week? Tap a face there."

    # "/" rather than a middle dot, for the same reason -- and rather than
    # "|", which is GSM-7 but only via the extension table, costing two
    # septets per character. "/" is in the basic set, so the character count
    # below IS the septet count and SMS_LENGTH_CAP means exactly what it says.
    line = " / ".join(
        f"{SHORT_DAYS[s.on_date.weekday()]} {_meal_name(meals, s.meal_id)}"
        for s in week.slots
    )
    full = f"{header}\n{line}\n{link}\n{footer}"
    if len(full) <= SMS_LENGTH_CAP:
        return full
    # Real meal names, a real Function URL, and a real token can blow the cap
    # even though the fixture-sized test above does not. Drop the day-by-day
    # line rather than raise it -- the page still carries the whole week.
    return f"{header}\n{link}\n{footer}"


def escalation_message(
    member: Member, question: str, week_start: date | None = None
) -> str:
    """The one message that goes to cooks only (spec section 5: "Decision SMS
    to the cook only"). It names what happened as well as what is being
    asked: a question with no context reads like a survey, and the fact that
    *no week went out* is the part the cook has to act on today."""
    header = f"Wotcha needs a decision, {member.name}."
    week = (
        f"No week was published for {week_start.strftime('%b')} {week_start.day}."
        if week_start is not None else "No week was published."
    )
    body = plain(question).strip()
    full = f"{header}\n{body}\n{week}"
    if len(full) <= SMS_LENGTH_CAP:
        return full
    # A model writes the question, so its length is not ours to assume.
    # Truncate the question, never the two lines that say what happened: a
    # cook holding half a sentence still knows a decision is waiting and that
    # nothing went out to the family.
    room = SMS_LENGTH_CAP - len(header) - len(week) - len("\n\n...")
    return f"{header}\n{body[:max(room, 0)]}...\n{week}"


def notify_escalation(
    repo: Repository,
    household_id: str,
    escalation: dict,
    channel: Channel,
) -> int:
    """Send one unresolved decision to the household's cooks. Returns how many
    messages were actually dispatched.

    Cooks only, by design: the fence being unsatisfiable is a decision for
    whoever holds authority over the rules, not news for the whole family --
    and a twelve-year-old cannot act on it.

    Stamped once it has actually reached someone, for the same reason
    `Week.notified_at` exists: schedulers retry, and a cook asked the same
    question four times stops reading the messages.
    """
    question = str(escalation.get("question") or "").strip()
    raw_week = escalation.get("week_start")
    week_start = None
    if isinstance(raw_week, str) and raw_week:
        try:
            week_start = date.fromisoformat(raw_week)
        except ValueError:
            # A stored value we cannot parse must not cost the cook the
            # message; the question is the part that matters.
            week_start = None
    sent = 0
    for member in repo.list_members(household_id):
        if not member.is_cook:
            continue
        if channel.send(member, escalation_message(member, question, week_start)):
            sent += 1
    if sent > 0:
        repo.mark_escalation_notified(household_id, escalation["sk"])
    return sent


def notify_week(
    repo: Repository,
    household_id: str,
    week: Week,
    channel: Channel,
    base_url: str,
    secret: str,
    force: bool = False,
) -> int:
    """Message every reachable household member their own link. Returns how
    many messages were actually dispatched -- not how many members exist.

    `Channel.send` returns `False` for a member with no address on the given
    channel; that must not be counted; reporting "notified 4" while one
    person actually got a text is the exact lie this function exists to
    prevent.

    Refuses to send twice for the same week unless `force` is set, using
    `Week.notified_at` as the guard. Schedulers retry, dry runs happen, and a
    family texted twice about the same dinners loses confidence in the whole
    thing faster than a wrong meal would cost.
    """
    if week.notified_at is not None and not force:
        return 0
    meals = {m.meal_id: m for m in repo.list_meals(household_id)}
    sent = 0
    for member in repo.list_members(household_id):
        token = make_token(household_id, member.person_id, secret)
        body = weekly_message(member, week, meals, base_url, token)
        if channel.send(member, body):
            sent += 1
    # Only stamp when something actually went out. Stamping unconditionally
    # would let a run that reached nobody (a misconfigured channel, nobody
    # reachable yet) permanently lock the week against any future genuine
    # send without force=True -- and with only one household member currently
    # reachable at all, that lock could trigger silently on the first try.
    if sent > 0:
        week.notified_at = datetime.now(UTC)
        repo.put_week(week)
    return sent
