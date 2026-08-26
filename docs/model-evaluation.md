# The Minimum Viable Model study — evaluation plan

**What is the cheapest model that can actually do this job?**

A household agent runs forever on tiny volume. Frontier capability is the
obvious default and almost certainly overkill for most of the work. This is the
plan for answering that properly, on real logged traffic rather than benchmarks.

Design spec §13 states the question and the scorecards. This document is the
executable plan: what the harness is, what data exists, what is missing, and
what has changed since §13 was written.

---

## Read this part first

§13 sets three hard guardrails, and the first one is the important one:

> **This does not start until M3 is complete.** It has strong gravity and could
> quietly become the project.

That is a correct assessment and this document does not soften it. Everything
below is a plan to execute *later*, written now because the plan is cheap and
the execution is not. Two consequences worth stating plainly:

1. **Writing this down is not starting it.** The value of planning now is that
   the data-capture requirements become visible while the code that produces
   the data is still being written — see "What the product must log", below.
2. **The remaining guardrails still apply.** One working session to first
   numbers; if there is no scorecard by then it ships reduced, as harness plus
   the Planner sweep only.

---

## Why this is tractable here

**The fence validator is a deterministic scorer.** "Did the model produce a
legal week" is machine-checkable — no human judgment, no LLM-as-judge
hand-waving. Most metrics fall out of artifacts the system already produces.

**The eval data comes free from dogfooding.** Every proposal, validation result
and inbound message is logged from M1 onward. The sweep is then a *replay of
logged real scenarios* against N models: near-zero cost during the build, and a
high-value artifact at the end.

This is the core methodological claim, and it is also the honest limit — see
"Known gaps" for where it does not hold.

---

## The model ladder, corrected

§13's table was verified on 2026-08-20. Re-verified 2026-08-24 by round-trip,
which changed several entries. **Round-trip, never a catalogue listing:**
`list_foundation_models` returns bare model ids, and a bare id is precisely what
cannot be invoked for a profile-only model.

| Rung | Identifier | Status |
|---|---|---|
| Ceiling | `us.anthropic.claude-sonnet-4-6` | ✅ round-tripped, `ca-central-1` |
| Cheap frontier | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | ✅ round-tripped |
| Managed baseline | `ca.amazon.nova-lite-v1:0` | ✅ round-tripped |
| Managed baseline | `global.amazon.nova-2-lite-v1:0` | ✅ round-tripped |
| Open-weight large | `meta.llama3-70b-instruct-v1:0` | ON_DEMAND in region, **not yet round-tripped** |
| Open-weight MoE | `mistral.mixtral-8x7b-instruct-v0:1` | ON_DEMAND in region, **not yet round-tripped** |
| Open-weight | `deepseek.v3.2` | ON_DEMAND, `us-east-1` only |
| Open-weight | `qwen.qwen3-32b-v1:0` | ON_DEMAND, `us-east-1` only |
| Frontier ceiling | `us.anthropic.claude-opus-5` | ❌ profile ACTIVE in region; **account lacks model access** |

### What changed since §13

**Nova Lite is in the product region.** §13 listed it as "us-east-1 (needs a
profile in ca-central-1)". `ca.amazon.nova-lite-v1:0` is ACTIVE in
`ca-central-1` and round-trips. The bare id `amazon.nova-lite-v1:0` fails with
"Invocation of model ID ... with on-demand throughput isn't supported", which
reads like an availability problem and was recorded as one for months.

**Two naming regimes, and no rule of thumb spans them.** A model's
`inferenceTypesSupported` decides how it must be named:

- `ON_DEMAND` — the bare id works. DeepSeek and Qwen are this.
- `INFERENCE_PROFILE` — the bare id is refused; name a profile
  (`us.` / `global.` / `ca.` / `eu.` / `apac.`). All modern Anthropic models
  and both Novas are this.

**The sweep may not need `us-east-1` at all.** §13 concluded the product runs
in `ca-central-1` and the sweep runs against `us-east-1`, having noted that
`ca-central-1` carries "only Llama 3 and Mixtral" and judging those not modern.
That was a deliberate judgement, not an oversight — but it is cheap to test,
because both are in-region and on-demand. If either performs acceptably the
sweep collapses into one region and Canadian data residency extends to the
research as well as the product. **That is itself a finding worth reporting.**

**Opus 5 is a model-access question, not a region one.** It fails with
`AccessDenied`, not `ValidationException` — the profile is ACTIVE in-region and
the account simply is not granted it. One console action if §13 wants that rung.

---

## Scorecards

### Planner — the primary sweep target

Entirely objective; every metric is computable from stored artifacts.

| Metric | Definition | Data status |
|---|---|---|
| `first_pass_validity` | % of proposals passing the fence with zero revisions | ✅ `EVAL#` records carry `attempt` and `valid` |
| `revisions_to_valid` | Mean propose→validate→revise cycles | ✅ same |
| `escalation_precision` / `recall` | Against labelled unsatisfiable scenarios | ❌ **no data source** — see gaps |
| `rationale_faithfulness` | Typed claim tags only, checked against stored fields | ✅ claims stored per slot |
| `cost_per_plan`, `latency` | Usage metering | ⚠️ not currently captured — see below |

`rationale_faithfulness` is deliberately scoped to the five machine-verifiable
claim tags (`fits_time_ceiling`, `no_protein_repeat`, `respects_assignment`,
`within_takeout_budget`, `is_audition`). Free-text flourishes are **not scored**;
grading those needs a judge, which is out of scope.

### Liaison — objective

| Metric | Definition | Data status |
|---|---|---|
| `extraction_accuracy` | Labelled inbound messages → expected typed record | ⚠️ needs labels; **corpus is live** — first real `extraction` record written 2026-08-24 |
| `injection_resistance` | Whether the model attempts out-of-scope action, claims authority it lacks, or leaks system context | ❌ **suite must be authored** |
| `cost_per_message` | Usage metering | ⚠️ not currently captured |

**The Liaison v1 design makes `extraction_accuracy` scorable before the
extraction is trusted.** It records a `kind` label on every suggestion —
`new_meal`, `existing_meal`, or a non-suggestion class — without acting on
anything but suggestions. The label is the eval artifact; the restraint is the
safety property. Turning the other kinds on becomes a decision backed by a
number rather than a hope.

**`injection_resistance` needs an observable channel.** A constrained output
schema means the model cannot express "I have changed the fence" in a typed
field. The Liaison's free-text `note` is therefore where an attempt becomes
visible, and that is deliberate rather than incidental.

### Curator — hardest, still objective if the backfill is labelled

| Metric | Definition | Data status |
|---|---|---|
| `retirement_precision` / `recall` | Against drift cases planted in backfilled history | ⚠️ one case exists; evidence does not |

---

## What the product must log

The single reason to write this plan before M3: **a metric with no data source
is not a metric, and the cheapest moment to fix that is while the code is being
written.**

| Requirement | State |
|---|---|
| Every Planner attempt logged, including crashes and cap-hits | ✅ done — `EVAL#`, `kind: "validation"` / `"publish_refusal"` |
| Publish-time refusals distinguishable from validation attempts | ✅ done — separate `kind` |
| Liaison extraction logged | ⬜ v1 requirement — `kind: "extraction"` |
| `model_id` on every record | ✅ done |
| **Prompt version on every record** | ❌ **missing** — see gaps |
| Token counts and latency per call | ❌ missing — needed for `cost_per_*` |

---

## The harness

A replay, not a live re-run. Nothing texts anyone; nothing writes to the
household's table.

1. **Extract** logged scenarios from `EVAL#` records — for the Planner, the
   `week_start` plus the household state as of that week.
2. **Replay** each scenario against each rung, with the fence validator as the
   scorer. Per-agent model selection is one line
   (`BedrockModel(model_id=...)`), so the ladder is a loop.
3. **Score** into a table, per agent per rung.
4. **Report** — the artifact is the builder.aws.com post, not just numbers.

The expected result is that the Liaison runs fine on something small, the
Planner needs a middle tier, and the Curator wants real capability.
**The interesting outcome is wherever that expectation turns out to be wrong.**

---

## Known gaps — each one blocks a specific metric

**No labelled unsatisfiable scenarios.** `escalation_precision` / `recall`
scores against weeks where the fence genuinely cannot be satisfied. Real weeks
are satisfiable by design, so these must be *authored* — and nothing authors
them. Without that, two Planner metrics are uncomputable regardless of how good
the replay harness is.

**No jailbreak suite.** `injection_resistance` needs an adversarial corpus. §14
places the jailbreak demo at M4; the suite it demos is the same artifact and
should be written once.

**The eval corpus has an unmarked prompt boundary.** `put_eval_record` stores
`model_id` and nothing identifying the prompt. The Planner's system prompt
changed on 2026-08-24 when `get_recent_weeks` began resolving outcomes, so
records either side are indistinguishable on replay. Six records predate it.
**Stamping a prompt version alongside `model_id` is a small change that gets
harder to backfill every week.**

**No token counts or latency.** `cost_per_plan` and `cost_per_message` are in
both scorecards and nothing currently records usage. Strands surfaces it; it is
not being persisted.

**The Liaison's corpus has exactly one real record.** As of 2026-08-24 the
path works end to end on `ca.amazon.nova-lite-v1:0` and the read was usable —
which answers "is the cheap rung obviously unfit" with a no, and answers
nothing else. `extraction_accuracy` still needs labelled expected outputs, and
one message is an anecdote by the same standard applied to the Planner below.

**Corpus size, not corpus correctness.** First-pass validity over a handful of
real weeks is an anecdote, not a rate. This resolves only with time, and it is
the honest reason the study is gated on M3 rather than on a code change.

**The Curator's ground truth has no evidence.** `DRIFTCASE#riley-tacos` records
that Riley went off tacos around 2026-06-01. Seeded history is two weeks in
August. The Curator would have a labelled answer and nothing to derive it from.
What is needed is a plausible March–August rotation in which tacos appear
regularly, enthusiasm fades, and they stop being cooked with **no explicit
retirement recorded** — because that silence is the entire thesis.

---

## What "done" looks like

A scorecard table per agent per rung, a recommendation per agent, and a written
account of where the expectation was wrong. Reduced form if the session cap
hits: harness plus the Planner sweep only.

The strongest possible finding is not "the cheap model works." It is a specific,
measured boundary — *this task needs this much capability and no more* — on a
workload small enough that the difference is the whole cost of ownership.
