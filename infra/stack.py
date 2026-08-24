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
            # The `point_in_time_recovery=True` shorthand is deprecated and
            # goes away in the next CDK major. This is the same synthesized
            # PointInTimeRecoverySpecification, verified by diffing the
            # template before and after -- which matters more than usual here,
            # because this table holds the family's real history and is
            # RETAIN: a property change that CloudFormation treated as a
            # replacement would orphan it.
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
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
        from aws_cdk import aws_sns_subscriptions as subs
        from aws_cdk import aws_sqs as sqs
        from aws_cdk.aws_lambda_event_sources import SqsEventSource

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
        # The Liaison gets its own asset rather than sharing `code` above.
        # wotcha/agents/liaison.py does `from strands import Agent`, and
        # strands-agents is not in build/web -- Makefile's build-lambda only
        # ever installed pydantic there. Bundling strands-agents into
        # build/web instead of a separate asset was rejected: that package
        # pulls in mcp, opentelemetry-*, httpx, jsonschema, pyyaml, watchdog
        # and docstring-parser, none of which Web or Scheduler import, so it
        # would bloat the cold start of both of them to satisfy a dependency
        # only the Liaison has. build/liaison (see the Makefile comment)
        # carries strands-agents and pydantic and nothing else the other two
        # functions would have to pay for.
        liaison_code = lambda_.Code.from_asset("../build/liaison")
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

        # --- inbound SMS ----------------------------------------------
        #
        # The transport for M2's Liaison. Proven by hand on 2026-08-24 (a text
        # from a verified handset landed in the queue) and moved here so it is
        # reproducible, diffable, and not one person's shell history.
        #
        # Shape: inbound -> SNS topic -> SQS queue -> (later) the Liaison.
        #
        # SNS is not a preference. AWS accepts only an SNS topic or Connect
        # Customer as a two-way destination; docs/two-way-sms-setup.md used to
        # claim SQS worked, inferred from CLI help that says merely "the ARN of
        # the two way channel". The queue is still here, behind the topic,
        # because the reason for wanting it stands: this is a side project
        # built in bursts, and a teenager texting on a Tuesday while the
        # Liaison is mid-refactor should find their message waiting rather
        # than discarded. SNS alone delivers once and forgets.
        #
        # The names are the ones the hand-built resources already had, on
        # purpose. SQS and SNS ARNs are derived from the name, so recreating
        # them here yields byte-identical ARNs -- which means the phone
        # number's TwoWayChannelArn keeps resolving and never has to be
        # re-pointed.
        inbound_dlq = sqs.Queue(
            self, "InboundDlq",
            queue_name="wotcha-inbound-dlq",
            # A message reaches this queue only after five failed deliveries,
            # so by definition every item in it is something that went wrong
            # and nobody has looked at yet. Same reasoning as the table.
            removal_policy=RemovalPolicy.RETAIN,
        )

        inbound = sqs.Queue(
            self, "Inbound",
            queue_name="wotcha-inbound",
            # Household data: a family member's actual words, not yet read by
            # anything. A stack mistake must not eat them -- and note the
            # trade-off this shares with the table, that a destroyed stack
            # leaves the queue behind and the next deploy then fails on the
            # name. That is the intended direction to fail in.
            removal_policy=RemovalPolicy.RETAIN,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5, queue=inbound_dlq,
            ),
        )

        inbound_topic = sns.Topic(
            self, "InboundTopic",
            # Standard, not FIFO: AWS does not support FIFO topics as a
            # two-way SMS destination.
            topic_name="wotcha-inbound",
            display_name="Wotcha inbound",
        )

        # SourceAccount/SourceArn are AWS's recommended confused-deputy guard.
        # Without them this statement lets End User Messaging publish to this
        # topic on behalf of any account, not just ours.
        inbound_topic.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowEndUserMessagingPublish",
            principals=[iam.ServicePrincipal("sms-voice.amazonaws.com")],
            actions=["sns:Publish"],
            resources=[inbound_topic.topic_arn],
            conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": f"arn:aws:sms-voice:{self.region}:{self.account}:*"
                },
            },
        ))

        # raw_message_delivery deliberately left at its default of False. The
        # inbound payload carries no timestamp of its own, inbound SMS has no
        # ordering guarantee across carrier networks, and AWS's guidance is to
        # approximate ordering from the SNS notification metadata -- which raw
        # delivery strips. "No" followed by "actually yes", arriving reversed,
        # is an ordinary household outcome rather than a hypothetical.
        inbound_topic.add_subscription(subs.SqsSubscription(inbound))

        # The consumer. Nothing else reads this queue, and a queue nobody
        # reads holds messages until they expire -- the family texts, the
        # message lands, and it is never seen.
        liaison_fn = lambda_.Function(
            self, "Liaison",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="liaison.handler",
            code=liaison_code,
            # One Bedrock call per record, on a small model. Generous enough
            # that a slow response is not a redelivery.
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                **common_env,
                "WOTCHA_LIAISON_MODEL_ID": os.environ.get(
                    "WOTCHA_LIAISON_MODEL_ID", "ca.amazon.nova-lite-v1:0"
                ),
            },
        )
        self.table.grant_read_write_data(liaison_fn)
        # Its entire job is one model call. Without this the first real
        # message fails with AccessDenied -- exactly how the runtime's SMS
        # grant was found, and for exactly the same reason: a permission that
        # only matters on a real run.
        #
        # Both actions, not just InvokeModel: strands' BedrockModel.structured_output
        # goes through .stream(), and streaming defaults to True there, so the
        # actual API call is ConverseStream -- which needs
        # bedrock:InvokeModelWithResponseStream, not bedrock:InvokeModel
        # (Converse only). Passing streaming=False in agents/liaison.py would
        # let InvokeModel alone suffice, but that pins this deploy's IAM to a
        # library default living in a different file -- someone removing that
        # kwarg later gets AccessDenied in production with nothing here to
        # catch it. Both actions name the same capability (invoke this
        # model), so granting both costs nothing in real privilege and
        # matches what the library actually does rather than what we hope it
        # does.
        #
        # resources=["*"], not scoped to a model or region ARN: unlike the
        # AgentCore grant above, there is no per-deploy identifier to narrow
        # to here -- WOTCHA_LIAISON_MODEL_ID is an operator-set default that
        # can change without a stack edit, and Bedrock foundation-model ARNs
        # are per-region, so a region-scoped ARN would need to track
        # self.region by hand for a cross-region model catalog that already
        # doesn't apply to a single Toronto household. The unscoped grant
        # trades nothing meaningful away: bedrock:InvokeModel(WithResponseStream)
        # already can't touch this account's own data, only run inference.
        liaison_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        # batch_size 1: a failure redelivers the whole batch, and every
        # message in a redelivered batch is read by the model again. One
        # record per invocation means one bad message cannot cost re-reads of
        # its neighbours.
        liaison_fn.add_event_source(SqsEventSource(inbound, batch_size=1))

        CfnOutput(self, "WebUrl", value=url.url)
        # The value that goes into `update-phone-number --two-way-channel-arn`.
        # That call is deliberately NOT made from this stack: the only
        # CloudFormation resource for it would take ownership of the family's
        # live long code, and a stack mistake could then release a number that
        # took a support process to obtain and is verified as an origination
        # identity. Left out, a deploy cannot revoke two-way -- the reverse of
        # the usual risk here -- and the setting survives because this ARN is
        # derived from the topic name and therefore stable.
        CfnOutput(self, "InboundTopicArn", value=inbound_topic.topic_arn)
        CfnOutput(self, "InboundQueueUrl", value=inbound.queue_url)
        # Named so the subscription can be added by hand without hunting for
        # it in the console.
        CfnOutput(self, "AlarmsTopicArn", value=alarms.topic_arn)
