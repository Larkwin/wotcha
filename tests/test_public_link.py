"""The script that mints a public read-only URL."""
import pytest
from public_link import build_url

from wotcha.web.tokens import parse_public_token, parse_token

SECRET = "test-secret"


def test_the_url_carries_a_parseable_public_token():
    url = build_url("https://example.test", "demo", SECRET)
    assert url.startswith("https://example.test/p/")
    assert parse_public_token(url.rsplit("/", 1)[1], SECRET) == "demo"


def test_the_minted_token_cannot_be_used_to_write():
    """The whole reason this is a separate token kind."""
    url = build_url("https://example.test", "demo", SECRET)
    assert parse_token(url.rsplit("/", 1)[1], SECRET) is None


def test_a_trailing_slash_on_the_base_url_does_not_double():
    """The WebUrl stack output carries one, and `//p/` is a 404 -- the path is
    split on empty segments."""
    assert build_url("https://example.test/", "demo", SECRET) == \
        build_url("https://example.test", "demo", SECRET)


def test_minting_for_no_household_is_refused():
    with pytest.raises(ValueError):
        build_url("https://example.test", "", SECRET)
