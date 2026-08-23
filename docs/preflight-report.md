# Preflight Report

**Run:** 2026-08-20
**Account tested:** `<AWS_ACCOUNT_ID>`, default region `ca-central-1`
**Excluded:** a separate client account in the same organisation is out of scope and is not to be used.

---

## Bedrock model access — **partially blocked**

Bare model ids (`anthropic.claude-opus-5`) are rejected; current models require an
inference profile id (`us.` or `global.` prefix). With profiles applied:

| Model | ca-central-1 | us-east-1 |
|---|---|---|
| `us.anthropic.claude-opus-5` | AccessDenied | AccessDenied |
| `us.anthropic.claude-opus-4-8` | — | AccessDenied |
| `us.anthropic.claude-opus-4-7` | — | AccessDenied |
| `us.anthropic.claude-sonnet-5` | — | AccessDenied |
| `us.anthropic.claude-fable-5` | — | AccessDenied |
| **`us.anthropic.claude-sonnet-4-6`** | — | **works** |
| **`us.anthropic.claude-haiku-4-5-20251001-v1:0`** | **works** | **works** |
| `amazon.nova-lite-v1:0` | needs profile | **works** |
| `deepseek.v3.2` | not offered | **works** |
| `qwen.qwen3-32b-v1:0` | not offered | **works** |

Error text for the denied tier:

> `anthropic.claude-opus-5 is not available for this account. ... For additional
> access options, contact AWS Sales.`

**That message is misleading.** `get-foundation-model-availability` shows what is
actually missing:

| Model | agreementAvailability | authorizationStatus | entitlementAvailability |
|---|---|---|---|
| `anthropic.claude-opus-5` | **NOT_AVAILABLE** | AUTHORIZED | AVAILABLE |
| `anthropic.claude-sonnet-5` | **NOT_AVAILABLE** | AUTHORIZED | AVAILABLE |
| `anthropic.claude-sonnet-4-6` | AVAILABLE | AUTHORIZED | AVAILABLE |

The account is authorized and entitled. Only the **model agreement** is absent —
self-serve, one command, no sales conversation:

```bash
aws bedrock list-foundation-model-agreement-offers --model-id anthropic.claude-opus-5
aws bedrock create-foundation-model-agreement --offer-token <token>
```

A published offer exists (`offer-f3u6lgbrem3zs`) at standard rates: $5 / $25 per
million input/output tokens, $0.50 cache read. No fixed fee. The Larkwin use-case
form is already on file.

**Decision, 2026-08-20: declined.** Sonnet 4.6 handles tool use correctly
(`stopReason: tool_use` with a well-formed tool block, verified), and a household
agent's weekly volume does not need a frontier ceiling. **The working ceiling is
`us.anthropic.claude-sonnet-4-6`**, with `us.anthropic.claude-haiku-4-5-20251001-v1:0`
as the cheap tier. The agreement remains one command away if §13 later shows
Sonnet 4.6 is the binding constraint rather than the answer.

**Note:** bare model ids are rejected outright. Every call must use an inference
profile id — a `us.` or `global.` prefix.

## Region

| | ca-central-1 | us-east-1 | us-west-2 |
|---|---|---|---|
| Models offered | 34 | 121 | 114 |
| Modern open-weight (DeepSeek V3.2, Qwen3) | **none** | yes | yes |
| Legacy open-weight | Llama 3, Mixtral | yes | yes |
| Haiku 4.5 | works | works | — |
| AgentCore control plane | reachable | reachable | reachable |

`ca-central-1` keeps household data in Canada but offers no modern open-weight
models, which would gut the model ladder in spec §13.

**Resolution:** run the product in `ca-central-1` and run the §13 sweep against
`us-east-1`. The sweep is an offline replay of logged scenarios, so it has no
reason to share a region with the product. Canadian data residency and the full
model ladder are not actually in conflict.

## AgentCore — **available, and now proven end to end (Task 5, 2026-08-20)**

`bedrock-agentcore-control list-agent-runtimes` returns cleanly (empty list, no
error) in `ca-central-1`, `us-east-1`, and `us-west-2`. No runtimes deployed at
preflight time.

The largest architectural risk in spec §17 is cleared: AgentCore Runtime hosting
is viable, including in Canada.

**Task 5 deployed a hello-world Strands agent (ping-only) as a container to
AgentCore Runtime in `ca-central-1` and invoked it remotely, successfully:**

| | |
|---|---|
| Runtime ARN | `arn:aws:bedrock-agentcore:ca-central-1:<AWS_ACCOUNT_ID>:runtime/<RUNTIME_ID>` |
| Region | `ca-central-1` |
| Deployment path | `bedrock-agentcore-starter-toolkit` 0.3.12, container deployment type, cloud CodeBuild build (ARM64) — no local Docker build required |
| Execution role (auto-created by toolkit) | `arn:aws:iam::<AWS_ACCOUNT_ID>:role/<EXECUTION_ROLE_NAME>` |
| Remote invoke | `agentcore invoke '{"action":"ping"}'` → `{"ok": true, "action": "ping", "reply": "ready"}` |

Full record, including the local run evidence, exact commands, and deviations
from the task-5 brief's Dockerfile/CLI assumptions, is in
`.superpowers/sdd/2026-08-20-wotcha-m0-m1/task-5-report.md`.

## SMS — **nothing provisioned; now the longest-lead item**

| Check | ca-central-1 | us-east-1 |
|---|---|---|
| Account tier | `SANDBOX` | `SANDBOX` |
| Origination phone numbers | **<SENDING_NUMBER>** (see below) | none |
| Verified destination numbers | **none** | none |

**Canadian long code provisioned 2026-08-20** — and it went `ACTIVE` in under a
minute, which confirms the research empirically: Canada has no Campaign Registry
gate, so there was no approval queue to sit in.

| | |
|---|---|
| Number | `<SENDING_NUMBER>` |
| Id (`WOTCHA_SMS_ORIGINATION_ID`) | deliberately not recorded here -- read it from `aws pinpoint-sms-voice-v2 describe-phone-numbers --region ca-central-1` |
| Type | CA `LONG_CODE`, SMS, `TRANSACTIONAL` |
| Cost | $1.00 / month |
| Two-way | disabled — enabled at M2 per `docs/two-way-sms-setup.md` |

Sandbox is expected and sufficient (spec §12: up to 10 verified destinations).
But no number has been requested and no family handset verified, so the entire
delivery path is unproven. Canada requires no Campaign Registry gate, so a
Canadian long code should provision quickly — **request it first.**

## Open items

- [x] ~~Request a Canadian long code~~ — done 2026-08-20, `ACTIVE` immediately
- [x] ~~Does a Canadian carrier deliver from an unregistered long code?~~ —
      **YES, confirmed on real hardware 2026-08-20.** A verification text sent from
      `<SENDING_NUMBER>` arrived on a Canadian handset within a minute, main
      thread, not filtered. **SMS is the confirmed channel; email stays the unused
      fallback.** This was the last assumption in the design that research alone
      could not settle.
- [ ] Verify the remaining family handsets (mechanical now -- the risk is retired).
      The procedure is README step 9, which is authoritative: numbers into
      `data/household.json`, re-seed, then `preflight_sms.py verify`/`confirm`
      per handset.
- [x] ~~Verify whether Strands' `BedrockModel` reaches `deepseek.v3.2` and
      `qwen.qwen3-32b-v1:0` in us-east-1~~ — **YES, confirmed 2026-08-22.** Both
      round-tripped a prompt through `BedrockModel` with no Mantle-specific
      configuration, so §13's third hard guardrail is satisfied and the ladder
      keeps all five rungs.

      **`list_foundation_models` does not list either id.** The us-east-1
      catalogue offers `deepseek.r1-v1:0`, `qwen.qwen3-vl-235b` and
      `qwen.qwen3-coder-30b-a3b-v1:0` — not `deepseek.v3.2` or
      `qwen.qwen3-32b-v1:0` — yet both answer. Mantle-served models are
      reachable without appearing in the catalogue, so a listing is evidence a
      model exists, never evidence one is missing. Round-trip before concluding
      anything is unavailable.

      Re-run with `WOTCHA_PREFLIGHT_REGION=us-east-1 python
      scripts/preflight_bedrock.py`.
- [x] ~~Decide the working model ceiling~~ — Sonnet 4.6, agreement declined
- [x] ~~Choose the region~~ — product in `ca-central-1`, §13 sweep in `us-east-1`
