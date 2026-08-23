"""Mint the public read-only URL for one household.

The link is unguessable by design. `/p/<household_id>` would have been
enumerable -- a guessed household id would read a real family's week -- so
the household travels inside a signed token instead, and no household is
publicly readable until someone deliberately mints one for it.

The token is signed in a separate domain from the family's personal links, so
it cannot authorise a reaction or an outcome. See wotcha.web.tokens.

    WOTCHA_BASE_URL=<the WebUrl output> \
      .venv/bin/python scripts/public_link.py demo
"""
import argparse
import os
import sys

import boto3

from wotcha.config import settings
from wotcha.web.tokens import make_public_token


def build_url(base_url: str, household_id: str, secret: str) -> str:
    """The full link. `base_url` may carry the trailing slash the WebUrl stack
    output has, which would otherwise produce `//p/` -- an empty path segment,
    and a 404 from a handler that splits on them."""
    if not household_id:
        raise ValueError("a household id is required: the token names one household")
    return f"{base_url.rstrip('/')}/p/{make_public_token(household_id, secret)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("household_id", help="the household to publish read-only")
    parser.add_argument(
        "--base-url", default=os.environ.get("WOTCHA_BASE_URL"),
        help="the WebUrl stack output; defaults to $WOTCHA_BASE_URL",
    )
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error("--base-url or WOTCHA_BASE_URL is required")

    s = settings()
    client = boto3.client("secretsmanager", region_name=s.aws_region)
    secret = client.get_secret_value(SecretId=s.link_secret_name)["SecretString"]
    print(build_url(args.base_url, args.household_id, secret))
    return 0


if __name__ == "__main__":
    sys.exit(main())
