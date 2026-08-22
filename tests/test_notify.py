from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from wotcha.channel.console import ConsoleChannel
from wotcha.channel.sms import SmsChannel
from wotcha.domain.models import Meal, MealStatus, Member, Slot, Week
from wotcha.notify import (
    SMS_LENGTH_CAP,
    escalation_message,
    notify_escalation,
    notify_week,
    plain,
    weekly_message,
)
from wotcha.store.repo import Repository
from wotcha.web.tokens import make_token

HID, SECRET, BASE = "demo", "test-secret", "https://example.test"

MEALS = {
    "chili": Meal(meal_id="chili", name="Chili", protein="beef",
                  effort_minutes=45, status=MealStatus.SAFE),
    "flat-sushi": Meal(meal_id="flat-sushi", name="Flat Sushi Day",
                       effort_minutes=0, is_takeout=True, status=MealStatus.SAFE),
}

WEEK = Week(
    household_id=HID, week_start=date(2026, 8, 24),
    published_at=datetime.now(UTC),
    slots=[
        Slot(on_date=date(2026, 8, 24), meal_id="chili", cook_id="alex",
             rationale="Cook once, eat twice."),
        Slot(on_date=date(2026, 8, 25), meal_id="flat-sushi", cook_id="alex",
             rationale="It's Tuesday."),
    ],
)


def test_message_names_the_person_and_lists_the_week():
    token = make_token(HID, "maya", SECRET)
    member = Member(person_id="maya", name="Maya", phone="+15195550111")
    body = weekly_message(member, WEEK, MEALS, BASE, token)
    assert "Maya" in body
    assert "Chili" in body and "Flat Sushi Day" in body
    assert f"{BASE}/w/{token}" in body


def test_message_stays_short_enough_for_sms():
    """Long messages fragment across carriers and read badly. The week lives on
    the page; the text is a pointer, not a document."""
    token = make_token(HID, "maya", SECRET)
    member = Member(person_id="maya", name="Maya", phone="+15195550111")
    assert len(weekly_message(member, WEEK, MEALS, BASE, token)) <= SMS_LENGTH_CAP


def test_sms_length_holds_for_real_meal_names():
    """The cap is load-bearing and this fixture is optimistic: two short
    meals, a short base URL. Real names ("Sheet-pan sausages", "Chicken and
    rice") across seven days, plus a Lambda Function URL (~75 chars) and a
    token (~45), can blow past it. If this fails, drop the day-by-day line from
    the message and let the page carry it -- do not raise the cap."""
    long_meals = {
        f"m{i}": Meal(meal_id=f"m{i}", name=name, effort_minutes=30,
                      status=MealStatus.SAFE)
        for i, name in enumerate([
            "Sheet-pan sausages", "Chicken and rice", "Flat Sushi Day",
            "Roast chicken dinner", "Fish tacos", "Chili con carne",
            "Pasta with meatballs",
        ])
    }
    week = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        slots=[Slot(on_date=date.fromordinal(date(2026, 8, 24).toordinal() + i),
                    meal_id=f"m{i}", cook_id="alex", rationale="x")
               for i in range(7)],
    )
    base = "https://abcdefghijklmnopqrstuvwxyz123456.lambda-url.us-east-1.on.aws"
    token = make_token(HID, "maya", SECRET)
    member = Member(person_id="maya", name="Maya", phone="+15195550111")
    assert len(weekly_message(member, week, long_meals, base, token)) <= SMS_LENGTH_CAP


def test_sms_length_holds_for_this_households_actual_meal_names():
    """The generic fixture above is optimistic in a second way: it invents
    plausible-sounding meal names, but this specific household's real Safe
    List (data/household.json) is longer than the invented one, and the real
    deployed Function URL is a ca-central-1 one (this product's actual
    region, not us-east-1). Both matter to the arithmetic: seven real meal
    names plus a real ~70-character ca-central-1 Lambda Function URL plus a
    real 40-character token pushes the naive full-week message to 361
    characters, 55 over the 306-character (two GSM-7 segment) cap. The
    fallback (drop the day-by-day line,
    let the page carry the week) must engage here, not just on a contrived
    fixture."""
    real_household_meals = {
        "roast-chicken": Meal(meal_id="roast-chicken", name="Roast chicken dinner",
                              protein="chicken", effort_minutes=90,
                              status=MealStatus.SAFE, tags=["weekend"]),
        "flat-sushi": Meal(meal_id="flat-sushi", name="Flat Sushi Day",
                           effort_minutes=0, is_takeout=True,
                           status=MealStatus.SAFE, tags=["tradition", "takeout"]),
        "fried-rice": Meal(meal_id="fried-rice", name="Chicken fried rice",
                           protein="chicken", effort_minutes=25,
                           status=MealStatus.SAFE, tags=["fast", "uses-leftovers"]),
        "chili": Meal(meal_id="chili", name="Chili", protein="beef",
                      effort_minutes=45, status=MealStatus.SAFE, tags=["cook-once"]),
        "sloppy-chicken": Meal(meal_id="sloppy-chicken",
                               name="Sloppy chicken sandwiches", protein="chicken",
                               effort_minutes=30, status=MealStatus.SAFE,
                               tags=["fast"]),
        "beef-broccoli": Meal(meal_id="beef-broccoli", name="Beef and broccoli",
                              protein="beef", effort_minutes=30,
                              status=MealStatus.SAFE, tags=["fast"]),
        "roast-pork": Meal(meal_id="roast-pork", name="Roast pork dinner",
                           protein="pork", effort_minutes=90,
                           status=MealStatus.SAFE, tags=["weekend"]),
    }
    week_start = date(2026, 8, 24)
    order = ["roast-chicken", "flat-sushi", "fried-rice", "chili",
             "sloppy-chicken", "beef-broccoli", "roast-pork"]
    week = Week(
        household_id=HID, week_start=week_start,
        slots=[Slot(on_date=date.fromordinal(week_start.toordinal() + i),
                    meal_id=meal_id, rationale="x")
               for i, meal_id in enumerate(order)],
    )
    # Shape matches a real Lambda Function URL: 32+ char random subdomain,
    # ca-central-1 (this product's real region per docs/preflight-report.md).
    real_base = "https://abcdefghijklmnopqrstuvwxyz234567.lambda-url.ca-central-1.on.aws"
    token = make_token(HID, "alex", SECRET)
    member = Member(person_id="alex", name="Alex", phone="+15195550101")

    body = weekly_message(member, week, real_household_meals, real_base, token)

    assert len(body) <= SMS_LENGTH_CAP
    # The fallback must actually have engaged for this real scenario, not
    # merely happen to fit -- confirm the day-by-day line is gone.
    assert "Mon" not in body
    assert f"{real_base}/w/{token}" in body


def test_message_falls_back_to_no_day_line_when_the_full_form_is_too_long():
    """Directly exercises the fallback path, independent of whether any
    particular fixture happens to trip it."""
    huge_meals = {
        f"m{i}": Meal(meal_id=f"m{i}",
                      name=f"An implausibly long and descriptive meal name {i}",
                      effort_minutes=30, status=MealStatus.SAFE)
        for i in range(7)
    }
    week = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        slots=[Slot(on_date=date.fromordinal(date(2026, 8, 24).toordinal() + i),
                    meal_id=f"m{i}", cook_id="alex", rationale="x")
               for i in range(7)],
    )
    token = make_token(HID, "maya", SECRET)
    member = Member(person_id="maya", name="Maya", phone="+15195550111")
    body = weekly_message(member, week, huge_meals, BASE, token)
    assert len(body) <= SMS_LENGTH_CAP
    # The fallback drops the day-by-day line entirely.
    assert "Mon" not in body
    assert f"{BASE}/w/{token}" in body


@pytest.fixture
def seeded():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="wotcha-test",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        r = Repository(table_name="wotcha-test", region="us-east-1")
        for m in MEALS.values():
            r.put_meal(HID, m)
        r.put_member(HID, Member(person_id="alex", name="Alex",
                                 phone="+15195550101", is_cook=True))
        r.put_member(HID, Member(person_id="maya", name="Maya",
                                 phone="+15195550111"))
        r.put_week(WEEK)
        yield r


def test_every_member_is_messaged_once(seeded):
    channel = ConsoleChannel()
    count = notify_week(seeded, HID, WEEK.model_copy(deep=True), channel, BASE, SECRET)
    assert count == 2
    assert sorted(p for p, _ in channel.sent) == ["alex", "maya"]


def test_a_week_is_never_notified_twice(seeded):
    """Scheduler retries and dry runs must not re-text the family."""
    channel = ConsoleChannel()
    assert notify_week(seeded, HID, WEEK.model_copy(deep=True), channel,
                       BASE, SECRET) == 2
    again = ConsoleChannel()
    week = seeded.get_week(HID, WEEK.week_start)
    assert notify_week(seeded, HID, week, again, BASE, SECRET) == 0
    assert again.sent == []
    assert notify_week(seeded, HID, week, again, BASE, SECRET, force=True) == 2


def test_each_member_gets_their_own_link(seeded):
    channel = ConsoleChannel()
    notify_week(seeded, HID, WEEK.model_copy(deep=True), channel, BASE, SECRET)
    bodies = dict(channel.sent)
    assert bodies["alex"] != bodies["maya"]
    assert make_token(HID, "maya", SECRET) in bodies["maya"]
    assert make_token(HID, "maya", SECRET) not in bodies["alex"]


# --- the alphabet the carrier actually bills for ---------------------------

# GSM 03.38, the default 7-bit alphabet. One character outside it re-encodes
# the ENTIRE message as UCS-2 at 67 characters per segment, so a 198-character
# text that should cost two segments costs three.
#
# Deliberately the BASIC set only. The extension characters (^{}\\[~]|) do
# encode in GSM-7, but via an escape, costing two septets each -- so a
# message full of them would pass a character-count cap while costing more
# segments than the cap allows. Excluding them here keeps
# SMS_LENGTH_CAP's character count identical to the real septet cost.
GSM7_BASIC = set(
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r\u00c5\u00e5"
    "\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e"
    "\u00c6\u00e6\u00df\u00c9"
    " !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
    "\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7"
    "\u00bfabcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
)


def _rendered_messages() -> list[tuple[str, str]]:
    """Every message this system can put on a phone: the weekly text in both
    its forms, and the escalation to the cook."""
    member = Member(person_id="alex", name="Alex", phone="+15195550101", is_cook=True)
    token = make_token(HID, "alex", SECRET)
    long_meals = {
        f"m{i}": Meal(meal_id=f"m{i}", name=f"A fairly long real meal name {i}",
                      effort_minutes=30, status=MealStatus.SAFE)
        for i in range(7)
    }
    long_week = Week(
        household_id=HID, week_start=date(2026, 8, 24),
        slots=[Slot(on_date=date.fromordinal(date(2026, 8, 24).toordinal() + i),
                    meal_id=f"m{i}", rationale="x")
               for i in range(7)],
    )
    return [
        ("weekly, full form", weekly_message(member, WEEK, MEALS, BASE, token)),
        ("weekly, fallback form",
         weekly_message(member, long_week, long_meals, BASE, token)),
        ("escalation",
         escalation_message(member, "Taco Tuesday · which tradition gives?",
                            date(2026, 8, 24))),
        ("escalation, no week", escalation_message(member, "Which gives?")),
    ]


@pytest.mark.parametrize("label,body", _rendered_messages())
def test_every_rendered_message_is_gsm7_only(label, body):
    """The em dash in the header and the middle-dot separator were outside
    GSM-7, so every weekly text this system rendered was UCS-2 -- 67
    characters per segment, three segments for a message that should cost
    two. This test exists so the next typographic character somebody types
    into a template is caught here rather than on four phone bills."""
    outside = sorted({c for c in body if c not in GSM7_BASIC})
    assert not outside, (
        f"{label} contains characters outside GSM-7: "
        f"{[(c, hex(ord(c))) for c in outside]} -- the whole message will "
        f"encode as UCS-2 at 67 chars/segment"
    )


def test_every_rendered_message_fits_two_gsm7_segments(label_body=None):
    """153 septets per part once concatenated (7 go to the UDH header), so
    306 is two parts. The cap is that number, not a round one."""
    assert SMS_LENGTH_CAP == 2 * 153
    for label, body in _rendered_messages():
        assert len(body) <= SMS_LENGTH_CAP, label


# --- escalations: the decision message, cooks only -------------------------

def test_escalation_message_names_the_decision_and_the_missing_week():
    member = Member(person_id="alex", name="Alex", phone="+15195550101", is_cook=True)
    body = escalation_message(
        member, "Flat Sushi Day and the chicken target collide. Which gives?",
        date(2026, 8, 24),
    )
    assert "Alex" in body
    assert "Which gives?" in body
    # The cook has to know that nothing went out, not just that a question
    # exists -- that is the part they have to act on today.
    assert "No week was published" in body
    assert "Aug 24" in body


def test_escalation_message_folds_a_models_typography_to_gsm7():
    """The question is model output, and a model reaches for an em dash --
    or a middle dot as a separator -- by habit. One of them re-encodes the
    whole SMS as UCS-2 at 67 characters per segment -- the exact
    fragmentation the cap exists to prevent."""
    member = Member(person_id="alex", name="Alex", phone="+15195550101", is_cook=True)
    body = escalation_message(
        member,
        "Two rules collide \u2014 which gives\u2026 the \u201ctradition\u201d "
        "\u00b7 the freezer?",
        date(2026, 8, 24),
    )
    assert body.isascii(), body
    assert "\u2014" not in body
    assert "\u00b7" not in body


def test_escalation_message_folds_a_middot_to_a_slash_not_a_pipe():
    """`|` is GSM-7 only via the extension table, at two septets -- exactly
    the fragmentation SMS_LENGTH_CAP's reasoning rejects, and `isascii()`
    would pass on it without catching the mistake."""
    member = Member(person_id="alex", name="Alex", phone="+15195550101", is_cook=True)
    body = escalation_message(
        member, "Taco Tuesday \u00b7 Flat Sushi Day, which gives?",
        date(2026, 8, 24),
    )
    assert "Taco Tuesday / Flat Sushi Day" in body
    assert "|" not in body
    assert "\u00b7" not in body


def test_escalation_message_truncates_a_runaway_question_not_the_context():
    member = Member(person_id="alex", name="Alex", phone="+15195550101", is_cook=True)
    body = escalation_message(member, "x" * 900, date(2026, 8, 24))
    assert len(body) <= SMS_LENGTH_CAP
    assert body.startswith("Wotcha needs a decision, Alex.")
    assert body.endswith("No week was published for Aug 24.")


def test_plain_leaves_meaningful_characters_alone():
    """Folding is punctuation only. An accented letter in a name is meaning,
    and mangling it to save a segment is the wrong trade."""
    assert plain("Zo\u00eb\u2019s favourite") == "Zo\u00eb's favourite"


def test_escalation_goes_to_cooks_only_and_stamps_the_row(seeded):
    """Spec section 5: "Decision SMS to the cook only". A twelve-year-old
    cannot act on an unsatisfiable fence, and the whole family being told
    the system failed is not the same product."""
    seeded.put_escalation(HID, {
        "record_id": "abc123", "timestamp": "2026-08-22T13:00:00+00:00",
        "reason": "fence_unsatisfiable", "question": "Which tradition gives?",
        "resolved": False, "week_start": "2026-08-24",
    })
    row = seeded.latest_unresolved_escalation(HID)
    channel = ConsoleChannel()

    sent = notify_escalation(seeded, HID, row, channel)

    assert sent == 1
    assert [p for p, _ in channel.sent] == ["alex"]  # Maya is not a cook
    assert seeded.latest_unresolved_escalation(HID)["notified_at"]


def test_escalation_that_reaches_nobody_is_not_stamped(seeded):
    """Same reasoning as notify_week: a run that reached nobody must not lock
    the question against a later, genuine send."""
    seeded.put_escalation(HID, {
        "record_id": "abc123", "timestamp": "2026-08-22T13:00:00+00:00",
        "reason": "fence_unsatisfiable", "question": "Which tradition gives?",
        "resolved": False, "week_start": "2026-08-24",
    })
    row = seeded.latest_unresolved_escalation(HID)

    class _Unreachable:
        def send(self, member, body) -> bool:
            return False

    assert notify_escalation(seeded, HID, row, _Unreachable()) == 0
    assert seeded.latest_unresolved_escalation(HID).get("notified_at") is None


def test_members_without_an_address_on_the_channel_are_skipped_and_not_counted():
    """Channel.send returns False when a member has no address on that
    channel. That must not be counted as sent -- the household would
    otherwise be told "notified 2" while only one person, the one with a
    phone, actually received anything. This matters concretely: right now
    exactly one member of the real household has a phone number."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName="wotcha-test-partial",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        r = Repository(table_name="wotcha-test-partial", region="us-east-1")
        for m in MEALS.values():
            r.put_meal(HID, m)
        r.put_member(HID, Member(person_id="alex", name="Alex",
                                 phone="+15195550101", is_cook=True))
        # No phone -- reachable only by email, like the rest of this
        # household today. An SMS channel must skip, not count, this member.
        r.put_member(HID, Member(person_id="maya", name="Maya",
                                 email="maya@example.test"))
        r.put_week(WEEK.model_copy(deep=True))

        sent = []
        channel = SmsChannel(origination_id="phone-fake", region="us-east-1")
        channel._send_raw = lambda number, body: sent.append((number, body))

        count = notify_week(r, HID, r.get_week(HID, WEEK.week_start), channel,
                            BASE, SECRET)

        assert count == 1
        assert len(sent) == 1
        assert sent[0][0] == "+15195550101"
