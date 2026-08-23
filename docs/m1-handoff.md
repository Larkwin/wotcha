# M1 handoff — state, runbook, and what is deliberately unfinished

Written 2026-08-20, at the end of M1. Everything here is either operational
knowledge you will need, or a decision someone made on purpose that a future
reader would otherwise rediscover as a bug.

## Live state

| | |
|---|---|
| Account / region | `<AWS_ACCOUNT_ID>` / `ca-central-1` |
| Runtime | `<RUNTIME_ID>`, `WOTCHA_CHANNEL=console` |
| Family page | the `WebUrl` CloudFormation output |
| Weekly schedule | `wotcha-weekly-plan`, **DISABLED**, Sat 09:00 America/Toronto |
| SMS long code | provisioned, ACTIVE, sandbox tier |
| Verified handsets | Alex only. Morgan, Riley, Jesse have no phone on file. |
| Suite | 199 tests, lint clean |

## The first real send

Two commands, in this order. The second one is the first message Wotcha ever
sends a person.

```bash
WOTCHA_CHANNEL=sms \
  WOTCHA_SMS_ORIGINATION_ID=<origination id> \
  WOTCHA_BASE_URL=<the WebUrl output> \
  make runtime-deploy

.venv/bin/agentcore invoke '{"action":"plan_and_notify"}'
```

**`WOTCHA_BASE_URL` is not optional.** Omit it and the runtime redeploys against
`http://localhost:8080`, putting a dead link in everyone's message. `make
runtime-deploy` now refuses that combination outright, but know why the guard
exists.

**If you need to retry, use `{"action": "notify", "force": true}` — never
`plan_and_notify`.** The latter re-runs the Planner, so the family would get a
text about a week nobody reviewed.

Afterwards, put the runtime back on `console`:

```bash
WOTCHA_BASE_URL=<the WebUrl output> make runtime-deploy
```

Then enable the weekly schedule, and **invoke the scheduler Lambda once by hand**
— its IAM scope has never been exercised, and its failure mode is silent:
the schedule fires, the Lambda is denied, nobody is texted, no alarm.

```bash
WOTCHA_SCHEDULE_ENABLED=true WOTCHA_RUNTIME_ARN=<arn> WOTCHA_RUNTIME_ROLE_ARN=<arn> make deploy
```

## Adding the rest of the family

Kids were at camp during M1, so only Alex is reachable. When they are back:

1. Add E.164 `phone` fields to `data/household.local.json` (untracked; the
   committed `data/household.json` is the public sample household)
2. Re-seed with `--file data/household.local.json` (the loader validates
   before writing, refuses to clobber a published week or change a stored
   number without `--force`, and refuses outright to create a household that
   does not already exist)
3. Verify each handset with `scripts/preflight_sms.py` — the SMS sandbox only
   delivers to verified destinations, up to 10

That sandbox restriction is also the thing that made an agent's stray deploy
harmless in August. **It goes away when you request production access** — which
is the same moment the family's numbers go in. Re-read the channel safety rules
before making that request.

## Parked deliberately — not bugs

- **`plan_and_notify` re-runs the Planner on every invocation**, so a retry
  publishes a different legal week into the row the family was texted about. The
  notify guard correctly blocks a second text, so nobody is misinformed by a
  message — but the page can drift from what was sent. Changing replan semantics
  was judged riskier than the behaviour, this close to first use.
- **A mid-week manual `plan_week` makes the page show next week.** The page
  prefers the coming Monday's row when one exists. That is right on Saturday and
  wrong on Wednesday. It is an M2 question: what should the page show mid-week?
- **Escalations are never marked resolved**, so rows accumulate. The same-week
  filter contains it.
- **`Suggestion` is defined and unused** — M2 vocabulary.

## The M3 evidence gap — read this before building the Curator

`DRIFTCASE#riley-tacos` records that Riley went off tacos around 2026-06-01. That
is genuine recall and it is the ground truth the Curator will be scored against.

**The evidence for it does not exist.** Seeded history is two weeks in August;
the drift happened in June. The Curator would have a labelled answer and nothing
to derive it from.

What is needed is not six months of precise recall. It is a plausible rotation
across roughly March–August in which:

1. Tacos appear regularly early on
2. Riley's signals on tacos fade over time — enthusiasm draining, not complaints arriving
3. Tacos stop appearing around June, with no explicit retirement recorded

The third point is the true story and the whole thesis: **nobody retired tacos.**
They stopped getting cooked because someone half-noticed they were not landing.
Making that visible and deliberate is the Curator's entire job, and it is only
demonstrable if the silence is in the data.

## This repo is public

Published at `github.com/Larkwin/wotcha`. Nothing account-identifying belongs in
it: no AWS account id or alias, no ARN carrying one, no provisioned or personal
phone number, and no real household names. `data/household.json` and
`data/household-notes.md` are pseudonymous sample data — structurally identical
to the real thing, which is all the code and the demo need.

Read live values from AWS instead of recording them here:

- `aws sts get-caller-identity` — account id
- `aws pinpoint-sms-voice-v2 describe-phone-numbers --region ca-central-1` —
  the sending number and its `WOTCHA_SMS_ORIGINATION_ID`

Anything committed to a public repo is permanent: force-pushing clears neither
GitHub's caches nor anyone's fork. Check a diff for identifiers before it lands,
not after.

## Addendum, 2026-08-20 — the permission nothing could have caught

The first real send failed with `AccessDeniedException` on `sms-voice:SendTextMessage`.
The runtime's execution role had DynamoDB and Secrets Manager but never SMS.

It is worth understanding why this survived fourteen reviewed tasks. **No agent on
this project was ever permitted near AWS End User Messaging** — a deliberate rule
adopted after one exceeded an explicit boundary. Every test injected a fake
`_send_raw`. Every dry run used the console channel. So the one permission that
only matters on a real send was the one permission nothing in the system could
exercise. It could only ever be found by a human running the real thing.

That is the honest cost of the safety rule, and the rule was still right: the
alternative was agents holding a live origination id while children were away at
camp. A permission error is recoverable in ninety seconds. A stray text is not.

The grant now lives in `infra/stack.py`, scoped to the single origination number
and supplied via `WOTCHA_SMS_ORIGINATION_ARN` at deploy time, so no account
resource id is committed. It is skipped when unset — a console deployment never
sends.

**The failure was clean:** `notified_at` was never stamped, because `notify_week`
only stamps when `sent > 0`. The retry needed no `force`. That behaviour came from
a review finding hours earlier, and this is the second time in one evening it has
paid for itself.
