"""Discover which Bedrock model identifiers actually work from this account.

Model access is per-model and per-region and may need an explicit request, and
cross-region inference profiles sometimes require a `us.` prefix. Rather than
assume, try each candidate and report. Run this before anything depends on a
model id.
"""
import sys

import boto3
from botocore.exceptions import ClientError
from strands import Agent
from strands.models import BedrockModel

# The product region. Re-run with us-east-1 to exercise the open-weight rungs,
# which ca-central-1 does not offer at all.
REGION = "ca-central-1"

# Verified reachable 2026-08-20 unless noted. Bare ids (no us./global. prefix)
# are rejected -- that is why none appear here.
CANDIDATES = [
    "us.anthropic.claude-sonnet-4-6",                 # working ceiling
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",    # cheap frontier
    "us.anthropic.claude-opus-5",                     # agreement-gated; expected to fail
    "deepseek.v3.2",                                  # us-east-1 only
    "qwen.qwen3-32b-v1:0",                            # us-east-1 only
    "amazon.nova-lite-v1:0",                          # us-east-1 only
]


def list_available() -> None:
    """Print what the account can see, so the candidate list can be corrected."""
    bedrock = boto3.client("bedrock", region_name=REGION)
    try:
        models = bedrock.list_foundation_models()["modelSummaries"]
    except ClientError as exc:
        print(f"  !! list_foundation_models failed: {exc}")
        return
    print(f"  {len(models)} models visible. Matching our providers:")
    for m in models:
        mid = m["modelId"]
        if any(p in mid for p in ("anthropic", "deepseek", "qwen", "llama", "mistral")):
            print(f"    {mid}")


def try_model(model_id: str) -> tuple[bool, str]:
    """Round-trip one trivial prompt through Strands. Success means the id works
    end to end, not merely that it appears in a catalogue listing."""
    try:
        agent = Agent(model=BedrockModel(model_id=model_id, region_name=REGION))
        result = agent("Reply with exactly the word: ready")
        return True, str(result).strip()[:60]
    except Exception as exc:  # noqa: BLE001 - we want every failure mode reported
        return False, f"{type(exc).__name__}: {exc}"[:200]


def main() -> int:
    print(f"Region: {REGION}\n")
    print("Catalogue:")
    list_available()
    print("\nRound-trip tests:")
    working = []
    for model_id in CANDIDATES:
        ok, detail = try_model(model_id)
        print(f"  [{'OK ' if ok else 'FAIL'}] {model_id}\n         {detail}")
        if ok:
            working.append(model_id)
    print(f"\nWorking identifiers: {working or 'NONE'}")
    if not working:
        print("\nNo model responded. Enable model access in the Bedrock console")
        print("(Model access -> Manage model access) and re-run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
