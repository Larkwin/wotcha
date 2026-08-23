"""The repository is public, and the household file committed to it is a
sample. The real numbers live in an untracked file beside it.

That separation is currently a convention: `data/household.json` still ships
`"phone": null` fields, which is exactly the shape that invites someone to
fill them in and commit. A convention nothing checks is a convention that
holds until the evening someone is in a hurry with three handsets waiting.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

E164 = re.compile(r"\+\d{10,15}")
# North American +1 numbers with a 555 exchange are the reserved fictional
# range. Everything else that parses as E.164 is treated as somebody's phone.
PLACEHOLDER = re.compile(r"\+1\d{3}555\d{4}$")


def real_phone_numbers(text: str) -> list[str]:
    """Every E.164 number in `text` that is not a reserved placeholder."""
    return [n for n in E164.findall(text) if not PLACEHOLDER.match(n)]


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [ROOT / f for f in out]


def test_placeholder_numbers_are_not_reported():
    assert real_phone_numbers("call +15195550123 or +16135550199") == []


def test_a_non_placeholder_number_is_reported():
    """The check has to actually catch one, or the test below passes for the
    wrong reason forever.

    The fixture is assembled at runtime rather than written as a literal:
    this file is tracked, so any complete E.164 string in it is something the
    check below would flag -- including the one proving the check works."""
    number = "+" + "441632960123"  # Ofcom's reserved drama range, unassigned
    assert real_phone_numbers(f"reach me on {number}") == [number]


def test_no_tracked_file_carries_a_real_phone_number():
    """Includes the sending long code: a public repo hands it to anyone
    looking for a number to abuse, and it is readable from AWS whenever it is
    actually needed."""
    offenders = {}
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        found = real_phone_numbers(text)
        if found:
            offenders[path.relative_to(ROOT).as_posix()] = sorted(set(found))
    assert not offenders, (
        f"real phone numbers in tracked files: {offenders}. Household numbers "
        f"belong in data/household.local.json, which is gitignored."
    )
