"""Discover which Bedrock model identifiers actually work from this account.

Model access is per-model and per-region and may need an explicit request, and
cross-region inference profiles sometimes require a `us.` prefix. Rather than
assume, try each candidate and report. Run this before anything depends on a
model id.
"""
import os
import sys

import boto3
from botocore.exceptions import ClientError
from strands import Agent
from strands.models import BedrockModel

# The product region, and everything the ladder needs is in it. An earlier
# version of this comment said to override to us-east-1 "to exercise the
# open-weight rungs, which ca-central-1 does not offer at all" -- untrue.
# Llama and Mistral are ON_DEMAND right here; only DeepSeek and Qwen are
# genuinely us-east-1-only, and they are not the only open-weight models.
# The override remains, for reaching those two specifically:
#   WOTCHA_PREFLIGHT_REGION=us-east-1 python scripts/preflight_bedrock.py
REGION = os.environ.get("WOTCHA_PREFLIGHT_REGION", "ca-central-1")

# How a model must be named depends on its `inferenceTypesSupported`, and
# there is no rule of thumb that covers both cases:
#
#   ON_DEMAND         -- the bare model id works. `deepseek.v3.2` and
#                        `qwen.qwen3-32b-v1:0` are this, in us-east-1.
#   INFERENCE_PROFILE -- the bare id is refused with "Invocation of model ID
#                        ... with on-demand throughput isn't supported", and
#                        you must name a profile: us./global./ca./eu./apac.
#
# That second error reads like an availability problem and is not one.
# `amazon.nova-lite-v1:0` sat in this list annotated "us-east-1 only" for
# exactly that reason, while `ca.amazon.nova-lite-v1:0` was ACTIVE in the
# product region the whole time. list_profiles() below exists so that is
# discoverable rather than guessed at -- list_foundation_models returns bare
# ids and can never show you a profile id.
#
# Every annotation here was checked against list-inference-profiles and
# list-foundation-models on 2026-08-24. "Absent" means no profile and no
# on-demand model of that name exists in the region named.
CANDIDATES = [
    "us.anthropic.claude-sonnet-4-6",                 # working ceiling
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",    # cheap frontier
    # Profile is ACTIVE in ca-central-1; the *account* lacks model access, so
    # this fails with AccessDenied rather than ValidationException. The fix is
    # Bedrock console -> Model access, not a different identifier.
    "us.anthropic.claude-opus-5",
    "ca.amazon.nova-lite-v1:0",                       # in-region; profile-only
    "global.amazon.nova-2-lite-v1:0",                 # in-region; profile-only
    # The open-weight rungs, on-demand in ca-central-1 -- no us-east-1 needed.
    # `list_available` has matched "llama" and "mistral" since this script was
    # written, so these printed on every preflight run and were never read.
    # That is the same failure as the Nova entry: the identifier was on the
    # screen and the annotation next to it said otherwise.
    "meta.llama3-70b-instruct-v1:0",                  # open-weight large
    "mistral.mixtral-8x7b-instruct-v0:1",             # open-weight MoE
    # Genuinely us-east-1-only: no profile and no on-demand model of either
    # name exists in ca-central-1. Reached with WOTCHA_PREFLIGHT_REGION.
    "deepseek.v3.2",                                  # on-demand, us-east-1 only
    "qwen.qwen3-32b-v1:0",                            # on-demand, us-east-1 only
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
            # The inference type is the whole point of printing this: it says
            # whether the bare id above is usable as-is (ON_DEMAND) or whether
            # you need a profile id from the next section instead.
            types = ",".join(m.get("inferenceTypesSupported") or ["-"])
            print(f"    {mid}  [{types}]")


def list_profiles() -> None:
    """Print the inference profiles this region offers.

    This is the half that was missing, and its absence is why a model that was
    ACTIVE here got annotated "us-east-1 only". `list_foundation_models`
    returns bare model ids, and a bare id is exactly what cannot be invoked --
    so the discovery half of this script was looking at a list that could
    never contain a usable identifier. These are the ids that belong in
    CANDIDATES.
    """
    bedrock = boto3.client("bedrock", region_name=REGION)
    try:
        profiles = bedrock.list_inference_profiles()["inferenceProfileSummaries"]
    except ClientError as exc:
        print(f"  !! list_inference_profiles failed: {exc}")
        return
    print(f"  {len(profiles)} inference profiles in {REGION}:")
    for p in sorted(profiles, key=lambda x: x["inferenceProfileId"]):
        print(f"    {p['inferenceProfileId']}  {p['status']}")


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
    print("\nInference profiles:")
    list_profiles()
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
