"""EventBridge Scheduler target: ask the AgentCore runtime to plan and notify.

A thin Lambda rather than a direct Scheduler-to-AgentCore target, because a
Lambda gives ordinary CloudWatch logs -- which matters when the next look at
this is days later.

API shape verified 2026-08-20 against the installed `bedrock-agentcore`
botocore service model (not just documentation): `invoke_agent_runtime` takes
`agentRuntimeArn` (required, string) and `payload` (required, blob) and
returns `response` as a streaming blob under that exact key. The brief's
shape was correct as written; no correction was needed.
"""
import json
import os

import boto3


def handler(event, _context):
    client = boto3.client("bedrock-agentcore",
                          region_name=os.environ["WOTCHA_AWS_REGION"])
    payload = json.dumps({"action": "plan_and_notify"}).encode()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["WOTCHA_RUNTIME_ARN"],
        payload=payload,
        contentType="application/json",
    )
    body = response["response"].read().decode()
    print(f"runtime response: {body}")
    return {"ok": True, "runtime_response": body}
