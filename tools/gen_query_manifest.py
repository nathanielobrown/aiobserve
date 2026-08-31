"""`rust/metadata/query_manifest.json`: what each library query takes, as data another tier reads.

Run by hand — `uv run python -m tools.gen_query_manifest` — and read back by
`tests/tools/test_gen_query_manifest.py`, which regenerates into a scratch directory and
compares bytes. Not a cog block like most of `tools/`: what it writes is a file another
runtime compiles in, not a table spliced into a document.

Python stays the manifest's one owner and the Rust runner binds from this file, so a query's
scope and its defaults have a single derivation across both implementations
(`plans/rust-prototype/full-port.md`). A parameter is written with `required` beside its
default because NULL is a real default — `command_failures` unbound surveys every command —
and absence cannot stand in for a choice the caller has to make.
"""

import datetime as dt
import json
from pathlib import Path

from hyphae.analyze.manifest import QUERIES
from hyphae.analyze.queries import NoDefault, Param, ParamValue, Query

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "rust" / "metadata" / "query_manifest.json"
# What a failing staleness check tells the reader to run.
COMMAND = "uv run python -m tools.gen_query_manifest"


def written(default: ParamValue) -> str | int | None:
    """One default in the vocabulary JSON has: a date as the ISO string a `--param` spells.

    Anything else crashes rather than serializing to whatever `json` makes of it — a default
    the other side cannot bind is a bridge that reads as complete and is not.
    """
    if isinstance(default, dt.date):
        return default.isoformat()
    if default is None or isinstance(default, str | int):
        return default
    raise TypeError(f"a `{type(default).__name__}` default has no spelling in the bridge")


def param(declared: Param) -> dict[str, str | int | bool | None]:
    """One parameter: what it binds as, whether the caller has to name it, and its default."""
    default = declared.default
    if isinstance(default, NoDefault):
        return {"type": declared.type.value, "required": True, "default": None}
    return {"type": declared.type.value, "required": False, "default": written(default)}


def entry(query: Query) -> dict[str, object]:
    """One query: what it is asking about, and every parameter its file declares."""
    return {
        "scope": query.scope.value,
        "params": {name: param(declared) for name, declared in query.params.items()},
    }


def generate() -> str:
    """The whole file, in manifest order — the order `analyze/manifest.py` declares them in."""
    listed = {name: entry(query) for name, query in QUERIES.items()}
    return json.dumps(listed, indent=2) + "\n"


def write(path: Path) -> None:
    """Write the file, creating its directory if the tree does not hold one yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate())


def main() -> None:
    write(MANIFEST)
    print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(QUERIES)} queries")


if __name__ == "__main__":
    main()
