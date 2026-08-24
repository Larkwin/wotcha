# Two-Way SMS Setup (needed for M2)

Inbound SMS lets the Liaison receive free-text signals ("the chicken was fine, it
was the sauce") and answer "what's for dinner tonight?".

**Inbound is live and proven.** Two-way was enabled on the long code on
2026-08-24 and a text from a verified handset landed in the queue. Everything
below is what was actually run, not what ought to work — the previous version of
this document was written from inference and got the central detail wrong.

## Inbound works in the SMS sandbox

This was the open question, and the honest answer was that AWS never says either
way: every documented sandbox restriction is about *outbound* — the $1.00
monthly spend cap, and sending only to verified destination numbers. Inbound has
no spend component and no destination to verify, so there is no mechanism for
the restriction to bite. But absence of mention is not a guarantee, so it was
tested rather than assumed.

**Result: a message sent from a verified handset to the long code arrived in the
queue.** No production access required.

Canada supports two-way SMS on long codes — confirmed in AWS's country
capability table (`CA`: long codes `Yes`, two-way SMS `Yes`), so the number
requested for M1 was always the right number. Enabling two-way changed a setting
on it: no new number, no re-verification, nothing lost.

## The destination is an SNS topic, not an SQS queue

**This is where the previous version of this document was wrong**, and the
mistake is worth recording because it survived for months and would have cost
somebody an afternoon.

It claimed `--two-way-channel-arn` "accepts either an SNS topic or an SQS
queue". It does not. AWS names only **Amazon SNS** or **Connect Customer** as
two-way destinations — in the two-way overview, in the console flow ("For
Destination type, choose either Amazon SNS or Connect Customer"), in every CLI
and API example, and in the sub-topic list, which has IAM policy pages for SNS
topics and for Connect Customer and none for SQS.

The claim came from reading the CLI help, which says only:

```
--two-way-channel-arn (string)
    The Amazon Resource Name (ARN) of the two way channel.
```

Generic. No service named, no enumeration — so "it takes an ARN, and SQS has
ARNs" looked like a reasonable inference. It was still an inference, written
down as a fact, and nothing tested it.

### The original reasoning was right; only the mechanism was wrong

The reason that document chose SQS is still correct, and worth keeping verbatim:

> This is a side project built in bursts, which means there will be days when the
> consumer is broken or half-deployed. SNS fans a message out once and it is gone;
> SQS holds it. A teenager texting on a Tuesday while the Liaison is mid-refactor
> should find their message waiting, not discarded.

That is true of SNS *alone*. The fix is not to abandon the queue but to put it
behind the topic:

```
inbound SMS -> SNS topic (wotcha-inbound) -> SQS queue (wotcha-inbound) -> Liaison
                                                 `-> DLQ after 5 failures
```

Every property that argument wanted survives: the message waits in the queue
while the consumer is broken, with a dead-letter queue and retries. It also
gains one — other subscribers can be added later without touching the phone
number's configuration, which is the setting that is awkward to change safely on
a live number.

## What was actually run

`scripts/`-worthy only once it moves into CDK (see the last section). For now
this is the record of the proving run.

### 1. Queue and dead-letter queue

Unchanged from the original document: create `wotcha-inbound-dlq`, then
`wotcha-inbound` with a `RedrivePolicy` naming the DLQ and `maxReceiveCount` 5.

### 2. An SNS topic — Standard, not FIFO

```bash
aws sns create-topic --region ca-central-1 --name wotcha-inbound
```

**FIFO topics are not supported for two-way SMS.** A Standard topic is required,
which is part of why ordering has to be handled downstream (below).

### 3. A topic policy — and no IAM role

The original document created a `WotchaTwoWaySms` role for End User Messaging to
assume. **That role is not needed.** `--two-way-channel-role` is only required
when the topic policy does *not* grant access, so a topic policy alone is
simpler and one fewer resource to keep in sync:

```json
{
  "Sid": "AllowEndUserMessagingPublish",
  "Effect": "Allow",
  "Principal": { "Service": "sms-voice.amazonaws.com" },
  "Action": "sns:Publish",
  "Resource": "<topic arn>",
  "Condition": {
    "StringEquals": { "aws:SourceAccount": "<account>" },
    "ArnLike": { "aws:SourceArn": "arn:aws:sms-voice:ca-central-1:<account>:*" }
  }
}
```

`sms-voice.amazonaws.com` is the correct service principal — the previous
document flagged it as unverified, and it is now confirmed by a working setup.
The `SourceAccount` / `SourceArn` conditions are AWS's recommended
confused-deputy guard; without them the statement lets the service publish to
this topic on behalf of any account.

**A topic policy cannot use `SNS:*`.** SNS rejects a wildcard in a
resource-based policy with:

```
InvalidParameter: Policy statement action out of service scope!
```

Only these eight actions are accepted, which is the list the console writes into
a topic's default policy — so an owner statement must enumerate them:

```
SNS:GetTopicAttributes   SNS:SetTopicAttributes   SNS:AddPermission
SNS:RemovePermission     SNS:DeleteTopic          SNS:Subscribe
SNS:ListSubscriptionsByTopic                      SNS:Publish
```

### 4. A queue policy letting the topic write

`sqs:SendMessage` for principal `sns.amazonaws.com`, conditioned on
`aws:SourceArn` equal to the topic ARN.

### 5. Subscribe the queue — with raw message delivery OFF

```bash
aws sns subscribe --region ca-central-1 --topic-arn <topic> \
  --protocol sqs --notification-endpoint <queue arn>
```

**Do not turn on `RawMessageDelivery`**, tempting as it is for simpler parsing.
The inbound payload carries no timestamp of its own:

```json
{
  "originationNumber": "+14255550182",
  "destinationNumber": "+12125550101",
  "messageKeyword": "JOIN",
  "messageBody": "EXAMPLE",
  "inboundMessageId": "cae173d2-66b9-564c-8309-21f858e9fb84",
  "previousPublishedMessageId": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}
```

Inbound SMS is explicitly **not** ordered — carriers make no guarantee, and
Standard topics deliver with best-effort ordering. AWS's guidance is to
approximate ordering from the SNS notification metadata's timestamp, and raw
delivery strips exactly that envelope. In a household, "no" followed by
"actually yes" arriving the wrong way round is a real outcome, not a
hypothetical, so the envelope stays.

### 6. Point the number at the topic

```bash
aws pinpoint-sms-voice-v2 update-phone-number --region ca-central-1 \
  --phone-number-id <PhoneNumberId> \
  --two-way-enabled \
  --two-way-channel-arn <topic arn>
```

Verify:

```bash
aws pinpoint-sms-voice-v2 describe-phone-numbers --region ca-central-1 \
  --query 'PhoneNumbers[].[PhoneNumber,TwoWayEnabled,TwoWayChannelArn]' --output table
```

Then text the long code from a verified handset and read the queue:

```bash
aws sqs receive-message --region ca-central-1 \
  --queue-url <inbound queue url> --wait-time-seconds 20
```

A body containing `originationNumber` and `messageBody` is the proof. That is
what settled the sandbox question, and nothing short of it would have.

## Still to do — move it into CDK

The commands above proved the path. The queues, the topic, both policies, the
subscription, and a Lambda event-source mapping belong in `infra/stack.py` so
the whole thing is reproducible for judges — the submission requires setup
instructions that actually work. Do this as part of M2, not as a separate chore.

Note that the resources now exist in the account but not in the stack, so the
first CDK pass has to either import them or use different names and retire these
by hand.

## Sender attribution is the payload's real value

Inbound messages carry the sender's phone number, which maps directly to a
`Member`. That is per-person identity for free, with no login and no token —
the same property the signed links give the web page. The Liaison should trust
the sender number for *identity* and nothing else: the message body is untrusted
input from someone who may well be trying to get poutine onto Thursday.
