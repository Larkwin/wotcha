"""Environment-driven settings. Every value has a documented default except
the household id, which must be set explicitly so nothing writes to the
wrong household by accident."""
import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    aws_region: str
    table_name: str
    household_id: str
    bedrock_model_id: str
    liaison_model_id: str
    base_url: str
    link_secret_name: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        aws_region=os.environ.get("WOTCHA_AWS_REGION", "ca-central-1"),
        table_name=os.environ.get("WOTCHA_TABLE_NAME", "wotcha"),
        household_id=os.environ["WOTCHA_HOUSEHOLD_ID"],
        # Inference-profile id, not a bare model id: bare ids are rejected.
        bedrock_model_id=os.environ.get(
            "WOTCHA_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
        ),
        # Separate from bedrock_model_id on purpose. Section 13 selects a
        # model per agent, and the Liaison's job -- extraction and matching
        # against a short list -- is expected to need far less than the
        # Planner's constraint satisfaction. Starting cheap is how that
        # expectation gets tested rather than assumed.
        liaison_model_id=os.environ.get(
            "WOTCHA_LIAISON_MODEL_ID", "ca.amazon.nova-lite-v1:0"
        ),
        base_url=os.environ.get("WOTCHA_BASE_URL", "http://localhost:8080"),
        link_secret_name=os.environ.get("WOTCHA_LINK_SECRET_NAME", "wotcha/link-signing-key"),
    )
