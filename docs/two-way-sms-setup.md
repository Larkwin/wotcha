# Two-Way SMS Setup (needed for M2, not M1)

Inbound SMS lets the Liaison receive free-text signals ("the chicken was fine, it
was the sauce") and answer "what's for dinner tonight?".

**Not needed for M1.** M1 is outbound only. Nothing here blocks the first real week.

## Why this is not urgent

Verified against the CLI on 2026-08-20: `request-phone-number` accepts only
`--iso-country-code`, `--message-type`, `--number-capabilities`, `--number-type`,
plus opt-out list, pool, registration, tags. **It has no two-way parameters.**

Two-way is set afterwards with `update-phone-number`, which does:

```
--two-way-enabled | --no-two-way-enabled
--two-way-channel-arn <value>
--two-way-channel-role <value>
```

So the long code requested for M1 is already the right number. Enabling two-way
later changes a setting on it — no new number, no re-verification, no lost work.

## SQS, not SNS

`--two-way-channel-arn` accepts either an SNS topic or an SQS queue. **Use SQS.**

This is a side project built in bursts, which means there will be days when the
consumer is broken or half-deployed. SNS fans a message out once and it is gone;
SQS holds it. A teenager texting on a Tuesday while the Liaison is mid-refactor
should find their message waiting, not discarded. SQS also gives a dead-letter
queue and retries for free.

## Step 1 — Queue and dead-letter queue

```bash
REGION=ca-central-1

aws sqs create-queue --region $REGION --queue-name wotcha-inbound-dlq

DLQ_ARN=$(aws sqs get-queue-attributes --region $REGION \
  --queue-url $(aws sqs get-queue-url --region $REGION \
    --queue-name wotcha-inbound-dlq --query QueueUrl --output text) \
  --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

aws sqs create-queue --region $REGION --queue-name wotcha-inbound \
  --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"

QUEUE_ARN=$(aws sqs get-queue-attributes --region $REGION \
  --queue-url $(aws sqs get-queue-url --region $REGION \
    --queue-name wotcha-inbound --query QueueUrl --output text) \
  --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

echo "QUEUE_ARN=$QUEUE_ARN"
```

## Step 2 — Role for End User Messaging to write to it

```bash
cat > /tmp/trust.json <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Principal":{"Service":"sms-voice.amazonaws.com"},
  "Action":"sts:AssumeRole"
}]}
JSON

aws iam create-role --role-name WotchaTwoWaySms \
  --assume-role-policy-document file:///tmp/trust.json

cat > /tmp/policy.json <<JSON
{"Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Action":["sqs:SendMessage","sqs:GetQueueAttributes"],
  "Resource":"$QUEUE_ARN"
}]}
JSON

aws iam put-role-policy --role-name WotchaTwoWaySms \
  --policy-name PublishInbound --policy-document file:///tmp/policy.json
```

**If `update-phone-number` later rejects the role**, the service principal is the
thing to check first — confirm it against
`aws pinpoint-sms-voice-v2 update-phone-number help` and the End User Messaging
docs before assuming the ARNs are wrong. `sms-voice.amazonaws.com` is the expected
principal but was not verified end to end here, because verifying it requires
creating the role.

## Step 3 — Switch it on

```bash
aws pinpoint-sms-voice-v2 update-phone-number --region ca-central-1 \
  --phone-number-id <PhoneNumberId> \
  --two-way-enabled \
  --two-way-channel-arn "$QUEUE_ARN" \
  --two-way-channel-role "arn:aws:iam::<AWS_ACCOUNT_ID>:role/WotchaTwoWaySms"
```

Verify:

```bash
aws pinpoint-sms-voice-v2 describe-phone-numbers --region ca-central-1 \
  --query 'PhoneNumbers[].[PhoneNumber,TwoWayEnabled,TwoWayChannelArn]' --output table
```

Then text the long code from a verified handset and confirm the message lands:

```bash
aws sqs receive-message --region ca-central-1 --queue-url <inbound queue url>
```

## Step 4 — Move it into CDK

The commands above are for proving the path today. Once it works, the queues, role,
and a Lambda event-source mapping belong in `infra/stack.py` so the whole thing
stays reproducible for judges — the submission requires setup instructions that
actually work. Do this as part of M2, not as a separate chore.

## Sender attribution is the payload's real value

Inbound messages carry the sender's phone number, which maps directly to a
`Member`. That is per-person identity for free, with no login and no token —
the same property the signed links give the web page. The Liaison should trust
the sender number for *identity* and nothing else: the message body is untrusted
input from someone who may well be trying to get poutine onto Thursday.
