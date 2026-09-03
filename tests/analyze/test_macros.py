"""What the shared macros answer, run against a real DuckDB rather than read as text.

The bounding macros are weighed in `tests/view/test_bounds.py`, which owns the cut protocol.
This module holds the ones whose contract is a value: what they answer for a key the table has,
for one it does not, and for NULL.
"""

import duckdb
import pytest

from hyphae.analyze import macros
from hyphae.extract.pricing import MODELS, SYNTHETIC_MODEL

# Every key the table sizes, then the three the macro must answer NULL for: the placeholder,
# which states no window; a model no table ever named; and NULL itself, which is what a thread
# with no answered call passes (`view_compactions.sql`).
SIZED = tuple(model for model, spec in MODELS.items() if spec.context_window is not None)
UNSIZED = (SYNTHETIC_MODEL, "claude-from-a-version-we-have-not-seen", None)


@pytest.fixture(scope="module")
def installed() -> duckdb.DuckDBPyConnection:
    """One in-memory connection carrying the macros, shared by every leaf here."""
    connection = duckdb.connect(":memory:")
    macros.install(connection)
    return connection


def test_context_window_answers_the_table_for_a_model_it_sizes(
    installed: duckdb.DuckDBPyConnection,
) -> None:
    """A model the price table sizes gets that window back, as a number a bar divides by."""
    # Every sized model in one statement, so a spelling the macro body mangles fails by name...
    answered = installed.execute(
        "SELECT model, context_window(model), typeof(context_window(model))"
        " FROM (SELECT unnest($models) AS model)",
        {"models": list(SIZED)},
    ).fetchall()
    assert [(model, window) for model, window, _ in answered] == [
        (model, MODELS[model].context_window) for model in SIZED
    ]
    # ...and the type is one the arithmetic downstream can divide, not a list or a struct.
    assert {kind for _, _, kind in answered} == {"INTEGER"}


@pytest.mark.parametrize("model", UNSIZED, ids=["placeholder", "unknown", "null"])
def test_context_window_answers_null_for_a_model_it_cannot_size(
    installed: duckdb.DuckDBPyConnection, model: str | None
) -> None:
    """A model with no window — placeholder, unknown, or none at all — is a bar left undrawn.

    The viewer reads a NULL window as "draw no bar" (`view_compactions.sql`), so a macro that
    answered a default here would invent a scale for a thread we cannot size.
    """
    answered = installed.execute("SELECT context_window($model)", {"model": model}).fetchone()
    assert answered == (None,)
