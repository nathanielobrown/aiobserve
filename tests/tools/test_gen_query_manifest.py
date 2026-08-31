"""What the Rust side reads instead of importing the manifest: every query's scope and bindings.

`rust/metadata/query_manifest.json` is the generation bridge — Python owns
`analyze/manifest.py`, and the Rust runner compiles this file in rather than keeping a second
copy of it (`plans/rust-prototype/full-port.md`). A drifted copy is a runner binding
yesterday's defaults while every Python leaf stays green, so the file is regenerated here and
compared byte for byte, then read back against the live manifest as data.
"""

import datetime as dt
import json
from pathlib import Path

from hyphae.analyze.manifest import QUERIES
from hyphae.analyze.queries import REQUIRED, ParamValue
from tools import gen_query_manifest


def spelled(default: ParamValue) -> str | int | None:
    """A default as JSON carries it, said here rather than imported: a date is its ISO form."""
    return default.isoformat() if isinstance(default, dt.date) else default


def test_the_checked_in_manifest_is_what_the_generator_writes(tmp_path: Path) -> None:
    """The tracked JSON is byte for byte what `QUERIES` generates today."""
    # If the generator writes the file fresh into a scratch directory...
    fresh = tmp_path / "query_manifest.json"
    gen_query_manifest.write(fresh)
    # ...then the tracked copy the Rust side compiles in is the same bytes, or it has drifted.
    assert fresh.read_bytes() == gen_query_manifest.MANIFEST.read_bytes(), (
        f"`{gen_query_manifest.MANIFEST.name}` has drifted from `analyze/manifest.py` —"
        f" regenerate it with `{gen_query_manifest.COMMAND}`"
    )


def test_every_query_reaches_the_file_with_its_scope_and_its_parameters() -> None:
    """Every query is in the file, in manifest order, with what it binds and what it defaults to.

    The whole manifest rather than a sample: a bridge that dropped a query would leave the
    other side unable to bind it at all, and one that dropped a parameter would bind it wrong.
    """
    # If the file is read back as data...
    written = json.loads(gen_query_manifest.MANIFEST.read_text())
    # ...then it is every query the manifest declares, in the order it declares them...
    assert list(written) == list(QUERIES)
    # ...each with its scope and every parameter it takes, said the way the other side reads it.
    assert written == {
        name: {
            "scope": query.scope.value,
            "params": {
                param_name: {
                    "type": param.type.value,
                    "required": param.default is REQUIRED,
                    "default": None if param.default is REQUIRED else spelled(param.default),
                }
                for param_name, param in query.params.items()
            },
        }
        for name, query in QUERIES.items()
    }


def test_a_required_parameter_is_told_apart_from_one_defaulted_to_null() -> None:
    """`required` is what carries the distinction JSON's `null` cannot.

    NULL is a real default — `command_failures` unbound surveys every command — so a parameter
    written with `"default": null` alone would read as either that or as a choice the caller
    has to make. Both kinds are in the library, so the flag is what tells them apart.
    """
    # If every parameter in the file is gathered by how it is written...
    written = json.loads(gen_query_manifest.MANIFEST.read_text())
    params = [param for query in written.values() for param in query["params"].values()]
    nulled = [param for param in params if param["default"] is None]
    # ...then the ones with no default at all are marked as such, and there are both kinds:
    # the library holds parameters a reader may leave blank and parameters they may not.
    kinds = {param["required"] for param in nulled}
    assert kinds == {True, False}, "a NULL default and a required parameter read the same"
    # And the two are the manifest's own reading of the same parameters.
    assert [param["required"] for param in nulled] == [
        param.default is REQUIRED
        for query in QUERIES.values()
        for param in query.params.values()
        if param.default is REQUIRED or param.default is None
    ]
