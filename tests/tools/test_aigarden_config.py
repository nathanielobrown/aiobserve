"""What `aigarden.toml` has to keep saying: the one costly exemption, and no rotting ones.

The config is data the linter reads, so nothing here runs the linter — these leaves guard the
two hazards a one-time probe catches once and then never again: an exemption quietly dropped,
and an exemption still listed after the thing it excused is gone.
"""

import glob
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# aigarden's built-in budget for a source file. Mirrored here so a ratchet entry that no longer
# excuses anything reads as red; if aigarden moves the budget, this moves with it.
SOURCE_FILE_BUDGET = 700

# The repository's share of aigarden's source-file budget glob — the languages we actually
# write. A file of some other kind is measured by whatever budget aigarden gives its extension.
SOURCE_GLOBS = ("*.py", "*.sh", "*.js")


@pytest.fixture(scope="module")
def config() -> dict:
    return tomllib.loads((ROOT / "aigarden.toml").read_text())


@pytest.fixture(scope="module")
def per_file_ignores(config: dict) -> dict[str, list[str]]:
    return config["per-file-ignores"]


def matches(pattern: str) -> list[str]:
    """Every file in the repo the pattern matches, directories dropped."""
    found = glob.glob(pattern, root_dir=ROOT, recursive=True)
    return [path for path in found if (ROOT / path).is_file()]


def test_prompt_templates_are_exempt_from_markdown_style(
    per_file_ignores: dict[str, list[str]],
) -> None:
    # The expensive one. A prompt template's bytes are model input: reflowing one changes the
    # prompt, which changes the stamp, which stales every enrichment derived from it
    # (`docs/enrichment.md`). The damage is invisible until a pass re-runs the whole corpus,
    # so the entry's loss has to be loud here rather than in a review.
    assert "markdown-style" in per_file_ignores["src/aiobserve/analyze/templates/**"]


def test_every_ignored_pattern_still_matches_a_file(
    per_file_ignores: dict[str, list[str]],
) -> None:
    # An exemption for a file that moved is an exemption nobody can see is dead, and it excuses
    # whatever later takes the path. Each one has to keep naming something real.
    empty = [pattern for pattern in per_file_ignores if not matches(pattern)]
    assert not empty, f"exemptions matching nothing in the repo: {empty}"


def test_every_file_length_exemption_names_a_file_still_over_budget(
    per_file_ignores: dict[str, list[str]],
) -> None:
    # The ratchet only ratchets if splitting a file takes its entry out with it. A file that
    # came back under the budget while its exemption stayed would leave the budget off for the
    # next thing that grows there. Only the named files are held to the line budget: the one
    # wildcard carrying `file-length` is `plans/**`, exempt as history and measured in tokens.
    ratchet = [
        pattern
        for pattern, rules in per_file_ignores.items()
        if "file-length" in rules and "*" not in pattern
    ]
    assert ratchet, "no per-file `file-length` entries left to check"
    under = {
        pattern: lines
        for pattern in ratchet
        if (lines := len((ROOT / pattern).read_text().splitlines())) <= SOURCE_FILE_BUDGET
    }
    assert not under, f"file-length exemptions no longer excusing anything: {under}"


def test_no_source_file_grows_past_the_budget_unexcused(
    per_file_ignores: dict[str, list[str]],
) -> None:
    # The other half of the ratchet: it only holds the line if every file over the budget is
    # named. A new one that grows past it and is not listed would be a finding the linter
    # reports and nothing else does — the linter is not wired into `check` from here, so this
    # is where a file written past the budget goes red.
    excused = {
        path
        for pattern, rules in per_file_ignores.items()
        if "file-length" in rules
        for path in matches(pattern)
    }
    # Everything the linter would walk: tracked files plus any new one not gitignored, since a
    # file written this session is exactly the one that could be over the budget.
    walked = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", *SOURCE_GLOBS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    over = {
        path: lines
        for path in walked
        if path
        and path not in excused
        # aigarden excludes recorded fixtures, and so must this: their size is the recording's.
        and "fixtures/" not in path
        and (lines := len((ROOT / path).read_text().splitlines())) > SOURCE_FILE_BUDGET
    }
    assert not over, f"over the {SOURCE_FILE_BUDGET}-line budget and not in the ratchet: {over}"
