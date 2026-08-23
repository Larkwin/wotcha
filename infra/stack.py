from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct


class WotchaStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)

        self.table = ddb.Table(
            self,
            "Table",
            table_name="wotcha",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            # One household of dinners is a rounding error; retain it so a bad
            # deploy never destroys the backfilled history the Curator needs.
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )

        self.link_secret = sm.Secret(
            self,
            "LinkSigningKey",
            secret_name="wotcha/link-signing-key",
            description="HMAC key for permanent per-person page links.",
            generate_secret_string=sm.SecretStringGenerator(
                exclude_punctuation=True, password_length=48
            ),
            # This key signs the permanent per-person links texted to family
            # members and never reissued. Deleting or replacing it silently
            # invalidates every link already sitting in someone's message
            # history; the only recovery is re-texting the whole family. Same
            # reasoning as the table's RETAIN above -- do not "clean this up".
            removal_policy=RemovalPolicy.RETAIN,
        )

        import os

        from aws_cdk import CfnOutput, Duration
        from aws_cdk import aws_cloudwatch as cw
        from aws_cdk import aws_cloudwatch_actions as cw_actions
        from aws_cdk import aws_iam as iam
        from aws_cdk import aws_lambda as lambda_
        from aws_cdk import aws_scheduler as scheduler
        from aws_cdk import aws_sns as sns

        # No default. "demo" is the public sample household in
        # data/household.json, and defaulting to it points the runtime and the
        # family page at a tenant nobody lives in -- with a green deploy. The
        # Makefile guard catches this for `make deploy`; this catches a direct
        # `cdk deploy`, which bypasses it.
        household_id = os.environ["WOTCHA_HOUSEHOLD_ID"]
        runtime_arn = os.environ.get("WOTCHA_RUNTIME_ARN", "")
        runtime_role_arn = os.environ.get("WOTCHA_RUNTIME_ROLE_ARN", "")
        # Schedule ships disabled by default -- a system that texts a family
        # should not start running unattended before the very first real send
        # (README step 10, performed by hand) has been confirmed to work. Flip with
        # WOTCHA_SCHEDULE_ENABLED=true on a redeploy once that send succeeds.
        schedule_enabled = os.environ.get("WOTCHA_SCHEDULE_ENABLED", "false").lower() == "true"

        code = lambda_.Code.from_asset("../build/web")
        common_env = {
            "WOTCHA_AWS_REGION": self.region,
            "WOTCHA_TABLE_NAME": self.table.table_name,
            "WOTCHA_HOUSEHOLD_ID": household_id,
            "WOTCHA_LINK_SECRET_NAME": self.link_secret.secret_name,
        }

        web = lambda_.Function(
            self, "Web",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="wotcha.web.app.handler",
            code=code,
            timeout=Duration.seconds(15),
            memory_size=512,
            environment=common_env,
        )
        self.table.grant_read_write_data(web)
        self.link_secret.grant_read(web)

        url = web.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        # NOTE: deliberately NOT setting WOTCHA_BASE_URL back onto `web`
        # here. The brief's original version did (`web.add_environment(...,
        # url.url.rstrip("/"))`), but that makes the Function's own
        # environment depend on its FunctionUrl's `url` attribute, while the
        # FunctionUrl resource already depends on the Function via
        # targetFunctionArn -- CDK synth fails with "Circular dependency
        # between resources" (confirmed 2026-08-20). It is also pointless:
        # `wotcha.web.app` never reads WOTCHA_BASE_URL -- the web Lambda only
        # ever renders the page for a token it is handed, it never needs to
        # know its own address. The value that *is* consumed -- by
        # `notify.py` inside the AgentCore runtime, to build the links
        # texted to the family -- comes from the WebUrl output below, copied
        # in by hand with the trailing slash stripped (see the
        # runtime-deploy comment in the Makefile). Read back the runtime's
        # actual env var after deploying to confirm the slash was stripped.

        sched_fn = lambda_.Function(
            self, "Scheduler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="scheduler.handler",
            code=code,
            timeout=Duration.minutes(5),
            environment={**common_env, "WOTCHA_RUNTIME_ARN": runtime_arn},
        )
        # Scoped to this runtime once its ARN is known. The endpoint child
        # ARN (.../runtime-endpoint/DEFAULT) is what an invoke actually names,
        # so both the runtime and its children are granted. Falls back to "*"
        # only on the very first deploy, when the runtime does not exist yet
        # and there is nothing to scope to -- and the Makefile's
        # check-deploy-env makes reaching that state an explicit choice
        # rather than a forgotten export.
        sched_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[runtime_arn, f"{runtime_arn}/*"] if runtime_arn else ["*"],
        ))

        role = iam.Role(
            self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        sched_fn.grant_invoke(role)

        scheduler.CfnSchedule(
            self, "WeeklyPlan",
            name="wotcha-weekly-plan",
            flexible_time_window={"mode": "OFF"},
            # Saturday 09:00 local: the week is planned and delivered before
            # the weekend shop, and well before anyone has to ask. Must be
            # America/Toronto, the same timezone runtime.py uses to decide
            # which week "next Monday" means -- a mismatched schedule
            # timezone would fire against a different day boundary than the
            # planner reasons about.
            schedule_expression="cron(0 9 ? * SAT *)",
            schedule_expression_timezone="America/Toronto",
            target={"arn": sched_fn.function_arn, "roleArn": role.role_arn},
            state="ENABLED" if schedule_enabled else "DISABLED",
        )

        # The AgentCore Runtime's execution role is created out-of-band by the
        # `agentcore` toolkit (agentcore configure / deploy), not by this
        # stack -- so it cannot be granted access the normal CDK way (there is
        # no `lambda_.Function` object here to call .grant_read_write_data on).
        # Without this, a fresh clone's runtime can `ping` (needs nothing but
        # Bedrock, already granted by the toolkit) but every other action
        # fails with AccessDeniedException on its first real DynamoDB/Secrets
        # Manager call -- exactly the command the README offers as
        # verification. WOTCHA_RUNTIME_ROLE_ARN is only known after the
        # runtime has been configured at least once (see README step 8), so
        # this grant is skipped, not failed, when the ARN isn't supplied yet;
        # re-run `make deploy` with it set once you have it.
        if runtime_role_arn:
            runtime_role = iam.Role.from_role_arn(
                self, "RuntimeExecutionRole", runtime_role_arn, mutable=True,
            )
            self.table.grant_read_write_data(runtime_role)
            self.link_secret.grant_read(runtime_role)

            # The runtime sends the weekly text through AWS End User Messaging.
            # Scoped to the single origination number, supplied at deploy time via
            # WOTCHA_SMS_ORIGINATION_ARN so no account resource id lives in the repo.
            # Skipped when unset: a console-channel deployment never sends, and the
            # very first real send is the moment this is needed -- which is also why
            # nothing could exercise it earlier. No agent on this project was ever
            # permitted near SMS, so this permission had no way to be discovered
            # except by a human running the real send and getting AccessDenied.
            sms_origination_arn = os.environ.get("WOTCHA_SMS_ORIGINATION_ARN", "")
            if sms_origination_arn:
                runtime_role.add_to_principal_policy(
                    iam.PolicyStatement(
                        actions=["sms-voice:SendTextMessage"],
                        resources=[sms_origination_arn],
                    )
                )

        # --- the SMS spend ceiling ------------------------------------
        #
        # AWS sets the sandbox monthly SMS spend quota to $1.00 (USD), and in
        # the sandbox that is the *maximum*, not a default -- a Service Quotas
        # increase on TextMessageMonthlySpend is refused while the account is
        # sandboxed. The only lever is production access, which also removes
        # the verified-destination restriction that is currently this
        # project's real safety net. So the ceiling stays, and the answer is
        # to see it coming rather than to raise it.
        #
        # Hitting it is a silent failure with a twist: End User Messaging
        # stops publishing messages within minutes, and
        # NumberOfTextMessagePartsSent explicitly *excludes* messages blocked
        # by spend limits -- so a blocked send leaves no trace in the delivery
        # metrics at all. The send itself does raise (channel/sms.py does not
        # swallow, notify.py has no except), so a scheduled run fails loudly
        # once the ceiling is hit. This alarm exists to arrive before that.
        #
        # Constants, not environment knobs. $1.00 is an AWS-documented
        # sandbox constant rather than an operator preference, and half of it
        # is a full weekly send cycle of warning: the weekly text is capped at
        # two GSM-7 segments per person (see notify.SMS_LENGTH_CAP), so one
        # household's steady state is a small fraction of the budget and
        # anything approaching $0.50 is a runaway, not growth. Raise both here
        # if production access is ever granted.
        sms_spend_ceiling_usd = 1.00
        sms_spend_alarm_usd = sms_spend_ceiling_usd / 2

        # Created always, subscribed by hand -- see docs/status.md. A CDK
        # email subscription would put a personal address into the synthesized
        # template and into the deploy command; this repo is public and
        # `make deploy` invocations get pasted into runbooks. The cost of that
        # choice is a topic with no subscriber, which is the same silent
        # failure in a new costume -- so `preflight_sms.py spend` reports the
        # confirmed subscription count, and `make preflight` runs it.
        alarms = sns.Topic(
            self, "Alarms",
            topic_name="wotcha-alarms",
            display_name="Wotcha",
        )

        cw.Alarm(
            self, "SmsMonthlySpend",
            alarm_name="wotcha-sms-monthly-spend",
            metric=cw.Metric(
                # Regional, unlike AWS/Billing -- the console walkthrough for
                # "spending alarms" tells you to switch to us-east-1, which is
                # true for AWS/Billing and wrong for this namespace. It belongs
                # in the product region with everything else.
                namespace="AWS/SMSVoice",
                metric_name="TextMessageMonthlySpend",
                # Month-to-date running total, so Maximum is the only
                # statistic that means anything. Sum would add a cumulative
                # gauge to itself.
                statistic="Maximum",
                period=Duration.hours(1),
            ),
            threshold=sms_spend_alarm_usd,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            # IGNORE, not MISSING. The metric only publishes around a send, so
            # six of every seven days have no datapoints at all. Under MISSING
            # an evaluation range of nothing goes to INSUFFICIENT_DATA, and an
            # alarm that has gone red would fall back out of ALARM the next
            # quiet day; IGNORE retains the current state instead, so a breach
            # stays visible until the month rolls over and real data clears it.
            # The residual gap -- a metric that never publishes at all leaves
            # this sitting in INSUFFICIENT_DATA forever -- is not closable from
            # here, and is exactly what the preflight check covers.
            treat_missing_data=cw.TreatMissingData.IGNORE,
            alarm_description=(
                f"Wotcha SMS spend has passed ${sms_spend_alarm_usd:.2f} of the "
                f"${sms_spend_ceiling_usd:.2f} monthly ceiling. At the ceiling AWS "
                f"stops sending within minutes and the weekly text fails. In the "
                f"sandbox the ceiling cannot be raised."
            ),
        ).add_alarm_action(cw_actions.SnsAction(alarms))

        CfnOutput(self, "WebUrl", value=url.url)
        # Named so the subscription can be added by hand without hunting for
        # it in the console.
        CfnOutput(self, "AlarmsTopicArn", value=alarms.topic_arn)
