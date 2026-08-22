# Household intake notes — from Alex, 2026-08-20

Raw material for `data/household.json` (Task 9). Prose here, structure there.

## Safe List (as given, verbatim)

- chili
- sloppy chicken sandwiches
- beef and broccoli
- roast dinner (pork or chicken)
- burgers
- chicken fried rice  *(protein: chicken, not none)*

Plus the fixed tradition already known: **Flat Sushi Day** — grocery-store sushi
and/or Tim Hortons, Tuesdays.

## Drift case #1 (REAL — this is ground truth, not scaffolding)

**Tacos.** "Used to be a huge hit, but teenager stopped liking it."

This is the Curator's headline test case and very likely the demo's money shot:
a meal that was a reliable win, retired because one specific person went off it.
Needs a rough date for when the enthusiasm faded.

## Observation 1 — the Safe List is SIX meals, not twelve to twenty

The spec assumed 12-20. Six is what was actually reported, and it makes the
thesis *stronger*, not weaker:

- Losing tacos from a six-meal rotation removes ~14% of the entire repertoire.
- With a fence requiring chicken ~2x/week and Tuesday fixed to takeout, six meals
  across six cookable nights leaves almost no slack — the Planner will be
  genuinely constrained, which makes its choices legible rather than arbitrary.
- Safe List rot is *more* urgent in a small rotation, not less. Attrition with no
  replenishment is how a family ends up at "whatever" and takeout.

This also means auditions matter early, not as a late-stage nicety.

## Observation 2 — "roast dinner (pork or chicken)" breaks the Meal model

`Meal.protein` is a single `str | None`. This is one dish with two protein
variants, and the fence's `FrequencyTargetRule` for chicken (~2x/week) can only
match if the protein is known.

Three options:
1. **Two meals** — `roast-chicken` and `roast-pork`, same shape, different protein.
   Frequency rules work correctly. Slightly duplicates the concept.
2. **One meal, `protein=None`** — never matches a frequency rule, so a roast
   chicken night silently fails to count toward "chicken twice a week".
3. Add protein variants to the model — real scope creep, rejected.

Recommend option 1. It is honest about what actually gets cooked, and it lets the
Planner reason about protein rotation correctly.

**This is exactly the value of real data over a synthetic fixture: the first six
real meals broke a model assumption that a tidy invented list would not have.**

## Fence correction, 2026-08-20

**Chicken frequency relaxed from exactly 2 to a range of 2-3 per week.**

Why it mattered: with `max_per_week: 2` and only two chicken meals in the whole
Safe List (`sloppy-chicken`, `roast-chicken`), *both* were forced into every week —
pinning two of six cookable nights before any other rule was even considered. With
Tuesday already fixed to takeout, that left three free nights and almost no room to
avoid repeats or respect the Thursday time ceiling.

Raising the ceiling to 3 restores real choice: the Planner can repeat a chicken meal
or bring in a third once the roster grows. It also means the Planner's decisions
become interesting rather than forced, which matters for the demo — a plan with only
one legal solution shows no judgement.

Worth noting as a product observation: **a fence can be over-tight without anyone
noticing, and the symptom is constant escalation rather than an error.** The Curator
proposing new meals is what keeps the fence satisfiable as the roster shrinks.

## Correction 2: fried rice is CHICKEN fried rice

Reported 2026-08-20. This matters twice over.

**It changes the constraint arithmetic.** The Safe List holds *three* chicken meals,
not two: `sloppy-chicken`, `roast-chicken`, `fried-rice`. Together with the ceiling
moving to 3, the fence goes from nearly forced to comfortably satisfiable.

**It reveals the household's real cook-once pattern.** A roast chicken earlier in the
week produces the chicken that becomes fried rice later. That is precisely the
"size up a cook-once meal, treat the second meal as a bonus" mechanic the design
calls for — except it is not an abstraction here, it is what this family already does.
Tagged `uses-leftovers` so a later task can sequence the pair deliberately.

Worth carrying into the Planner's prompt (Task 11): a rationale that says
*"chicken fried rice — Sunday's roast is still in the fridge"* demonstrates the agent
understands the household, not just the constraints. That is the difference between a
plan and a plan someone trusts.

## Beef: added as a rule, then deliberately removed

A beef ceiling of 2/week was requested (a cook's preference). It went in, and the
constraint analysis immediately showed why it could not stay: with chicken capped at 3
and beef at 2 across six cookable nights, the maximum chicken+beef is 5 — so
**`roast-pork` was forced into every single legal week.** A 90-minute Sunday roast,
mandatory, every week, forever. Verified by constructing all six legal compositions,
not just by arithmetic.

The fence went back to four rules. The beef preference moves into the Planner's prompt
as guidance: honoured in most weeks, never blocking a plan, never triggering an
escalation.

**The general lesson, worth keeping:** a six-meal roster cannot carry many hard rules.
Each one removes a large fraction of the solution space, and the symptom is not an error
— it is a plan that never varies, or an agent that escalates constantly. Growing the
roster is what buys freedom, which is precisely the Curator's job.

## The household

| person_id | name | role |
|---|---|---|
| `alex` | Alex | cook |
| `morgan` | Morgan | cook |
| `riley` | Riley | teenager — the tacos drift case |
| `jesse` | Jesse | tween |

Person ids are first names, matching `alex`. They surface in the family page and in the
Curator's reasoning, so they should read like people rather than roles.

**Jesse is a tween, not a small child.** Old enough to have opinions worth weighting —
relevant when the Liaison starts accepting free-text input from the family.

## Why the drift case reads better now

`teen-tacos` became `riley-tacos`, and the sentence the Curator will eventually produce
changes from *"teen stopped eating tacos"* to **"Riley stopped eating tacos."**

That is the whole product in one line. The first is a data model; the second is the
thing that makes someone watching say *yes, that happens in my house too.* Tacos were a
reliable win for years, one specific person quietly went off them, nobody noticed, and
the rotation shrank from seven meals to six without anyone deciding it should.

## Who cooks is not the Planner's decision

Alex, on reading the first real week (2026-08-20):

> "I would say don't assume Cook. That could be more of a role in the app. Morgan
> and I may be co-cooks but who cooks is not a fixed thing."

The Planner had been assigning `alex` or `morgan` to each night. It looked
authoritative and was invented — which is worse than blank, because a family
reading the plan would believe it meant something.

**Rule now:** the Planner never *decides* a cook. It only *copies* one when the
fence contains an `AssignmentRule` for that weekday — a decision the household
already made and wrote down. This household has none, so `cook_id` stays unset.

**Product idea worth keeping for M2** (Alex's "could be more of a role in the app"):
claiming a night is a *family interaction*, not a planning output. Someone opens the
week and says "I'll take Thursday." That belongs on the shared page alongside
reactions and suggestions — it is exactly the kind of low-effort, high-signal input
the page exists to collect, and it answers a question the household genuinely
negotiates rather than one an agent can infer.

**The general principle, worth carrying:** an agent should decline to decide things
it cannot know, and the decline should be visible. A blank cook field is honest. A
confidently wrong one erodes trust in every other line on the plan.

## The drift case is now real ground truth

`riley / tacos / 2026-06-01` — reported 2026-08-20 as "about two or three months ago",
so accurate to within a few weeks, which is fine. Drift is not a dated event; it is a
fade that nobody notices happening.

**But the evidence for it does not exist yet, and that is the gap that matters for M3.**

The label says Riley went off tacos in June. The seeded history is two weeks, both in
August. So the Curator has ground truth with nothing to detect it *from* — no record of
tacos being eaten in the spring, no signals from Riley fading, no moment where the meal
quietly stops appearing.

For the Curator to be measurable rather than merely asserted, the backfill needs history
spanning roughly **March through August**, with three things visible in it:

1. Tacos appearing regularly in the earlier months
2. Riley's signals on tacos degrading over time — enthusiasm fading, not complaint arriving
3. Tacos ceasing to appear around June, without any explicit decision recorded

That third point is the honest one. Nobody retired tacos. They just stopped getting
cooked, because Alex noticed at some level that they were not landing any more. The
Curator's job is to make that visible and deliberate instead of silent — which is only
demonstrable if the silence is in the data.

**This is the concrete shape of the "real rotation" ask.** Not six months of precise
recall: a plausible rotation across those months with tacos fading out of it.

## Still needed

- Rough cook time and main protein per meal (I will guess; correct me)
- Roughly when the teenager went off tacos
- Names of everyone in the house, and who cooks
- Whether anyone besides Alex cooks, and on which nights
- Any other drift cases — meals someone quietly stopped eating
