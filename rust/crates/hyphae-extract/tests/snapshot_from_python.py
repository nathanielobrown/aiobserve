"""Write the ids-and-parents snapshot the Rust walk is checked against.

The expected side of the parity leaf comes from the Python extractor, so the snapshot is
generated rather than typed: run this and the Rust test compares its own dump against what
`hyphae.extract` produced for the same transcripts.

    mise x -- python rust/crates/hyphae-extract/tests/snapshot_from_python.py

`dump()` below is the one place the format lives on this side; `tests/walk.rs:dump` is its
twin, and the two disagreeing is exactly what the leaf is for.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from hyphae.extract.claude_code import ClaudeCodeExtractor  # noqa: E402
from hyphae.model import SessionTrace  # noqa: E402
from hyphae.pipeline import SessionSource  # noqa: E402
from hyphae.sessions import SessionFiles  # noqa: E402

# The fixtures the leaf walks: the deepest run tree, and the three shapes whose ids and
# parents are easiest to get subtly wrong — parallel tools, a teammate thread, a compaction.
FIXTURES = (
    ("spine", "4208c1bd-78a0-46ef-9d3c-269b9b7a8e2b"),
    ("parallel_tools", "5f4b59fb-a9a8-4ca1-af62-a64b9d0ce515"),
    ("teammate", "10d0349d-0705-4e23-aa64-5b1b97698b2e"),
    ("compaction", "1de7cf38-b28a-4c7d-9a6d-66ebe002cfa9"),
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "walk__ids_and_parents.snap"


def flag(value: object) -> str:
    """A boolean as Rust prints it, so neither side has to translate."""
    return "true" if value else "false"


def dump(trace: SessionTrace) -> list[str]:
    """One line per entity, in the order the extractor produced them."""
    lines = [f"session {trace.session.id}"]
    lines += [
        f"  turn {t.index} {t.source} {t.id} replayed={flag(t.replayed)}" for t in trace.turns
    ]
    lines += [
        f"  call {c.index} {c.source} {c.id} turn={c.turn_id} replayed={flag(c.replayed)}"
        for c in trace.api_calls
    ]
    lines += [
        f"  tool {t.index} {t.source} {t.id} call={t.api_call_id} replayed={flag(t.replayed)}"
        for t in trace.tool_calls
    ]
    lines += [
        f"  run {r.id} parent={r.parent_agent_id} tool={r.tool_use_id} depth={r.spawn_depth}"
        for r in trace.agent_runs
    ]
    lines += [f"  compaction {c.source} {c.id}" for c in trace.compactions]
    return lines


def main() -> None:
    extractor = ClaudeCodeExtractor()
    lines: list[str] = []
    for directory, stem in FIXTURES:
        transcript = REPO / "tests" / "fixtures" / directory / f"{stem}.jsonl"
        files = SessionFiles(id=stem, transcript=transcript)
        source = SessionSource(id=stem, files=tuple(files.files()), fingerprint="fixture")
        lines += dump(extractor.extract(source))
    # insta's own file format: a YAML header naming what produced the value, then the value.
    header = "---\nsource: rust/crates/hyphae-extract/tests/walk.rs\nexpression: dump\n---\n"
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(header + "\n".join(lines) + "\n")
    print(f"{len(lines)} line(s) -> {SNAPSHOT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
