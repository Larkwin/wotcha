# scripts/preflight_sms.py
"""Verify SMS reaches every family handset.

The account starts in the AWS End User Messaging sandbox, which permits up to 10
verified destination numbers -- exactly one family. Each number must be verified
once with a code before it can receive anything.

Sending requires WOTCHA_SMS_ORIGINATION_ID (the PhoneNumberId to send from);
`list`, `verify` and `confirm` do not.

Usage:
  python scripts/preflight_sms.py list
  python scripts/preflight_sms.py verify +15195550123
  python scripts/preflight_sms.py confirm +15195550123 123456
  python scripts/preflight_sms.py send +15195550123
"""
import os
import sys

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


def client():
    return boto3.client("pinpoint-sms-voice-v2", region_name=REGION)


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


COMMANDS = {"list": cmd_list, "verify": cmd_verify,
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
