"""The Layout tree in `CLAUDE.md`: what lives where, glossed by the thing that lives there.

Run by a cog block in that file — `uv run python -m tools.gen_layout` — and it writes the fence
as well as the tree, because a cog marker inside a fenced block is an example rather than a live
block. The entry list below is curated: the tree is an index a reader walks, so what it shows
and in what order is an editorial choice. What each entry *says* is not — a package's gloss is
its own docstring and a document's is its opening sentence, so the tree cannot drift from the
thing it describes without that thing drifting first.

Three directories carry words written here instead: they hold no prose about themselves, and
what a reader needs to know about them is a convention rather than a summary.
"""

import importlib
from pathlib import Path
from typing import NamedTuple

from tools import text

ROOT = Path(__file__).resolve().parent.parent


class Module(NamedTuple):
    """A gloss lifted from a package or module docstring."""

    name: str


class Doc(NamedTuple):
    """A gloss lifted from a document's opening sentence, its heading skipped."""

    path: str


class Words(NamedTuple):
    """A gloss written here, for a directory nothing in the repo describes."""

    said: str


Gloss = Module | Doc | Words


class Entry(NamedTuple):
    """One line of the tree: a repo-root path, and where its gloss comes from.

    A directory carries a trailing slash. `gloss` is None for an entry that only heads the ones
    under it, and `scratch` marks a gitignored directory a fresh clone will not have.
    """

    path: str
    gloss: Gloss | None
    scratch: bool = False


# Top-level directories the tree leaves out, each with why. Guidance for the agent rather than
# the project's own layout: a reader of `CLAUDE.md` is already inside the first.
UNLISTED = {
    ".claude/": "the agent's own configuration — rules, hooks, skills and agents",
    ".github/": "CI, which runs exactly `mise run check`",
    ".vscode/": "editor settings, personal to whoever opens the repo",
}

# The tree, in reading order: the package first, then what tests and documents it.
ENTRIES = (
    Entry("src/hyphae/", Module("hyphae")),
    Entry("src/hyphae/extract/", Module("hyphae.extract")),
    Entry("src/hyphae/export/", Module("hyphae.export")),
    Entry("src/hyphae/enrich/", Module("hyphae.enrich")),
    Entry("src/hyphae/analyze/", Module("hyphae.analyze")),
    Entry("src/hyphae/view/", Module("hyphae.view")),
    Entry("src/hyphae/pipeline.py", Module("hyphae.pipeline")),
    Entry("tests/", Module("tests")),
    Entry("tools/", Module("tools")),
    Entry("docs/", None),
    Entry("docs/analysis.md", Doc("docs/analysis.md")),
    Entry("docs/schema.md", Doc("docs/schema.md")),
    Entry("docs/transcript-reading.md", Doc("docs/transcript-reading.md")),
    Entry("docs/session-layout.md", Doc("docs/session-layout.md")),
    Entry("docs/store.md", Doc("docs/store.md")),
    Entry("docs/enrichment.md", Doc("docs/enrichment.md")),
    Entry("docs/viewer.md", Doc("docs/viewer.md")),
    Entry("docs/viewer-bounds.md", Doc("docs/viewer-bounds.md")),
    Entry("docs/viewer-titles.md", Doc("docs/viewer-titles.md")),
    Entry("docs/ui-development.md", Doc("docs/ui-development.md")),
    Entry("docs/otlp-export.md", Doc("docs/otlp-export.md")),
    Entry("docs/documentation.md", Doc("docs/documentation.md")),
    Entry("docs/writing_style_guide.md", Doc("docs/writing_style_guide.md")),
    Entry("docs/mermaid-guide.md", Doc("docs/mermaid-guide.md")),
    Entry("docs/pull-requests.md", Doc("docs/pull-requests.md")),
    Entry("docs/commits.md", Doc("docs/commits.md")),
    Entry("docs/doc-sync.md", Doc("docs/doc-sync.md")),
    Entry("docs/handoffs.md", Doc("docs/handoffs.md")),
    Entry(
        "plans/",
        Words(
            "Designs and testing plans, one directory per change — committed on the "
            "implementing branch, not left untracked on main (`docs/documentation.md`)"
        ),
    ),
    Entry("reports/", Doc("reports/README.md")),
    Entry(
        "handoffs/",
        Words("Gitignored: scratch one agent run leaves for the next (`docs/handoffs.md`)"),
        scratch=True,
    ),
    Entry(
        "data/",
        Words(
            "Gitignored: the canonical trace store `traces.duckdb` (`docs/store.md`) and "
            "analysis scratch"
        ),
        scratch=True,
    ),
)


def glossed(gloss: Gloss) -> str:
    """One entry's gloss, lifted from wherever it lives."""
    match gloss:
        case Module(name):
            docstring = importlib.import_module(name).__doc__
            if not docstring:
                raise ValueError(f"`{name}` has no docstring for the layout tree to lift")
            return text.gloss(docstring)
        case Doc(path):
            document = (ROOT / path).read_text()
            # Past the `# Heading`, which names the file rather than describing it.
            body = document.split("\n", 1)[1] if document.startswith("# ") else document
            return text.gloss(body)
        case Words(said):
            return said


def lines() -> list[tuple[str, str]]:
    """Each entry as the tree draws it: the label under its parent, and its gloss.

    An entry whose parent is also an entry is drawn indented under it, by its own name; every
    other one carries its whole path, so a reader can copy any line into a command.
    """
    paths = {entry.path for entry in ENTRIES}
    drawn = []
    for entry in ENTRIES:
        parent = _parent(entry.path)
        depth = 0
        while parent in paths:
            depth += 1
            parent = _parent(parent)
        name = entry.path.rstrip("/").rpartition("/")[2] if depth else entry.path.rstrip("/")
        label = "  " * depth + name + ("/" if entry.path.endswith("/") else "")
        drawn.append((label, glossed(entry.gloss) if entry.gloss else ""))
    return drawn


def _parent(path: str) -> str:
    """The directory one level up, as an entry path spells it."""
    head = path.rstrip("/").rpartition("/")[0]
    return f"{head}/" if head else ""


def generate() -> str:
    """The tree as the cog block splices it, fence and all."""
    drawn = lines()
    width = max(len(label) for label, _ in drawn) + 2
    tree = [f"{label:<{width}}{gloss}".rstrip() for label, gloss in drawn]
    return "```\n" + "\n".join(tree) + "\n```"


def main() -> None:
    print(generate())


if __name__ == "__main__":
    main()
