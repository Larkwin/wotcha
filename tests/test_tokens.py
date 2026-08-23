from wotcha.web.tokens import (
    make_public_token,
    make_token,
    parse_public_token,
    parse_token,
)

SECRET = "test-secret-key"


def test_token_round_trips():
    t = make_token("demo", "maya", SECRET)
    assert parse_token(t, SECRET) == ("demo", "maya")


def test_tokens_differ_per_person():
    assert make_token("demo", "maya", SECRET) != make_token("demo", "sam", SECRET)


def test_token_is_stable_so_links_can_be_permanent():
    assert make_token("demo", "maya", SECRET) == make_token("demo", "maya", SECRET)


def test_tampered_token_is_rejected():
    t = make_token("demo", "maya", SECRET)
    body, sig = t.split(".")
    assert parse_token(f"{body}.{'a' * len(sig)}", SECRET) is None


def test_token_from_another_secret_is_rejected():
    """Rotating the secret is how a link gets revoked."""
    assert parse_token(make_token("demo", "maya", SECRET), "different") is None


def test_malformed_token_is_rejected_without_raising():
    for bad in ("", "nodot", "a.b.c", "!!!.???"):
        assert parse_token(bad, SECRET) is None


def test_non_ascii_signature_is_rejected_without_raising():
    """hmac.compare_digest raises TypeError on non-ASCII str input rather than
    returning False. The contract is 'never raise, return None for anything
    untrustworthy' -- a non-ASCII signature is untrustworthy, not exceptional."""
    t = make_token("demo", "maya", SECRET)
    body, _sig = t.split(".")
    for bad_sig in ("café" + "x" * 20, "über-signature-value"):
        assert parse_token(f"{body}.{bad_sig}", SECRET) is None


def test_non_ascii_body_is_rejected_without_raising():
    """`_sign(body, secret)` calls `body.encode()` inside the same try block
    that guards the signature comparison -- non-ASCII in the *body* half (not
    just the signature half) must not raise either. A token is base64url plus
    a `.` separator, so it is ASCII by construction; anything else is
    invalid before any cryptography needs to happen."""
    for bad in ("café.somesig12345678901234567890", "\ud800.somesig12345678901234567"):
        assert parse_token(bad, SECRET) is None


# --- public read-only tokens --------------------------------------------

def test_a_public_token_names_a_household_and_no_person():
    t = make_public_token("demo", SECRET)
    assert parse_public_token(t, SECRET) == "demo"


def test_a_public_token_is_not_a_person_token():
    """The write routes (/r/, /o/) authorise with parse_token. If a public
    link parsed there, handing someone a read-only URL would hand them the
    ability to write signals and outcomes. They must be mutually unparseable
    by construction, not by a check someone can forget to add."""
    t = make_public_token("demo", SECRET)
    assert parse_token(t, SECRET) is None


def test_a_person_token_is_not_a_public_token():
    """The converse: a person's private link must not become a public page
    URL, which would expose their household to anyone holding it."""
    t = make_token("demo", "maya", SECRET)
    assert parse_public_token(t, SECRET) is None


def test_a_public_token_signed_with_another_secret_is_rejected():
    assert parse_public_token(make_public_token("demo", SECRET), "other") is None


def test_a_tampered_public_token_is_rejected():
    sig = make_public_token("demo", SECRET).split(".")[1]
    forged = make_public_token("another-household", SECRET).split(".")[0]
    assert parse_public_token(f"{forged}.{sig}", SECRET) is None


def test_public_token_garbage_is_rejected():
    for bad in ("", ".", "no-dot", "a.b.c", "☃.☃"):
        assert parse_public_token(bad, SECRET) is None
