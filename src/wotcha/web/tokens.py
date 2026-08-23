"""Permanent per-person links.

A token is `base64url(household_id:person_id).hmac`. There is no expiry, by
design: the link lives in a text message the family keeps forever. Revocation is
rotating the signing secret, which invalidates every link at once -- acceptable
for one household, and the honest trade for zero onboarding friction.
"""
import base64
import binascii
import hmac
from hashlib import sha256

SIG_LEN = 24


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), body.encode(), sha256).digest()
    return _b64e(mac)[:SIG_LEN]


def make_token(household_id: str, person_id: str, secret: str) -> str:
    body = _b64e(f"{household_id}:{person_id}".encode())
    return f"{body}.{_sign(body, secret)}"


def _public_sign(body: str, secret: str) -> str:
    """Sign a public token under a separate domain from person tokens.

    Domain separation, not a flag inside the payload: it makes the two token
    kinds mutually unparseable rather than merely distinguishable. A public
    link handed to a stranger must not authorise anything -- and /r/ and /o/
    authorise with `parse_token`, so the guarantee has to hold in the crypto
    rather than in a check every future write route remembers to add.

    The body also carries no `:`, so even a signature collision would leave
    `parse_token`'s split raising rather than returning a household.
    """
    return _sign(f"public.{body}", secret)


def make_public_token(household_id: str, secret: str) -> str:
    """A read-only link to one household's published week.

    No person, because nobody is looking: the public page shows the plan and
    its reasoning, never who eats it.
    """
    body = _b64e(household_id.encode())
    return f"{body}.{_public_sign(body, secret)}"


def parse_public_token(token: str, secret: str) -> str | None:
    """Return the household id, or None for anything untrustworthy.

    Same input-hardening as `parse_token` -- this endpoint is reachable by
    anyone with the URL, which is the entire point of it.
    """
    if not token.isascii():
        return None
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    try:
        if not hmac.compare_digest(sig, _public_sign(body, secret)):
            return None
    except TypeError:
        return None
    try:
        household_id = _b64d(body).decode()
    except (UnicodeDecodeError, binascii.Error):
        return None
    return household_id or None


def parse_token(token: str, secret: str) -> tuple[str, str] | None:
    """Return (household_id, person_id), or None for anything untrustworthy."""
    # A token is base64url plus a `.` separator -- ASCII by construction.
    # Reject non-ASCII input up front rather than relying on individual
    # except clauses downstream: `body.encode()` inside `_sign` raises
    # UnicodeEncodeError on an unpaired surrogate, compare_digest raises
    # TypeError on a non-ASCII signature, and there may be other encoding
    # failures neither of us has enumerated. This makes the whole class
    # unreachable instead of chasing it clause by clause.
    if not token.isascii():
        return None
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    try:
        # Defence in depth: compare_digest raises TypeError on non-ASCII str
        # input instead of returning False. The isascii() guard above should
        # already make this unreachable, but a non-ASCII signature is
        # untrustworthy input either way, not an exceptional condition.
        if not hmac.compare_digest(sig, _sign(body, secret)):
            return None
    except TypeError:
        return None
    try:
        household_id, person_id = _b64d(body).decode().split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return household_id, person_id
