# Status — where wotcha stands

Living document. Updated 2026-08-23, the evening before dogfooding starts.
`docs/m1-handoff.md` is the M1 record and still authoritative for the runbook;
this is the ledger of what exists, what is next, and what is deliberately not
being done yet.

## Live state

| | |
|---|---|
| Repo | `github.com/Larkwin/wotcha`, **public**, CI on every push and PR |
| Suite | 254 tests, lint clean, Python 3.12 and 3.14 |
| Region / model | `ca-central-1`, `us.anthropic.claude-sonnet-4-6` |
| Runtime | deployed, `WOTCHA_CHANNEL=sms`, live origination number |
| Weekly schedule | `cron(0 9 ? * SAT *)` America/Toronto, **DISABLED** — first kickoff is manual, by choice |
| Table | one household: 4 members, 9 meals, 4 weeks, 3 signals, 1 drift case, 6 eval records |
| Reachable | one handset verified. **The rest of the family is not yet on file.** |
| SMS budget | `TEXT_MESSAGE_MONTHLY_SPEND_LIMIT` $1.00/month, cannot be raised in sandbox |

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

## Next — ordered

1. **Planner reads outcomes.** `get_recent_weeks` hands the Planner
   `slot.outcome`, which stays `planned` on live weeks, so a corrected night
   still reads as eaten. Route history through `resolve_outcome`. Small, and
   it is the other half of outcome capture — the clock is next Saturday's
   plan, generated from this week's history.
2. **Alarm on the SMS spend limit.** $1.00/month, unraisable, and hitting it
   stops sends silently mid-month. Same silent-failure class as the IAM grant.
3. **Escalation resolution.** Written `resolved: False`, read, never settled —
   nothing sets it true. The first unsatisfiable week asks the cook a question
   they cannot answer.
4. **M2 — Liaison and two-way SMS.** The structural gate: inbound free-text
   signals, suggestions, disruption capture and "what's for dinner tonight?"
   all arrive through it. Needs the SQS queue, DLQ and IAM role in
   `docs/two-way-sms-setup.md`; the existing long code is already the right
   number. **Verify inbound works in the sandbox** — the documented
   restrictions are all outbound and spend, but that is absence of mention,
   not a guarantee.
5. **M3 — Curator.** Standings, decay detection, retirement, auditions. The
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
- **Single-tenant in execution.** Storage and the read path are multi-tenant;
  `runtime.py` reads one household id from the environment, so the scheduler
  plans for exactly one household however many exist. Deliberate for v1 —
  see §16 — but new entry points should take `household_id` from the
  invocation payload rather than settings, and that is cheapest before M2 adds
  the Liaison.

## Known gaps in the research plan

- **§13's escalation scorecard has no data source.** It scores against
  labelled unsatisfiable scenarios, and real weeks are satisfiable by design.
  Those must be authored; nothing authors them.
- **Corpus size, not corpus correctness.** Eval records capture `model_id`,
  attempt count, validity, violations and the typed claim tags, so the Planner
  scorecard is computable. But first-pass validity over a handful of real
  weeks is an anecdote, not a rate.
- **The Curator scorecard depends on labelled backfill.** Drift cases planted
  in history are what `retirement_precision` scores against. Onboarding
  elicitation is the honest first-run path and is not in the spec at all;
  backfill remains §15's disclosed demo-data source. Both, not either.
