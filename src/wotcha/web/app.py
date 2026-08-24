"""The family page: a Lambda Function URL serving server-rendered HTML.

No build step, no framework, no client-side JavaScript. Reactions are form POSTs
because link previewers prefetch GET URLs, and a prefetched reaction is a
fabricated one.
"""
import base64
import binascii
import html
from datetime import date
from functools import lru_cache
from urllib.parse import parse_qs

import boto3

from wotcha.config import settings
from wotcha.dates import local_today, monday_of, next_monday
from wotcha.domain.models import (
    EMOJI_TO_LEVEL,
    Meal,
    MealStatus,
    Member,
    Outcome,
    Signal,
    SignalLevel,
    SlotOutcome,
    Substitute,
    SuggestionStatus,
    Week,
)
from wotcha.store.repo import Repository
from wotcha.web.tokens import parse_public_token, parse_token

LEVEL_LABELS = [
    (SignalLevel.LOVED, "\U0001F60D"),
    (SignalLevel.FINE, "\U0001F44D"),
    (SignalLevel.MEH, "\U0001F610"),
    (SignalLevel.REFUSED, "\U0001F645"),
]

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


@lru_cache(maxsize=1)
def _repo() -> Repository:
    s = settings()
    return Repository(table_name=s.table_name, region=s.aws_region)


@lru_cache(maxsize=1)
def _secret() -> str:
    s = settings()
    client = boto3.client("secretsmanager", region_name=s.aws_region)
    return client.get_secret_value(SecretId=s.link_secret_name)["SecretString"]


# The household calendar, shared with runtime.py. This module used to compute
# the current Monday from the *UTC* date while runtime.py deliberately used
# America/Toronto -- two modules that must agree about which week it is, each
# keeping its own answer.
_monday = monday_of


def _week_to_show(repo: Repository, household_id: str, today: date) -> Week | None:
    """The week this page is about.

    Prefer the coming Monday when a row exists for it, because that is the
    week the weekly message announces: runtime.py plans `next_monday` and
    notify.py puts that date in the text. A page that renders the current
    week instead answers a question nobody asked -- on the first real send
    the family taps the link and reads "No week planned yet." while the plan
    sits in the table one key away.

    Fall back to the current Monday, which is the state from Monday to
    Friday: next week is not planned until Saturday, and the family still
    has to be able to see the week they are living in.
    """
    coming = repo.get_week(household_id, next_monday(today))
    return coming if coming is not None else repo.get_week(household_id, monday_of(today))



# The three states a household ever delivers. `made` is derived and `planned`
# is the absence of news, so neither is offered here -- see
# wotcha.domain.outcomes.
DELIVERABLE = (
    (SlotOutcome.SWAPPED, "cooked something else"),
    (SlotOutcome.TAKEOUT, "takeout or ate out"),
    (SlotOutcome.SKIPPED, "no real dinner"),
)


def _correction_html(slot, meals: dict, token: str, delivered,
                     today: date) -> str:
    """A collapsed disclosure, never a question.

    Section 6 rejected a nightly "rate your dinner" ping as a nag, and the
    same objection kills a row of "did you make this?" buttons on seven
    nights. `<details>` is native HTML -- this page has no JavaScript and is
    not getting any -- so the affordance costs a reader nothing until they
    have something to say.

    Offered on every night, not only past ones. Knowing on Monday that Friday
    is a night out is ordinary, and `resolve_outcome` honours a correction
    whatever the date; refusing it here would make the page argue with the
    household.
    """
    action = f'/o/{html.escape(token)}'
    on = slot.on_date.isoformat()
    if delivered is None or delivered.outcome is not SlotOutcome.SWAPPED:
        opts = "".join(
            f'<button name="outcome" value="{o.value}">{label}</button>'
            for o, label in DELIVERABLE
        )
        # Most nights on a published week have not happened yet. Asking
        # "not what happened?" about Friday reads as a system that has lost
        # track of what day it is -- and plans genuinely change in advance,
        # which is why resolve_outcome honours a future correction at all.
        summary = "not what happened?" if slot.on_date < today else "plans changed?"
        if delivered is not None:
            summary = f"{delivered.outcome.value} \u2014 change?"
        return f"""
          <details class="fix">
            <summary>{summary}</summary>
            <form method="post" action="{action}">
              <input type="hidden" name="on_date" value="{on}">
              {opts}
            </form>
          </details>"""

    # Swapped and recorded. The substitute is a follow-up that never blocked
    # the swap, so this is offered after the fact and stays optional -- an
    # off-list night is worth counting even when nobody names what it was.
    if delivered.substitute is not Substitute.UNSPECIFIED:
        named = (meals[delivered.substitute_meal_id].name
                 if delivered.substitute_meal_id in meals else "something else")
        return f'<div class="fix">swapped \u2014 {html.escape(named)}</div>'
    # The replaced meal is not a candidate for having replaced itself: it
    # would record a swap asserting nothing changed.
    picker = "".join(
        f'<button name="substitute_meal_id" value="{html.escape(m.meal_id)}">'
        f'{html.escape(m.name)}</button>'
        for m in meals.values() if m.meal_id != slot.meal_id
    )
    return f"""
          <details class="fix">
            <summary>swapped \u2014 what did you have?</summary>
            <form method="post" action="{action}">
              <input type="hidden" name="on_date" value="{on}">
              {picker}
              <button name="substitute" value="off_list">something else</button>
            </form>
          </details>"""


def _slugify(name: str) -> str:
    """A meal id from a cook-typed name. Deliberately dumb and predictable --
    a surprising id shows up in the fence, in rationales, and in every
    signal ever recorded against the meal."""
    slug = "".join(c.lower() if c.isalnum() else "-" for c in name.strip())
    return "-".join(part for part in slug.split("-") if part)


def _suggestions_html(suggestions: list, meals: dict, token: str,
                      is_cook: bool) -> str:
    """Pending suggestions, and the cook's controls if this is the cook.

    Shown to everyone, deliberately. v1 sends nothing back over SMS, so this
    is the only feedback a person gets that their text arrived at all --
    without it a suggestion is indistinguishable from a broken system.
    """
    pending = [s for s in suggestions if s.status is SuggestionStatus.PENDING]
    if not pending:
        return ""
    cards = []
    for s in pending:
        # Untrusted: this text came off a carrier network from a person.
        said = html.escape(s.text)
        note = f'<div class="why">{html.escape(s.note)}</div>' if s.note else ""
        if s.matched_meal_id and s.matched_meal_id in meals:
            note += (f'<div class="why">Already on the list as '
                     f'{html.escape(meals[s.matched_meal_id].name)}.</div>')
        if not is_cook:
            cards.append(f'<li><div class="meal">{said}</div>{note}'
                         f'<div class="why">waiting for the cook</div></li>')
            continue
        name = html.escape(s.proposed_name or "")
        cards.append(f"""
        <li>
          <div class="meal">{said}</div>
          {note}
          <form method="post" action="/s/{html.escape(token)}">
            <input type="hidden" name="suggestion_id" value="{html.escape(s.suggestion_id)}">
            <input type="hidden" name="created_at" value="{s.created_at.isoformat()}">
            <input name="proposed_name" value="{name}" placeholder="meal name">
            <button name="action" value="approve">add as candidate</button>
            <button name="action" value="decline">no</button>
          </form>
        </li>""")
    return f'<h2>Suggestions</h2><ul>{"".join(cards)}</ul>'


def _public_page(week: Week, meals: dict) -> str:
    """The same week, for anyone with the link and nobody in particular.

    Rationale is shown here though it is cook-only on the private page: on a
    public page the agent's reasoning is the exhibit, not a household detail.
    Nobody is named -- not the reader, not the eaters -- and there is nothing
    to submit, so a read-only link is read-only in structure rather than by
    convention.
    """
    rows = "".join(
        f'''
        <li>
          <div class="day">{WEEKDAYS[slot.on_date.weekday()]}</div>
          <div class="meal">{html.escape(
              meals[slot.meal_id].name if slot.meal_id in meals else slot.meal_id)}</div>
          <div class="why">{html.escape(slot.rationale)}</div>
        </li>'''
        for slot in week.slots
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wotcha</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 1.5rem;
         max-width: 34rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: .75rem 0; border-bottom: 1px solid color-mix(in srgb, currentColor 15%, transparent); }}
  .day {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
          opacity: .6; }}
  .meal {{ font-size: 1.1rem; }}
  .why {{ font-size: .9rem; opacity: .75; margin-top: .2rem; }}
  .note {{ font-size: .8rem; opacity: .6; margin-top: 1.5rem; }}
</style></head><body>
<h1>The week</h1>
<ul>{rows}</ul>
<p class="note">A read-only view of one household's plan, and why the agent
chose it. Week of {week.week_start.isoformat()}.</p>
</body></html>"""


def _page(member: Member, week: Week, meals: dict, token: str,
          outcomes: dict, today: date, suggestions: list) -> str:
    # The rationale is shown only to cooks -- everyone else sees the meal and
    # can react, but not the reasoning behind it (household owner's call).
    show_rationale = member.is_cook
    rows = []
    for slot in week.slots:
        meal = meals.get(slot.meal_id)
        buttons = "".join(
            f'<button name="level" value="{lvl.value}" '
            f'title="{lvl.value}">{emoji}</button>'
            for lvl, emoji in LEVEL_LABELS
        )
        why_html = (
            f'<div class="why">{html.escape(slot.rationale)}</div>'
            if show_rationale else ""
        )
        rows.append(f"""
        <li>
          <div class="day">{WEEKDAYS[slot.on_date.weekday()]}</div>
          <div class="meal">{html.escape(meal.name if meal else slot.meal_id)}</div>
          {why_html}
          <form method="post" action="/r/{html.escape(token)}">
            <input type="hidden" name="on_date" value="{slot.on_date.isoformat()}">
            <input type="hidden" name="meal_id" value="{html.escape(slot.meal_id)}">
            {buttons}
          </form>
          {_correction_html(slot, meals, token, outcomes.get(slot.on_date), today)}
        </li>""")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wotcha</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 1.5rem;
         max-width: 34rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .25rem; }}
  .sub {{ opacity: .7; margin: 0 0 1.5rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 1rem 0; border-top: 1px solid rgba(128,128,128,.3); }}
  .fix {{ font-size: .8rem; opacity: .65; margin-top: .35rem; }}
  .fix summary {{ cursor: pointer; }}
  .fix form {{ margin-top: .35rem; }}
  .day {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
          opacity: .6; }}
  .meal {{ font-size: 1.15rem; font-weight: 600; }}
  .why {{ opacity: .75; margin: .15rem 0 .6rem; }}
  button {{ font-size: 1.4rem; background: none; border: 1px solid
            rgba(128,128,128,.4); border-radius: .5rem; padding: .3rem .55rem;
            margin-right: .3rem; cursor: pointer; }}
</style></head><body>
<h1>Wotcha</h1>
<p class="sub">Hi {html.escape(member.name)} — here's the week of
{week.week_start.isoformat()}.</p>
<ul>{''.join(rows)}</ul>
{_suggestions_html(suggestions, meals, token, member.is_cook)}
</body></html>"""


def _resp(status: int, body: str, content_type: str = "text/html") -> dict:
    return {"statusCode": status,
            "headers": {"content-type": f"{content_type}; charset=utf-8"},
            "body": body}


class _BadBody(Exception):
    """Raised for a body that claims to be base64 but isn't decodable."""


def _request_body(event: dict) -> str:
    """Lambda Function URLs base64-encode the request body for some content
    types (isBase64Encoded: true). Parsing that as literal form text finds no
    fields at all -- a family member's tap would silently fail to record,
    or fail with a misleading error, and nobody would know. Raises _BadBody
    for a body that claims to be base64 but doesn't decode cleanly, so the
    caller can turn it into a clean 400 instead of an uncaught exception."""
    raw = event.get("body") or ""
    if not event.get("isBase64Encoded"):
        return raw
    try:
        return base64.b64decode(raw, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as e:
        raise _BadBody from e


def handler(event: dict, _context) -> dict:
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/")
    parts = [p for p in path.split("?")[0].split("/") if p]

    if len(parts) != 2:
        return _resp(404, "Not found", "text/plain")
    route, token = parts

    # Resolved before the person-token parse: a public link carries no person,
    # and its signature is domain-separated so it cannot satisfy parse_token
    # below -- which is what stops a read-only URL authorising a write.
    if route == "p":
        if method != "GET":
            return _resp(404, "Not found", "text/plain")
        public_household = parse_public_token(token, _secret())
        if public_household is None:
            return _resp(403, "That link isn't valid.", "text/plain")
        repo = _repo()
        week = _week_to_show(repo, public_household, local_today())
        if week is None:
            return _resp(200, "<p>No week published yet.</p>")
        meals = {m.meal_id: m for m in repo.list_meals(public_household)}
        return _resp(200, _public_page(week, meals))

    claim = parse_token(token, _secret())
    if claim is None:
        return _resp(403, "That link isn't valid.", "text/plain")
    household_id, person_id = claim

    repo = _repo()
    members = {m.person_id: m for m in repo.list_members(household_id)}
    member = members.get(person_id)
    if member is None:
        return _resp(403, "That link isn't valid.", "text/plain")

    if route == "w" and method == "GET":
        week = _week_to_show(repo, household_id, local_today())
        meals = {m.meal_id: m for m in repo.list_meals(household_id)}
        suggestions = repo.list_suggestions(household_id)
        if week is None:
            # A sender's feedback loop does not depend on the Planner having
            # run. v1 sends nothing back over SMS, so this page is the only
            # sign a text arrived at all -- tying that to a published week
            # would make an unplanned week look like a lost message, hitting
            # hardest on a brand-new household whose first act is a text
            # before any week exists.
            body = ("<p>No week planned yet. Check back soon.</p>"
                    + _suggestions_html(suggestions, meals, token, member.is_cook))
            return _resp(200, body)
        outcomes = repo.outcomes_for_week(household_id, week.week_start)
        return _resp(200, _page(member, week, meals, token, outcomes,
                                local_today(), suggestions))

    if route == "r" and method == "POST":
        try:
            body = _request_body(event)
        except _BadBody:
            return _resp(400, "Malformed request body.", "text/plain")
        form = parse_qs(body)
        level_raw = (form.get("level") or [""])[0]
        level = EMOJI_TO_LEVEL.get(level_raw)
        if level is None:
            try:
                level = SignalLevel(level_raw)
            except ValueError:
                return _resp(400, "Unknown reaction.", "text/plain")
        # Same untrusted source as `level` above -- a stale or truncated form
        # must get a clean 400, not an uncaught ValueError, on this
        # unauthenticated-by-anything-but-the-link endpoint.
        try:
            on_date = date.fromisoformat((form.get("on_date") or [""])[0])
        except ValueError:
            return _resp(400, "Missing or invalid date.", "text/plain")
        # `meal_id` is the third untrusted field on this endpoint, and the
        # only one that was taken on faith. It flows into Signal, into the
        # sort key, and back into the Planner's context via get_signals --
        # so anyone holding a link could write arbitrary strings into the
        # corpus M3 depends on, and into text the model reads. The fence
        # holds regardless (it is Python and cannot be argued with), but
        # rationales are model output shown to cooks.
        #
        # Checked against every meal the household has, not against the Safe
        # List: a meal retired last month was still eaten, and a reaction to
        # it is exactly the signal the Curator exists to read.
        meal_id = (form.get("meal_id") or [""])[0]
        if meal_id not in {m.meal_id for m in repo.list_meals(household_id)}:
            return _resp(400, "Unknown meal.", "text/plain")
        # The signal is always attributed to the token's own person -- never to
        # anything a form field could claim -- because signals are per-person
        # and misattribution would corrupt the one dataset the product runs on.
        repo.put_signal(Signal(
            household_id=household_id,
            person_id=person_id,
            meal_id=meal_id,
            on_date=on_date,
            level=level,
        ))
        # Redirect back so a refresh doesn't resubmit.
        return {"statusCode": 303, "headers": {"location": f"/w/{token}"}, "body": ""}

    if route == "o" and method == "POST":
        try:
            body = _request_body(event)
        except _BadBody:
            return _resp(400, "Malformed request body.", "text/plain")
        form = parse_qs(body)
        try:
            on_date = date.fromisoformat((form.get("on_date") or [""])[0])
        except ValueError:
            return _resp(400, "Missing or invalid date.", "text/plain")

        known = repo.outcomes_for_week(household_id, monday_of(on_date)).get(on_date)
        meal_ids = {m.meal_id for m in repo.list_meals(household_id)}

        # A second POST for a night already swapped is the substitute
        # follow-up, which never blocked the swap and must not silently
        # rewrite what it replaced.
        sub_raw = (form.get("substitute") or [""])[0]
        sub_meal = (form.get("substitute_meal_id") or [""])[0]
        outcome_raw = (form.get("outcome") or [""])[0]

        if not outcome_raw and known is not None:
            outcome = known.outcome
        else:
            try:
                outcome = SlotOutcome(outcome_raw)
            except ValueError:
                return _resp(400, "Unknown outcome.", "text/plain")
        # `made` is derived and `planned` is the absence of news. Accepting
        # either would turn a presumption nobody confirmed into a stored fact
        # -- the confidently-wrong field the Planner already refuses to write
        # for cook_id.
        if outcome in (SlotOutcome.MADE, SlotOutcome.PLANNED):
            return _resp(400, "That outcome is derived, not delivered.", "text/plain")

        # Untrusted, exactly like `meal_id` on the reaction endpoint: it flows
        # into the corpus the Curator reads. Checked against every household
        # meal, not the Safe List -- swapping to a retired meal is precisely
        # the signal worth having.
        if sub_meal:
            if sub_meal not in meal_ids:
                return _resp(400, "Unknown meal.", "text/plain")
            substitute, substitute_meal_id = Substitute.KNOWN, sub_meal
        elif sub_raw == Substitute.OFF_LIST.value:
            substitute, substitute_meal_id = Substitute.OFF_LIST, None
        elif sub_raw:
            return _resp(400, "Unknown substitute.", "text/plain")
        else:
            substitute, substitute_meal_id = Substitute.UNSPECIFIED, None

        repo.put_outcome(household_id, Outcome(
            on_date=on_date,
            outcome=outcome,
            substitute=substitute,
            substitute_meal_id=substitute_meal_id,
        ))
        return {"statusCode": 303, "headers": {"location": f"/w/{token}"}, "body": ""}

    if route == "s" and method == "POST":
        # Checked here, not merely hidden in the template. Hiding the control
        # is presentation; refusing the request is the rule. The buck stops
        # with the cook.
        if not member.is_cook:
            return _resp(403, "Only a cook can decide that.", "text/plain")
        try:
            body = _request_body(event)
        except _BadBody:
            return _resp(400, "Malformed request body.", "text/plain")
        form = parse_qs(body)
        suggestion_id = (form.get("suggestion_id") or [""])[0]
        created_at = (form.get("created_at") or [""])[0]
        action = (form.get("action") or [""])[0]

        suggestion = repo.get_suggestion(household_id, created_at, suggestion_id)
        if suggestion is None:
            return _resp(404, "No such suggestion.", "text/plain")

        if action == "decline":
            repo.put_suggestion(household_id, suggestion.model_copy(
                update={"status": SuggestionStatus.DECLINED}))
            return {"statusCode": 303, "headers": {"location": f"/w/{token}"},
                    "body": ""}
        if action != "approve":
            return _resp(400, "Unknown action.", "text/plain")

        name = (form.get("proposed_name") or [""])[0].strip()
        meal_id = _slugify(name)
        if not meal_id:
            return _resp(400, "A meal needs a name.", "text/plain")
        # Refused rather than merged: put_meal overwrites wholesale, so
        # approving onto an existing id would rewrite a Safe Meal's status
        # from a suggestion form -- silently retiring or un-retiring it.
        if any(m.meal_id == meal_id for m in repo.list_meals(household_id)):
            return _resp(400, "The household already has a meal by that name.",
                         "text/plain")

        # CANDIDATE, never SAFE or AUDITIONING. get_safe_list returns neither,
        # so approving adds to the roster without putting it on next week's
        # table -- the Curator decides whether it earns an audition.
        # effort_minutes=0 because nobody has said. The field is required and
        # get_safe_list withholds it from the Planner anyway, so an honest
        # zero beats a guess -- the same reasoning that leaves cook_id unset.
        repo.put_meal(household_id, Meal(
            meal_id=meal_id, name=name, effort_minutes=0,
            status=MealStatus.CANDIDATE,
        ))
        repo.put_suggestion(household_id, suggestion.model_copy(
            update={"status": SuggestionStatus.APPROVED}))
        return {"statusCode": 303, "headers": {"location": f"/w/{token}"},
                "body": ""}

    return _resp(404, "Not found", "text/plain")
