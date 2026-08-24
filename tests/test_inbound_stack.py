"""The inbound SMS transport, as declared in infra/stack.py.

Source assertions rather than a synth: the CDK lives in infra/.venv and is not
importable from the suite (see test_preflight_sms.py, which pins the spend
alarm the same way). Crude, but it catches the edits whose failure mode is
silence -- and every one of these has that shape, because the phone number's
two-way configuration lives outside CloudFormation and cannot be re-checked by
a deploy.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STACK = (REPO_ROOT / "infra" / "stack.py").read_text()
MAKEFILE = (REPO_ROOT / "Makefile").read_text()

# Comment lines stripped. Every "this must not appear" assertion below is
# about what the stack *does*, and stack.py explains its choices at length --
# so a bare substring check matches the prose describing the thing it forbids
# and fails on a file that is entirely correct. That has now happened three
# times in this repo; assert against the code, explain in the comments.
CODE = "\n".join(
    line for line in STACK.splitlines() if not line.strip().startswith("#")
)


@pytest.mark.parametrize("name", ["wotcha-inbound", "wotcha-inbound-dlq"])
def test_the_inbound_names_are_stated_not_generated(name):
    """CDK would happily invent a unique name for each of these. It must not.

    An SQS or SNS ARN is derived from its name, and the phone number's
    TwoWayChannelArn -- set once by hand, deliberately outside this stack --
    names the topic by ARN. A generated name is a different ARN, and the
    number would go on pointing at one that no longer exists.
    """
    assert f'"{name}"' in STACK


def test_renaming_the_inbound_topic_would_silently_break_inbound():
    """The topic name is load-bearing in a way nothing in AWS will warn about.

    `update-phone-number --two-way-channel-arn` is a setting on the phone
    number, not a stack resource, so a deploy cannot update it and cannot
    notice it has gone stale. Rename the topic here and inbound stops: the
    number keeps publishing to an ARN that no longer resolves, nobody gets an
    error, and the first symptom is a family member saying nobody answered.
    """
    assert 'topic_name="wotcha-inbound"' in STACK


def test_raw_message_delivery_is_never_enabled():
    """Turning it on looks like a simplification and costs the only timestamp
    there is.

    The inbound payload has no time field of its own -- originationNumber,
    destinationNumber, messageKeyword, messageBody, inboundMessageId,
    previousPublishedMessageId, and that is the whole list. Inbound SMS has no
    ordering guarantee across carriers, and AWS's guidance is to approximate
    ordering from the SNS notification metadata. Raw delivery strips exactly
    that envelope, so "no" then "actually yes" become unorderable.
    """
    assert "raw_message_delivery" not in CODE


@pytest.mark.parametrize("construct_id", ["InboundDlq", "Inbound"])
def test_the_inbound_queues_are_retained(construct_id):
    """A queue here holds a family member's actual words, not yet read by
    anything. Same standing as the table and the signing key: a bad deploy
    must not be able to eat it."""
    start = STACK.index(f'self, "{construct_id}",')
    block = STACK[start:start + 700]
    assert "RemovalPolicy.RETAIN" in block


def test_the_dead_letter_queue_is_actually_wired_up():
    """A DLQ that nothing redrives into is a queue that stays empty while
    messages are lost -- reassuring and useless."""
    assert "dead_letter_queue=sqs.DeadLetterQueue(" in STACK
    assert "queue=inbound_dlq" in STACK


def test_the_publish_grant_is_scoped_to_this_account():
    """Without SourceAccount/SourceArn the statement lets End User Messaging
    publish to this topic on behalf of *any* account -- AWS's confused-deputy
    problem, and the guard they explicitly recommend for this integration."""
    assert '"aws:SourceAccount": self.account' in STACK
    assert "arn:aws:sms-voice:{self.region}:{self.account}:*" in STACK


def test_the_phone_number_itself_is_not_a_stack_resource():
    """Deliberate, and the reverse of the usual risk in this file.

    The family's long code took a support process to obtain and is verified as
    an origination identity. Putting it under CloudFormation would mean a
    stack mistake could release it. Left out, a deploy cannot revoke two-way
    at all -- and the setting survives because the topic ARN is stable.
    """
    assert "PinpointSMSVoiceV2" not in CODE
    assert "CfnPhoneNumber" not in CODE


def test_the_consumer_is_wired_to_the_inbound_queue():
    """A queue with no consumer holds messages until they expire. The family
    would text, the message would land, and nothing would ever read it."""
    assert 'self, "Liaison",' in CODE
    assert 'handler="liaison.handler"' in CODE
    assert "SqsEventSource(inbound" in CODE


def test_the_consumer_can_reach_bedrock():
    """Its whole job is one model call. Without this grant the first real
    message fails with AccessDenied -- the same failure mode as the SMS grant
    in the M1 addendum, discovered the same way.

    Both actions, deliberately. strands' BedrockModel.structured_output
    streams by default, so the real call is ConverseStream, which needs
    bedrock:InvokeModelWithResponseStream -- InvokeModel alone covers only
    Converse. InvokeModel alone is the trap here: it looks right, the string
    is even a substring of the one actually needed, and it only fails on a
    real invocation, never in this test suite or in synth.
    """
    assert "bedrock:InvokeModel" in CODE
    assert "bedrock:InvokeModelWithResponseStream" in CODE


def test_the_liaison_function_uses_the_bundle_that_installs_strands_agents():
    """C1: wotcha/agents/liaison.py does `from strands import Agent`, and
    strands-agents is third-party -- it does not ship with the Lambda
    runtime. Nothing before this test checked that the asset the Liaison
    function actually points at is the one that ever gets strands-agents
    installed into it, and the gap is not theoretical:
    test_the_consumer_is_wired_to_the_inbound_queue above passes today even
    though the Liaison could not cold-start at all if it were pointed at
    build/web -- every real inbound text would raise ModuleNotFoundError
    before executing a line, fail its batch of one (batch_size=1, see
    test_the_consumer_batches_small), redeliver five times, and dead-letter.
    A green test suite asserting a feature is deployed, while it is dead.

    Read the Makefile and infra/stack.py as text, like every other test in
    this module -- the CDK is not importable here, and a Makefile has no
    importable form at all. This deliberately does not hardcode
    "build/liaison" as a literal in both places: it reads the --target out
    of the Makefile's strands-agents install and confirms *that* directory
    -- whatever it is named -- is the one wired to the Liaison Function, so
    renaming the asset in only one file still fails this test.
    """
    idx = MAKEFILE.index("pip install strands-agents")
    target_match = re.search(r"--target\s+(\S+)", MAKEFILE[idx:idx + 200])
    assert target_match, "no --target directory follows the strands-agents install"
    liaison_asset = target_match.group(1)

    # infra/stack.py's from_asset paths are relative to infra/, hence "../".
    asset_ref = f'"../{liaison_asset}"'
    assert asset_ref in STACK, (
        f"no Code.from_asset({asset_ref}) in stack.py -- the Liaison's code "
        "asset and the Makefile's strands-agents install target have drifted apart"
    )
    var_match = re.search(
        r'(\w+)\s*=\s*lambda_\.Code\.from_asset\(' + re.escape(asset_ref) + r'\)',
        STACK,
    )
    assert var_match, "no variable is built from the strands-agents bundle"

    start = STACK.index('self, "Liaison",')
    block = STACK[start:start + 700]
    assert f"code={var_match.group(1)}," in block, (
        "the Liaison Function is not passed the asset that gets "
        "strands-agents installed into it"
    )


def test_the_consumer_batches_small():
    """Pinned at 1, not just present. lambdas/liaison.py's `handler`
    deliberately has no per-record try/except -- a ruling made on the
    explicit grounds that batch_size=1 makes "the whole batch" one record,
    so a transient DynamoDB error retries that one message instead of a
    swallowed exception silently dropping it. Raising this number would
    silently invalidate that reasoning: a batch failure would then redeliver
    -- and re-read by the model -- every neighbour of one bad record, with
    nothing here to say so. A reader who changes this value should learn the
    cross-file dependency from this failing test, not from a production
    incident."""
    assert "batch_size=1" in CODE
