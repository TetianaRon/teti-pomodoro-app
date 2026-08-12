"""Guards the numbers the documentation quotes about this repo.

The test count is the one figure that changes with *any* work, and it is
load-bearing in `docs/MAC-PORT.md`: it appears at step 5 of the setup as
"you should see N passing", which is the first thing a non-developer checks
on a fresh machine. A stale figure there reads as *"something is broken on
my computer"* before she has touched anything — and it went stale three
times in two days, caught only by someone thinking to ask.

So it is checked mechanically instead. Adding tests now fails the suite
until the documents are updated, which is the correct order: the number is a
claim about this repo, and a claim nobody verifies is just a comment.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Every place a document states how many tests there are. Each pattern must
#: capture the number in group 1.
CLAIMS = (
    ("docs/MAC-PORT.md", r"\*\*all (\d+) automated tests\*\*"),
    ("docs/MAC-PORT.md", r"\*\*You should see (\d+) passing\.\*\*"),
    ("PLAN.md", r"in daily use\.\*\* (\d+) tests"),
    ("PLAN.md", r"pytest tests\s+# (\d+) tests"),
)


def claimed_counts() -> dict[str, int]:
    """Every quoted figure, keyed by where it was found."""
    found = {}
    for name, pattern in CLAIMS:
        text = (REPO / name).read_text(encoding="utf-8")
        match = re.search(pattern, text)
        assert match is not None, (
            f"{name} no longer states a test count matching {pattern!r}. "
            f"Either restore the sentence or drop it from CLAIMS — a check "
            f"that quietly stops checking is worse than no check."
        )
        found[f"{name}  ({pattern})"] = int(match.group(1))
    return found


def partial_run(config) -> bool:
    """Whether this run collected only part of the suite.

    Running one file while working on it is normal, and the total is
    meaningless then. Detected from how pytest was invoked rather than from
    the count itself, which would be circular.
    """
    option = config.option
    if getattr(option, "keyword", "") or getattr(option, "markexpr", ""):
        return True
    if getattr(option, "lf", False) or getattr(option, "failedfirst", False):
        return True
    return any(
        pathlib.Path(str(arg).split("::")[0]).is_file()
        for arg in config.args
    )


def test_the_documents_agree_with_each_other():
    """Checked separately, so a mismatch between them is not mistaken for a
    stale count. This one holds however the suite was invoked."""
    counts = claimed_counts()
    assert len(set(counts.values())) == 1, (
        "the documents quote different test counts:\n  "
        + "\n  ".join(f"{where}: {n}" for where, n in counts.items())
    )


def test_the_documents_quote_the_real_test_count(request):
    if partial_run(request.config):
        pytest.skip("only meaningful when the whole suite is collected")

    collected = request.session.testscollected
    for where, claimed in claimed_counts().items():
        assert claimed == collected, (
            f"{where}\n  says {claimed} tests, the suite collects {collected}.\n"
            f"  Update the figure in docs/MAC-PORT.md and PLAN.md to "
            f"{collected}."
        )
