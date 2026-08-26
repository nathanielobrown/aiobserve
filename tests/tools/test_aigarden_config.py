"""What `aigarden.toml` has to keep saying: the one costly exemption, and no rotting ones.

The config is data the linter reads, so nothing here runs the linter — these leaves guard the
two hazards a one-time probe catches once and then never again: an exemption quietly dropped,
and an exemption still listed after the thing it excused is gone.
"""

import glob
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# aigarden's built-in budget for a source file. Mirrored here so a ratchet entry that no longer
# excuses anything reads as red; if aigarden moves the budget, this moves with it.
SOURCE_FILE_BUDGET = 700


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
