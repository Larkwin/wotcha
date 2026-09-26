"""A fingerprint of what an agent was told, stamped on every eval record.

`model_id` says which rung answered; this says what it was asked. Without it
the corpus cannot tell a change of model from a change of prompt, and replay
cannot group records by the instructions they were produced under -- the
Planner's prompt changed on 2026-08-24 and the records either side of that
are indistinguishable for exactly this reason.

A content hash rather than a hand-kept number, on purpose. A version someone
has to remember to bump is a version that is wrong the first time someone is
in a hurry, and a wrong stamp is worse than none: it asserts two prompts were
the same. A hash cannot be forgotten. What it gives up is ordering -- two
hashes do not say which came first -- and the record's own timestamp already
answers that.

It covers the static text the model reads: the system prompt, the kickoff
message template, and the tool or output schemas, whose docstrings are
instructions as much as the prompt is. It does not cover data rendered per
call (the roster, the fence) -- that is the scenario, not the prompt.
"""
import hashlib

# Twelve hex characters: 48 bits, far past collision range for the handful of
# prompt revisions one household's corpus will ever see, and short enough to
# read in a DynamoDB console.
_LENGTH = 12


def fingerprint(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        # Length-prefixed so ("ab", "c") and ("a", "bc") cannot collide.
        encoded = part.encode("utf-8")
        digest.update(f"{len(encoded)}:".encode())
        digest.update(encoded)
    return digest.hexdigest()[:_LENGTH]
