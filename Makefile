VENV := .venv
PY := $(VENV)/bin/python

.PHONY: install test lint preflight bootstrap-infra build-lambda check-deploy-env deploy check-runtime-deploy-env runtime-deploy

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -v

lint:
	$(PY) -m ruff check src tests scripts lambdas

# `list` explicitly: preflight_sms.py with no arguments prints usage and
# exits 1, so this target used to fail every single time it was run. `list`
# is the read-only check -- origination identities and verified destinations
# -- and sends nothing.
preflight:
	$(PY) scripts/preflight_bedrock.py
	$(PY) scripts/preflight_sms.py list

# Creates infra/.venv (gitignored, not created by `install`) and installs the
# CDK CLI's Python dependencies into it. infra/cdk.json execs
# infra/.venv/bin/python3, so this must run before the first `make deploy`
# on a fresh clone; safe to re-run any time.
bootstrap-infra:
	cd infra && python3 -m venv .venv
	cd infra && .venv/bin/pip install --upgrade pip
	cd infra && .venv/bin/pip install -r requirements.txt

# Builds the Lambda deployment asset for both the Web and Scheduler
# functions (infra/stack.py points aws_lambda.Code.from_asset at
# ../build/web for both). The web Lambda runs on PYTHON_3_12/x86_64, which
# is not this machine -- pydantic-core is a compiled extension, so a plain
# `pip install` here would vendor a macOS/arm wheel that 500s on every
# invoke. --platform/--python-version/--implementation/--only-binary force
# pip to fetch the manylinux cp312 wheel instead of building one locally.
# boto3 ships in the Lambda runtime already and is deliberately not vendored
# -- confirmed 2026-08-20 against the actual public.ecr.aws/lambda/python:3.12
# base image (boto3 1.42.97), which already has the bedrock-agentcore service
# model with invoke_agent_runtime, so the scheduler Lambda needs nothing extra.
# Uses $(PY) (the repo .venv), not the bare `python3` on PATH, so this works
# the same on a fresh clone regardless of what pip does or doesn't allow for
# the system interpreter -- --python-version 3.12 already makes the *target*
# interpreter irrelevant, this is only about which pip actually runs.
build-lambda: $(VENV)
	rm -rf build/web && mkdir -p build/web
	$(PY) -m pip install pydantic \
		--target build/web \
		--only-binary=:all: \
		--platform manylinux2014_x86_64 \
		--python-version 3.12 \
		--implementation cp \
		--quiet
	cp -r src/wotcha build/web/wotcha
	cp lambdas/scheduler.py build/web/scheduler.py

# infra/stack.py reads these three from the environment, and every one of
# them fails *quietly* when absent: no WOTCHA_RUNTIME_ROLE_ARN silently
# deletes the IAM grant that lets the runtime reach DynamoDB and Secrets
# Manager, no WOTCHA_RUNTIME_ARN silently unscopes and blanks the scheduler
# Lambda's target, and no WOTCHA_SCHEDULE_ENABLED silently turns the weekly
# schedule off. A bare `make deploy` therefore un-deploys work that is
# already live, with a green exit code. So this target refuses to run unless
# all three are *stated*.
#
# Stated, not non-empty: `$(origin ...)` distinguishes "unset" from "set to
# the empty string", and an explicit empty value is a legitimate answer --
# it is exactly what README step 6's first-ever deploy needs, before the
# runtime exists to have an ARN. `WOTCHA_RUNTIME_ARN= WOTCHA_RUNTIME_ROLE_ARN=
# WOTCHA_SCHEDULE_ENABLED=false make deploy` is a deliberate act; a bare
# `make deploy` is an accident.
#
# The alternative was checking the three values into infra/cdk.context.json.
# Rejected: two of them are ARNs containing this AWS account's id, which
# would hardcode one account into the repo and break any other clone, and
# the third is operational state (is the schedule live right now?) rather
# than source. Deployment state belongs in the deploy command, not in git.
DEPLOY_VARS := WOTCHA_RUNTIME_ARN WOTCHA_RUNTIME_ROLE_ARN WOTCHA_SCHEDULE_ENABLED
DEPLOY_UNSET := $(strip $(foreach v,$(DEPLOY_VARS),\
	$(if $(filter undefined,$(origin $(v))),$(v))))

check-deploy-env:
	@if [ -n "$(DEPLOY_UNSET)" ]; then \
		echo "make deploy: REFUSING -- these are not stated: $(DEPLOY_UNSET)"; \
		echo ""; \
		echo "Deploying without them does not fail, it silently revokes:"; \
		echo "  WOTCHA_RUNTIME_ROLE_ARN  unset -> deletes the runtime IAM grant"; \
		echo "  WOTCHA_RUNTIME_ARN       unset -> blanks the scheduler target"; \
		echo "  WOTCHA_SCHEDULE_ENABLED  unset -> turns the weekly schedule off"; \
		echo ""; \
		echo "State all three. Empty is a legitimate answer, silence is not:"; \
		echo "  WOTCHA_RUNTIME_ARN= WOTCHA_RUNTIME_ROLE_ARN= WOTCHA_SCHEDULE_ENABLED=false make deploy"; \
		exit 1; \
	fi
	@if [ "$(WOTCHA_HOUSEHOLD_ID)" = "$(WOTCHA_HOUSEHOLD_ID_DEFAULT)" ]; then \
		echo "make deploy: REFUSING -- WOTCHA_HOUSEHOLD_ID is still '$(WOTCHA_HOUSEHOLD_ID_DEFAULT)'."; \
		echo "That is the public sample household, not the one the family lives in."; \
		echo "Deploying at this default does not fail: the planner reads an empty"; \
		echo "tenant and the family page renders nothing, with a green exit code."; \
		echo ""; \
		echo "  WOTCHA_HOUSEHOLD_ID=<the household> make deploy"; \
		exit 1; \
	fi
	@case "$$(printf %s '$(WOTCHA_SCHEDULE_ENABLED)' | tr A-Z a-z)" in \
		true|false) ;; \
		*) echo "make deploy: REFUSING -- WOTCHA_SCHEDULE_ENABLED must be true or false, got '$(WOTCHA_SCHEDULE_ENABLED)'."; \
		   echo "Any other value reads as false, silently disabling the weekly schedule."; \
		   exit 1 ;; \
	esac
	@echo "deploy: schedule=$(WOTCHA_SCHEDULE_ENABLED), runtime_arn=$(if $(WOTCHA_RUNTIME_ARN),set,empty), runtime_role_arn=$(if $(WOTCHA_RUNTIME_ROLE_ARN),set,empty)"

# check-deploy-env first, and it is listed first deliberately: make runs
# prerequisites in order, so the refusal happens before anything is built.
# build-lambda before the deploy itself: infra/stack.py's
# Code.from_asset("../build/web") must exist before `cdk synth`/`cdk deploy`
# can package it, and that directory does not exist on a fresh clone.
deploy: check-deploy-env bootstrap-infra build-lambda
	cd infra && cdk deploy --require-approval never

# Deploys the AgentCore Runtime container (src/wotcha/runtime.py). Requires
# `make install` first, for the `agentcore` CLI (bedrock-agentcore-starter-toolkit,
# a dev dependency). Requires the one-time, interactive `agentcore configure`
# below to have been run once per machine first -- omitting --deployment-type
# container silently falls back to direct_code_deploy (no image, no
# container at all), so both flags are required, not optional:
#
#   agentcore configure --entrypoint src/wotcha/runtime.py \
#     --deployment-type container --region ca-central-1
#
# See docs/preflight-report.md for the deployed runtime ARN.
#
# WOTCHA_CHANNEL defaults to console -- not a placeholder to swap out later
# without thinking, but the standing safety default. `wotcha.channel.get_channel`
# already defaults to console when the variable is absent entirely, but this
# target sets it explicitly every time (default console, override on the
# command line) so a runtime redeploy always states its channel rather than
# leaning on that fallback; flipping it to `sms` is a deliberate, visible act
# the household owner performs, e.g.
# `WOTCHA_CHANNEL=sms WOTCHA_SMS_ORIGINATION_ID=<origination id> WOTCHA_BASE_URL=<the WebUrl output> make runtime-deploy`
# -- never a real phone number or origination id hardcoded here. WOTCHA_BASE_URL
# should be the `WebUrl` stack output with any trailing slash stripped --
# override it on the command line, e.g.
# `make runtime-deploy WOTCHA_BASE_URL=https://xxxx.lambda-url.ca-central-1.on.aws`.
WOTCHA_BASE_URL_DEFAULT := http://localhost:8080
WOTCHA_BASE_URL ?= $(WOTCHA_BASE_URL_DEFAULT)
# The public pseudonymous household in data/household.json. The real one is
# never named in this repo -- state it on the command line or in the shell.
WOTCHA_HOUSEHOLD_ID_DEFAULT := demo
WOTCHA_HOUSEHOLD_ID ?= $(WOTCHA_HOUSEHOLD_ID_DEFAULT)
WOTCHA_TABLE_NAME ?= wotcha
WOTCHA_BEDROCK_MODEL_ID ?= us.anthropic.claude-sonnet-4-6
WOTCHA_CHANNEL ?= console
WOTCHA_SMS_ORIGINATION_ID ?=

# `sms` plus a `WOTCHA_BASE_URL` still sitting at its localhost default is
# never intentional: it means every family member's first real text carries
# `http://localhost:8080/w/<token>` -- a dead link on their phone, not the
# page, and the runtime has no way to tell "operator forgot" from "operator
# meant it" after the fact. Console runs legitimately use localhost (there is
# no phone to reach), so only `sms` is checked.
check-runtime-deploy-env:
	@if [ "$(WOTCHA_CHANNEL)" = "sms" ] && [ "$(WOTCHA_BASE_URL)" = "$(WOTCHA_BASE_URL_DEFAULT)" ]; then \
		echo "make runtime-deploy: REFUSING -- WOTCHA_CHANNEL=sms but WOTCHA_BASE_URL is still its localhost default ($(WOTCHA_BASE_URL_DEFAULT))."; \
		echo ""; \
		echo "A real text would put http://localhost:8080/w/<token> in every family member's message -- a dead link, not a page."; \
		echo ""; \
		echo "State WOTCHA_BASE_URL explicitly, e.g.:"; \
		echo "  WOTCHA_CHANNEL=sms WOTCHA_SMS_ORIGINATION_ID=<id> WOTCHA_BASE_URL=<the WebUrl output> make runtime-deploy"; \
		exit 1; \
	fi

runtime-deploy: check-runtime-deploy-env
	$(VENV)/bin/agentcore deploy \
		--env WOTCHA_CHANNEL=$(WOTCHA_CHANNEL) \
		--env WOTCHA_HOUSEHOLD_ID=$(WOTCHA_HOUSEHOLD_ID) \
		--env WOTCHA_TABLE_NAME=$(WOTCHA_TABLE_NAME) \
		--env WOTCHA_BEDROCK_MODEL_ID=$(WOTCHA_BEDROCK_MODEL_ID) \
		--env WOTCHA_BASE_URL=$(WOTCHA_BASE_URL) \
		$(if $(WOTCHA_SMS_ORIGINATION_ID),--env WOTCHA_SMS_ORIGINATION_ID=$(WOTCHA_SMS_ORIGINATION_ID),)
