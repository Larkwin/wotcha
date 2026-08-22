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
from wotcha.domain.models import EMOJI_TO_LEVEL, Member, Signal, SignalLevel, Week
from wotcha.store.repo import Repository
from wotcha.web.tokens import parse_token

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


def _page(member: Member, week: Week, meals: dict, token: str) -> str:
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
        if week is None:
            return _resp(200, "<p>No week planned yet. Check back soon.</p>")
        meals = {m.meal_id: m for m in repo.list_meals(household_id)}
        return _resp(200, _page(member, week, meals, token))

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

    return _resp(404, "Not found", "text/plain")
