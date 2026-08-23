# scripts/preflight_sms.py
"""Verify SMS reaches every family handset.

The account starts in the AWS End User Messaging sandbox, which permits up to 10
verified destination numbers -- exactly one family. Each number must be verified
once with a code before it can receive anything.

Sending requires WOTCHA_SMS_ORIGINATION_ID (the PhoneNumberId to send from);
`list`, `verify` and `confirm` do not.

Usage:
  python scripts/preflight_sms.py list
  python scripts/preflight_sms.py spend
  python scripts/preflight_sms.py verify +15195550123
  python scripts/preflight_sms.py confirm +15195550123 123456
  python scripts/preflight_sms.py send +15195550123
"""
import os
import sys
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

# The origination number must be a Canadian long code, provisioned in the
# product region. Confirmed 2026-08-20: account tier is SANDBOX in both
# ca-central-1 and us-east-1.
REGION = "ca-central-1"


def origination_id() -> str:
    """The phone number id to send from, read at call time from the
    environment.

    Never a literal in this file. An origination id is an account-specific
    AWS resource identifier, and this repo's standing rule is that no phone
    number or origination id is written into it. Read inside cmd_send rather
    than at import, so `list`, `verify` and `confirm` -- none of which send
    anything -- keep working without it set.
    """
    try:
        return os.environ["WOTCHA_SMS_ORIGINATION_ID"]
    except KeyError:
        raise SystemExit(
            "WOTCHA_SMS_ORIGINATION_ID is not set. Find it with:\n"
            "  aws pinpoint-sms-voice-v2 describe-phone-numbers "
            f"--region {REGION} \\\n"
            "    --query 'PhoneNumbers[].[PhoneNumber,PhoneNumberId]' --output table"
        ) from None


# The sandbox ceiling, and the alarm threshold infra/stack.py sets at half
# of it. Duplicated rather than imported: stack.py runs under infra/.venv
# with the CDK installed and this script does not, so there is no import path
# between them. If one changes, change both -- and the test below is what
# makes a drift visible.
SPEND_CEILING_USD = 1.00
SPEND_ALARM_USD = SPEND_CEILING_USD / 2
ALARM_NAME = "wotcha-sms-monthly-spend"
ALARMS_TOPIC = "wotcha-alarms"


def client():
    return boto3.client("pinpoint-sms-voice-v2", region_name=REGION)


def read_spend(datapoints: list[dict]) -> tuple[float | None, str]:
    """Interpret the TextMessageMonthlySpend datapoints. Pure, so the
    judgement can be tested without touching AWS.

    No datapoints is the case worth being careful about, and it is NOT the
    same as zero spend. The metric requires the service-linked role to exist
    and does not publish at all until at least one message has been sent, so
    silence means either "nothing sent this month" or "this account is not
    publishing the metric and the alarm can never fire" -- and those need
    opposite responses. Say so rather than printing $0.00 and looking fine.
    """
    if not datapoints:
        return None, (
            "no datapoints. That is NOT $0.00 spent -- the metric needs the "
            "service-linked role and does not publish until at least one "
            "message has been sent. An alarm on a metric nobody publishes "
            "sits in INSUFFICIENT_DATA forever and never fires."
        )
    spent = max(d["Maximum"] for d in datapoints)
    if spent >= SPEND_CEILING_USD:
        return spent, (
            f"AT THE CEILING (${SPEND_CEILING_USD:.2f}). Sends are being "
            f"stopped. In the sandbox this cannot be raised -- only "
            f"production access lifts it, and that also drops the "
            f"verified-destination restriction."
        )
    if spent >= SPEND_ALARM_USD:
        return spent, (
            f"past the ${SPEND_ALARM_USD:.2f} alarm threshold, "
            f"${SPEND_CEILING_USD - spent:.2f} left this month."
        )
    return spent, f"under the ${SPEND_ALARM_USD:.2f} alarm threshold."


def cmd_spend() -> None:
    """Answer both halves of "is the spend alarm real": is the metric being
    published, and would the alarm reach a person.

    The second half exists because the topic is created by CDK and subscribed
    by hand. A topic with no confirmed subscriber is an alarm that fires into
    nothing -- the same silent failure the alarm was built to close, one level
    up. A *pending* subscription counts as nobody: an unconfirmed email
    address receives no notifications.
    """
    cw = boto3.client("cloudwatch", region_name=REGION)
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stats = cw.get_metric_statistics(
        Namespace="AWS/SMSVoice",
        MetricName="TextMessageMonthlySpend",
        StartTime=month_start,
        EndTime=now,
        Period=3600,
        Statistics=["Maximum"],
    )["Datapoints"]
    spent, note = read_spend(stats)
    shown = "unknown" if spent is None else f"${spent:.2f}"
    print(f"SMS spend this month: {shown} -- {note}")

    alarms = cw.describe_alarms(AlarmNames=[ALARM_NAME])["MetricAlarms"]
    if not alarms:
        print(f"Alarm {ALARM_NAME!r}: MISSING. Run `make deploy`.")
    else:
        state = alarms[0]["StateValue"]
        print(f"Alarm {ALARM_NAME!r}: {state}")
        if state == "INSUFFICIENT_DATA":
            print("  INSUFFICIENT_DATA is expected until the first send of a "
                  "month, and permanent if the metric never publishes.")

    sns = boto3.client("sns", region_name=REGION)
    topic = next((t["TopicArn"] for t in sns.list_topics()["Topics"]
                  if t["TopicArn"].rsplit(":", 1)[-1] == ALARMS_TOPIC), None)
    if topic is None:
        print(f"Topic {ALARMS_TOPIC!r}: MISSING. Run `make deploy`.")
        return
    subs = sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
    confirmed = [s for s in subs if s["SubscriptionArn"].startswith("arn:")]
    if not confirmed:
        print(f"Topic {ALARMS_TOPIC!r}: NO CONFIRMED SUBSCRIBER -- the alarm "
              f"fires into nothing. Subscribe an address and click the "
              f"confirmation link:")
        print(f"  aws sns subscribe --region {REGION} --topic-arn {topic} \\")
        print("    --protocol email --notification-endpoint <you@example.com>")
    else:
        print(f"Topic {ALARMS_TOPIC!r}: {len(confirmed)} confirmed subscriber(s).")


def cmd_list() -> None:
    c = client()
    print("Origination identities:")
    for p in c.describe_phone_numbers()["PhoneNumbers"]:
        print(f"  {p['PhoneNumber']}  id={p['PhoneNumberId']}  status={p['Status']}")
    print("Verified destinations:")
    for d in c.describe_verified_destination_numbers()["VerifiedDestinationNumbers"]:
        print(f"  {d['DestinationPhoneNumber']}  status={d['Status']}")


def cmd_verify(number: str) -> None:
    c = client()
    created = c.create_verified_destination_number(DestinationPhoneNumber=number)
    vid = created["VerifiedDestinationNumberId"]
    c.send_destination_number_verification_code(
        VerifiedDestinationNumberId=vid, VerificationChannel="TEXT"
    )
    print(f"Code sent to {number}. Run: confirm {number} <code>")


def cmd_confirm(number: str, code: str) -> None:
    c = client()
    for d in c.describe_verified_destination_numbers()["VerifiedDestinationNumbers"]:
        if d["DestinationPhoneNumber"] == number:
            c.verify_destination_number(
                VerifiedDestinationNumberId=d["VerifiedDestinationNumberId"],
                VerificationCode=code,
            )
            print(f"{number} verified.")
            return
    print(f"{number} not found. Run verify first.")


def cmd_send(number: str) -> None:
    c = client()
    resp = c.send_text_message(
        DestinationPhoneNumber=number,
        OriginationIdentity=origination_id(),
        MessageBody="Wotcha preflight. If you can read this, reply OK.",
        MessageType="TRANSACTIONAL",
    )
    print(f"Sent. MessageId={resp['MessageId']}")
    print("A MessageId means AWS accepted it, NOT that a carrier delivered it.")
    print("Confirm on the actual handset before recording success.")


COMMANDS = {"list": cmd_list, "spend": cmd_spend, "verify": cmd_verify,
            "confirm": cmd_confirm, "send": cmd_send}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd, *args = sys.argv[1:]
    # Looked up, not called, inside this branch: with the call inside a
    # `try: {...}[cmd](*args) except KeyError`, ANY KeyError raised inside a
    # command -- a missing key in an AWS response, say -- printed the usage
    # text as if the operator had mistyped the command name, hiding the real
    # failure entirely.
    handler = COMMANDS.get(cmd)
    if handler is None:
        print(f"unknown command: {cmd!r}\n")
        print(__doc__)
        return 1
    try:
        handler(*args)
    except ClientError as exc:
        print(f"AWS error: {exc}")
        print("If a parameter name was rejected, check: aws sms-voice help")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
