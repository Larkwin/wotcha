# Status — where wotcha stands

Living document. Updated 2026-08-23, the evening before dogfooding starts.
`docs/m1-handoff.md` is the M1 record and still authoritative for the runbook;
this is the ledger of what exists, what is next, and what is deliberately not
being done yet.

## Live state

| | |
|---|---|
| Repo | `github.com/Larkwin/wotcha`, **public**, CI on every push and PR |
| Suite | 355 tests, lint clean, Python 3.12 and 3.14 |
| Region / model | `ca-central-1`, `us.anthropic.claude-sonnet-4-6` |
| Model ladder | 4 rungs round-tripped from `ca-central-1`; Opus 5 gated on account model access |
| Runtime | deployed, `WOTCHA_CHANNEL=sms`, live origination number |
| Weekly schedule | `cron(0 9 ? * SAT *)` America/Toronto, **DISABLED** — first kickoff is manual, by choice |
| Table | one household: 4 members, 9 meals, 4 weeks, 3 signals, 1 drift case, 6 eval records |
| Reachable | one handset verified. **The rest of the family is not yet on file.** |
| Inbound SMS | two-way **live and proven** on the long code — SNS topic → SQS queue + DLQ, in CDK. Consumer: Liaison Lambda |
| Liaison | inbound → `SUGGESTION#` → cook's page, deployed. **Never exercised on a real message.** |
| SMS budget | $1.00/month. In the sandbox that is the **maximum**, not a default — see below. Alarm at $0.50, deployed, one confirmed subscriber |

## Done

**M0 — prove the pipe.** Repo, MIT licence, one deploy command, DynamoDB
schema, Strands agent on AgentCore Runtime, Bedrock access verified per model
and per region, SMS deliverability confirmed on real hardware.

**M1 — the family-usable slice.** Fence, validator and Planner producing a
weekly plan; SMS with a permanent per-person signed link; shared page
accepting reactions; eval logging from the first real week. Deployed, and one
real week has been planned, published and texted.

**Since M1:**

- **The Liaison is deployed, and it only ever proposes.** An inbound text from a
  known member becomes one `SUGGESTION#` row; the cook approves it on their
  page and approval creates a `Meal` with `status=CANDIDATE` — the status
  that had existed as the model default since M1 and never once been used.
  A candidate is on the roster and not plannable, so approving adds an idea
  without putting it on next week's table.

  The agent has no tools. The roster is rendered into its prompt, so there is
  no mechanism by which it could reach the roster, the fence or a week — it
  returns a value and the consumer decides what to persist. A model outage
  degrades to an honest `unknown` read rather than a lost message.

  Signals, outcomes and replies are still deferred: the first two need
  attribution the agent is not yet trusted with, the third spends against the
  SMS ceiling. What survives is the **label** — every suggestion records the
  `kind` the agent believed it to be, so `extraction_accuracy` is measurable
  before the extraction is trusted.

  Runs on `ca.amazon.nova-lite-v1:0` via `WOTCHA_LIAISON_MODEL_ID`, and writes
  `kind: "extraction"` eval records — the corpus now has two agents in it
  rather than one.
- **Model ladder settled** — Strands' `BedrockModel` reaches the open-weight
  rungs through Project Mantle, so §13 keeps all five rather than degrading to
  three. Note the catalogue does not list them; round-trip before concluding
  anything is unavailable.
- **The ladder does not need us-east-1** (corrects the note above, 2026-08-24).
  That note assumed the open-weight rungs were DeepSeek and Qwen, which are
  genuinely us-east-1-only. They are not the only open-weight models: Llama
  and Mistral are `ON_DEMAND` in `ca-central-1`. Four rungs now round-trip
  from the product region — `us.anthropic.claude-sonnet-4-6`,
  `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `ca.amazon.nova-lite-v1:0`
  and `global.amazon.nova-2-lite-v1:0` — with `meta.llama3-70b-instruct-v1:0`
  and `mistral.mixtral-8x7b-instruct-v0:1` added to the candidate list and not
  yet round-tripped.

  Two identifier bugs sat behind that. `amazon.nova-lite-v1:0` was annotated
  "us-east-1 only" when `ca.amazon.nova-lite-v1:0` was ACTIVE in-region all
  along: a profile-only model refuses its bare id with "on-demand throughput
  isn't supported", which reads like absence. And `list_foundation_models`
  returns bare ids, so the discovery half of `preflight_bedrock.py` could
  never surface a profile id — it now prints inference profiles and each
  model's `inferenceTypesSupported` alongside.

  **The Llama and Mistral ids were never hidden.** `list_available` has
  matched "llama" and "mistral" since the script was written, so they printed
  on every run and nobody read them. Worth remembering the next time a
  capability looks absent: check what is already on the screen before
  concluding the region lacks it.
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
- **Inbound SMS proven in the sandbox.** The M2 structural gate, and it was
  the one thing nobody could answer from documentation: every stated sandbox
  restriction is outbound and spend, which is absence of mention rather than a
  guarantee. Two-way was enabled on the long code and a text from a verified
  handset landed in the queue. No production access needed.

  It also corrected `docs/two-way-sms-setup.md` on its central point. That
  document said `--two-way-channel-arn` accepts an SQS queue; AWS accepts only
  an SNS topic or Connect Customer. The claim came from CLI help that says
  merely "the ARN of the two way channel" — an inference written down as a
  fact, of exactly the kind the Nova model id turned out to be. The shape is
  now inbound → SNS topic → SQS queue → Liaison, which keeps every durability
  property the original reasoning wanted. Two smaller corrections came with
  it: no IAM role is needed (a topic policy suffices), and `RawMessageDelivery`
  must stay off, because the inbound payload has no timestamp of its own and
  the SNS envelope is the only thing that can approximate ordering on a
  channel that guarantees none.
- **The inbound transport is in CDK**, under the same names the hand-built
  resources had. SQS and SNS ARNs are derived from their names, so recreating
  them under CloudFormation produced identical ARNs and the phone number's
  `TwoWayChannelArn` never had to be re-pointed. That also makes the names
  load-bearing with no warning attached: `--two-way-channel-arn` is a setting
  on the number, not a stack resource, so a rename would leave it publishing
  to an ARN that no longer resolves — silently.
  `tests/test_inbound_stack.py` pins the names, the retention, the
  dead-letter wiring and the confused-deputy conditions. The phone number
  itself is deliberately *not* a stack resource: putting a verified
  origination identity under CloudFormation means a stack mistake could
  release it.
- **Escalations resolve themselves.** `escalate` wrote `resolved: False`,
  `unresolved_escalations` read it, and nothing ever set it true — the first
  unsatisfiable week would have left a question open permanently. Resolution
  is now derived at read time in `domain/escalations.py`, the same shape as
  `outcomes.py`: a `fence_unsatisfiable` question naming a week that has
  since been published is answered, because the published week *is* the
  answer. A stored `resolved: True` still wins, so M2's inbound reply has
  somewhere to land. Nothing is written and nothing is deleted — the
  `ESCALATION#` history stays whole for the reliability corpus; only the open
  view narrows.
- **`_tell_the_cook`'s fallback narrowed to weekless rows.** It reached for
  any open escalation when the target week had none, which was harmless while
  everything stayed open forever. It is not harmless now that `retirement`
  and `new_constraint` rows stay open by design: a run that failed to publish
  *without* escalating (exhausted attempt cap, a crash) would have handed the
  cook a months-old retirement question labelled with the wrong week. The
  fallback now covers only rows that name no week at all, which is what it
  was written for.
- **`preflight_sms.py spend`**, wired into `make preflight`. Answers the two
  questions the alarm cannot answer about itself: is the metric publishing at
  all, and would the alarm reach a person. No datapoints is reported as
  *unknown*, never as $0.00 — the metric needs the service-linked role and
  does not publish until the first message, so an alarm on a metric nobody
  publishes sits in INSUFFICIENT_DATA forever.

## Next — ordered

1. **M2 — Liaison v1.5.** The Liaison transport is **live and proven** — a real
   text landed in the queue on 2026-08-24. v1 is deployed and has never exercised
   on a real message; `lambdas/liaison.py` writes `SUGGESTION#` rows. What is left
   of M2 is the second round of inbound: free-text **signals** (what the household
   ate), **disruption capture** (takeout, skipped, swapped), and **"what's for
   dinner tonight?"** — all deferred from v1 as too risky or expensive.

   **Signals and disruptions** need attribution that traces a correction to a
   person and a night, not just classification. The agent cannot infer that
   reliably. v1's `kind` label — recorded on every suggestion — is the data
   point that will let v1.5 turn that on: once extraction accuracy is proven,
   the inference risk is a known number, not a hope.

   **"What's for dinner tonight?"** costs money; v1's budget is $1.00/month.
   The feature goes in after rate limiting can cap the damage, or after the
   Curator (M3) has proven enough adoption to justify a budget raise.
2. **M3 — Curator.** Standings, decay detection, retirement, auditions. The
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
- **Enable Opus 5 model access** if §13 wants that rung. It is the only
  candidate still failing, and it fails with `AccessDenied` rather than
  `ValidationException` — the profile is ACTIVE in `ca-central-1`, so this is
  account model access, not availability. Bedrock console → Model access.
- **Re-subscribe if the alarm topic is ever recreated.** CDK creates
  `wotcha-alarms` unsubscribed on purpose — an email subscription lands in the
  synthesized template and in the `make deploy` line that gets pasted into
  runbooks, and this repo is public. One address is confirmed as of
  2026-08-24; `make preflight` reports the count on every run, and a topic
  with no confirmed subscriber is an alarm that fires into nothing.
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
- **Off-list nights have no name field.** An off-list outcome records only the
  flag `Substitute.OFF_LIST` with no name — the household ate something, and the
  system does not know what. The flag is valuable as-is: off-list nights are the
  roster's own health metric, the evidence behind a Curator proposing an
  audition, countable precisely because they remain distinguishable from silence.
  The open boundary is that there is nowhere to put the name *if* someone wanted
  to say what they ate instead — that field does not exist on the `/o/` path, and
  if it did, what it captured would be a suggestion (`Suggestion`'s table).
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
- **`retirement` and `new_constraint` escalations never close.** A published
  week answers a fence question; it says nothing about whether a meal should
  be retired or a new hard constraint accepted. Nothing in the system can
  know those were settled, and inventing a signal — ageing them out, or
  reading the Planner's silence as assent — is exactly what
  `household-notes.md` forbids. They accumulate, but the same-week filter and
  the `notified_at` guard mean they never re-text anyone, and the narrowed
  fallback means they can no longer be mistaken for this week's question.
  They close in M2, when the cook can answer one.
- **Single-tenant in execution.** Storage and the read path are multi-tenant;
  `runtime.py` reads one household id from the environment, so the scheduler
  plans for exactly one household however many exist. Deliberate for v1 —
  see §16. The Liaison (`lambdas/liaison.py`) also reads from environment, not
  from invocation payload, because SQS-triggered Lambda has no payload to carry
  a household id. Multi-tenant inbound needs a different approach: the transport
  already knows which number the text reached (`destinationNumber` is in the
  SNS payload), so household is a number-to-household lookup, not a parameter
  threaded through the Lambda chain.

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

Now planned properly in `docs/model-evaluation.md`, which carries the corrected
model ladder, the per-agent scorecards with a data-status column, and the
replay harness design. **It is a plan to execute after M3, not now** — §13's
first guardrail is that the study "has strong gravity and could quietly become
the project", and that assessment stands.


- **§13's escalation scorecard has no data source.** It scores against
  labelled unsatisfiable scenarios, and real weeks are satisfiable by design.
  Those must be authored; nothing authors them.
- **The eval corpus has an unmarked prompt boundary at 2026-08-24.** The
  Planner's system prompt changed when `get_recent_weeks` started resolving
  outcomes, and `put_eval_record` stores `model_id` but nothing identifying
  the prompt — so records either side of the change are indistinguishable on
  replay. Six records predate it. Stamping a prompt version alongside
  `model_id` is the fix, it gets harder to backfill every week, and it is one
  of four data-capture gaps now enumerated in `docs/model-evaluation.md`.
- **Corpus size, not corpus correctness.** Eval records capture `model_id`,
  attempt count, validity, violations and the typed claim tags, so the Planner
  scorecard is computable. But first-pass validity over a handful of real
  weeks is an anecdote, not a rate.
- **The Curator scorecard depends on labelled backfill.** Drift cases planted
  in history are what `retirement_precision` scores against. Onboarding
  elicitation is the honest first-run path and is not in the spec at all;
  backfill remains §15's disclosed demo-data source. Both, not either.
