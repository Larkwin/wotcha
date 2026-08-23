# Status — where wotcha stands

Living document. Updated 2026-08-23, the evening before dogfooding starts.
`docs/m1-handoff.md` is the M1 record and still authoritative for the runbook;
this is the ledger of what exists, what is next, and what is deliberately not
being done yet.

## Live state

| | |
|---|---|
| Repo | `github.com/Larkwin/wotcha`, **public**, CI on every push and PR |
| Suite | 271 tests, lint clean, Python 3.12 and 3.14 |
| Region / model | `ca-central-1`, `us.anthropic.claude-sonnet-4-6` |
| Runtime | deployed, `WOTCHA_CHANNEL=sms`, live origination number |
| Weekly schedule | `cron(0 9 ? * SAT *)` America/Toronto, **DISABLED** — first kickoff is manual, by choice |
| Table | one household: 4 members, 9 meals, 4 weeks, 3 signals, 1 drift case, 6 eval records |
| Reachable | one handset verified. **The rest of the family is not yet on file.** |
| SMS budget | $1.00/month. In the sandbox that is the **maximum**, not a default — see below. Alarm at $0.50; topic needs a subscriber |

## Done

**M0 — prove the pipe.** Repo, MIT licence, one deploy command, DynamoDB
schema, Strands agent on AgentCore Runtime, Bedrock access verified per model
and per region, SMS deliverability confirmed on real hardware.

**M1 — the family-usable slice.** Fence, validator and Planner producing a
weekly plan; SMS with a permanent per-person signed link; shared page
accepting reactions; eval logging from the first real week. Deployed, and one
real week has been planned, published and texted.

**Since M1:**

- **Model ladder settled** — Strands' `BedrockModel` reaches the open-weight
  rungs through Project Mantle, so §13 keeps all five rather than degrading to
  three. Note the catalogue does not list them; round-trip before concluding
  anything is unavailable.
- **Two-household split** — the committed `data/household.json` is a
  pseudonymous sample; the real household lives in an untracked file. The
  seeder takes a required `--file`, and refuses to create a household that
  does not already exist without `--new-household`.
- **Deploy guards** — `make deploy` refuses unless all four silently-revoking
  variables are stated, plus the household id. The fourth was added after
  `cdk diff` caught a deploy about to remove the runtime's SMS grant.
- **Repo hygiene, enforced not remembered** — the suite fails if any tracked
  file carries a real phone number, or the real household's names or id. The
  identity check sources its values from the untracked file so the guard does
  not publish what it protects.
- **Outcome capture** — a night nobody corrects resolves to `made` at read
  time and is never written; `swapped`, `takeout` and `skipped` are delivered,
  never asked. A swap records immediately, and naming what replaced it is an
  optional follow-up including an explicit `off_list`.
- **Public read-only page** — `/p/<token>`, signed in a separate domain from
  personal links so it cannot authorise a write. `scripts/public_link.py`
  mints one.
- **CI** — ruff and the suite on every push and PR, with deliberately invalid
  AWS credentials so nothing can reach a real account.
- **The Planner reads outcomes.** `get_recent_weeks` resolves each slot
  through `resolve_outcome` instead of handing over the stored `planned` a
  live week is written with and nothing ever rewrites. A finished night
  nobody corrected reads as `made`; a corrected one reads as what the
  household said. Resolved into the returned dict, never onto the model —
  the presumption stays computed at read time. **The system prompt changed
  with it**: "do not repeat a meal the household ate" now says *ate, not
  planned*, and spells out that a `swapped`, `takeout` or `skipped` night
  leaves that meal fresh — resolving the field changes nothing the model does
  unless the prompt says what the values mean.
- **Alarm on the SMS spend ceiling.** CloudWatch alarm on
  `AWS/SMSVoice` / `TextMessageMonthlySpend` at $0.50, half the ceiling, into
  an SNS topic `wotcha-alarms`. Three things that are easy to get wrong and
  are now pinned by tests: statistic **Maximum**, because the metric is a
  month-to-date cumulative gauge and Sum would add it to itself;
  **`TreatMissingData.IGNORE`**, because the metric only publishes around a
  send and under `MISSING` an alarm that had gone red would fall back to
  INSUFFICIENT_DATA on the next quiet day; and **`ca-central-1`**, because
  AWS's own spending-alarm walkthrough says to switch to `us-east-1`, which
  is true for `AWS/Billing` and wrong for this regional namespace.
- **`preflight_sms.py spend`**, wired into `make preflight`. Answers the two
  questions the alarm cannot answer about itself: is the metric publishing at
  all, and would the alarm reach a person. No datapoints is reported as
  *unknown*, never as $0.00 — the metric needs the service-linked role and
  does not publish until the first message, so an alarm on a metric nobody
  publishes sits in INSUFFICIENT_DATA forever.

## Next — ordered

1. **Escalation resolution.** Written `resolved: False`, read, never settled —
   nothing sets it true. The first unsatisfiable week asks the cook a question
   they cannot answer.
2. **M2 — Liaison and two-way SMS.** The structural gate: inbound free-text
   signals, suggestions, disruption capture and "what's for dinner tonight?"
   all arrive through it. Needs the SQS queue, DLQ and IAM role in
   `docs/two-way-sms-setup.md`; the existing long code is already the right
   number. **Verify inbound works in the sandbox** — the documented
   restrictions are all outbound and spend, but that is absence of mention,
   not a guarantee.
3. **M3 — Curator.** Standings, decay detection, retirement, auditions. The
   differentiator, and the only milestone genuinely gated on accumulated
   history.

## Operational, not code

- **Add the rest of the family.** Numbers into the untracked household file,
  re-seed with `--file`, then `preflight_sms.py verify`/`confirm` per handset.
  Sandbox holds 10 verified destinations; one family fits, and no production
  access request is needed for that.
- **First family-wide send waits for Monday.** `plan_and_notify` targets
  `next_monday`, and `notify_week` returns 0 when the week already carries a
  `notified_at`. Before Monday the target is a week already notified, so a
  kickoff reaches nobody — newly verified handsets included — and reports
  success.
- **Hand-invoke the scheduler Lambda once.** Both IAM hops simulate as
  allowed, but simulation does not cover resource-based policies. Do it on a
  Monday or later, when it targets a week nobody was texted about.
- **Subscribe an address to `wotcha-alarms`.** The topic and the alarm are
  deployed; the subscription is not. CDK creates it unsubscribed on purpose —
  an email subscription lands in the synthesized template and in the `make
  deploy` line that gets pasted into runbooks, and this repo is public. Take
  the `AlarmsTopicArn` output and:
  `aws sns subscribe --region ca-central-1 --topic-arn <arn> --protocol email
  --notification-endpoint <you>`, then click the confirmation link. **Until
  it is confirmed the alarm fires into nothing** — `make preflight` says so
  every time, and that is the only thing standing between this design and the
  failure it was built to close.
- **Seed the demo household** if the public link should show anything:
  `--file data/household.json --new-household`.

## Parked deliberately — not bugs

- **`plan_and_notify` re-plans on every invocation**, so a retry publishes a
  different legal week into the row the family was texted about. The notify
  guard stops a second text, so nobody is misinformed; the page can drift from
  what was sent. Retry with `{"action": "notify", "force": true}`, never
  `plan_and_notify`.
- **A mid-week manual `plan_week` makes the page show next week.** Right on a
  Saturday, wrong on a Wednesday. There is currently a published,
  never-notified week sitting one week out.
- **`Suggestion` is defined and unused** — M2 vocabulary. A novel off-list
  substitute is really a suggestion, and that is where it should land.
- **A known substitute is not history the Planner can see.** `get_recent_weeks`
  now says a swapped night's planned meal was not eaten, but not what replaced
  it, so a named substitute can be planned again the following week as though
  it were fresh. Not a regression — that fact was invisible before outcomes
  were resolved at all — and it belongs with the substitute work in M2 rather
  than widening a history read.
- **A send that fails partway through re-texts the people it already
  reached.** `notify_week` increments `sent` per member and only stamps
  `notified_at` after the loop, and nothing catches the send exception — so a
  failure on member three (the spend ceiling being the realistic cause)
  propagates before the stamp, and the retry starts again at member one.
  Loud rather than silent, which is the right half to have got right, and
  with one household the blast radius is two duplicate texts. Found while
  building the spend alarm; the fix belongs with whatever makes sends
  resumable, not with the alarm.
- **Single-tenant in execution.** Storage and the read path are multi-tenant;
  `runtime.py` reads one household id from the environment, so the scheduler
  plans for exactly one household however many exist. Deliberate for v1 —
  see §16 — but new entry points should take `household_id` from the
  invocation payload rather than settings, and that is cheapest before M2 adds
  the Liaison.

## The sandbox, and what leaving it costs

Recorded because the obvious move is the wrong one. The $1.00/month SMS spend
quota is **the maximum for sandboxed accounts, not a default** — AWS's wording
is "we set the maximum spending quota for all accounts in the Sandbox at $1.00
(USD) per month" — so a Service Quotas increase on `TextMessageMonthlySpend`
is refused while the account is sandboxed. The only lever is production
access: a Support case under Account and Billing → Service Quotas → **AWS End
User Messaging SMS (Pinpoint)** → quota **SMS Production Access**, value 1,
per region.

**Do not reach for it to buy headroom.** Production access also removes the
verified-destination restriction, and that restriction is this project's real
safety net — the M1 addendum records it making an agent's stray deploy
harmless. The ceiling is not close: `notify.SMS_LENGTH_CAP` holds every
message to two GSM-7 segments, so one household's weekly send is roughly 35
message parts a month, a small fraction of the budget. Anything approaching
$0.50 is a runaway, not growth, and there is a documented runaway path already
parked below (`plan_and_notify` re-planning on every invocation). The alarm is
the answer; the quota increase is not.

## Known gaps in the research plan

- **§13's escalation scorecard has no data source.** It scores against
  labelled unsatisfiable scenarios, and real weeks are satisfiable by design.
  Those must be authored; nothing authors them.
- **The eval corpus has an unmarked prompt boundary at this commit.** The
  Planner's system prompt changed when `get_recent_weeks` started resolving
  outcomes, and `put_eval_record` stores `model_id` but nothing identifying
  the prompt — so records either side of the change are indistinguishable on
  replay. Six records predate it. Stamping a prompt version alongside
  `model_id` is the fix and is its own small item; until then the boundary is
  a date, recorded here.
- **Corpus size, not corpus correctness.** Eval records capture `model_id`,
  attempt count, validity, violations and the typed claim tags, so the Planner
  scorecard is computable. But first-pass validity over a handful of real
  weeks is an anecdote, not a rate.
- **The Curator scorecard depends on labelled backfill.** Drift cases planted
  in history are what `retirement_precision` scores against. Onboarding
  elicitation is the honest first-run path and is not in the spec at all;
  backfill remains §15's disclosed demo-data source. Both, not either.
