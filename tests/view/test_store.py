"""What one read binds: the keys it is about, the surface's widths, and the reader's sizes.

`store.bound` is the seam every page reads through, so these leaves are about the mapping it
hands back rather than about rows: the same keys and values the routes used to spell by hand,
in the same order, because that mapping is what the citation under the page quotes
(`view/citation.py`). Nothing here opens a connection — a read short of a parameter is refused
before the store is touched, which is the whole reason the function exists.

The shipped query files are the world here: the parameters a page declares are read off its
own statement (`analyze/manifest.py:describe`). One leaf plants a `.sql` of its own, and says
so.
"""

from pathlib import Path

import pytest

from hyphae.analyze import queries
from hyphae.view import bounds
from hyphae.view.store import Page, bound
from tests.conftest import MAIN, SLASH_TURN, SPINE


def test_a_filled_read_binds_what_the_route_used_to_spell_by_hand() -> None:
    """The turn header, bound from its keys, its surface and the reader's `?detail=`.

    Every other leaf here is downstream of this one: what the page runs at, what the footer
    quotes and what a reader pastes back into `hp query` are all this mapping. The order is
    part of it — a citation is written key by key in the order the mapping holds them — so the
    three groups come back in the order a read spells them: keys, widths, sizes.
    """
    hand = {
        "session_id": SPINE,
        "source": MAIN,
        "turn_id": SLASH_TURN,
        "head_chars": queries.HEADER_CHARS,
        "detail_chars": bounds.DETAIL.default,
    }
    filled = bound(
        Page.TURN_HEADER,
        bounds.HEADER_WIDTHS,
        {"detail_chars": bounds.DETAIL.default},
        session_id=SPINE,
        source=MAIN,
        turn_id=SLASH_TURN,
    )
    assert filled == hand
    assert list(filled) == list(hand)


def test_a_parameter_neither_the_keys_nor_the_surface_carries_is_refused_by_name() -> None:
    """A read whose surface is short a width crashes, naming the page, the parameter and it.

    DuckDB refuses the same read — a named parameter it never got is an error there too — but
    it names neither the surface nor which of the two halves should have carried the number,
    and it only says so once a connection has been opened and the statement handed over.

    The session header cuts three strings and caps a list; the popover declares the two it
    prints and no `head_chars`, so binding one against the other is the mistake this catches:
    a surface used for a read that is not its own.
    """
    with pytest.raises(ValueError, match=r"view_session_header.*head_chars.*Popover"):
        bound(Page.SESSION_HEADER, bounds.POPOVER_WIDTHS, session_id=SPINE)


def test_a_key_that_is_also_a_width_is_refused_rather_than_overriding_it() -> None:
    """A read cannot bind a width of its own: a second width is a second surface.

    Silent overriding is what the profiles exist to end — a page that passed its own
    `head_chars` would run at a width nothing in `bounds.py` declares, and the citation under
    it would be the only place that number appears. The refusal names the field, so the fix is
    to declare the surface rather than to keep the argument.
    """
    with pytest.raises(ValueError, match=r"head_chars.*Header"):
        bound(
            Page.TURN_HEADER,
            bounds.HEADER_WIDTHS,
            {"detail_chars": bounds.DETAIL.default},
            session_id=SPINE,
            source=MAIN,
            turn_id=SLASH_TURN,
            head_chars=10,
        )


def test_a_key_the_statement_does_not_bind_is_refused_before_a_connection_is_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A key no statement binds is a mistake at the call, not a binding error at the store.

    The query is planted — invented, and deliberately a shape no shipped file has: one
    parameter, and that one a width — because what this holds is the arm that reads the
    statement rather than any page. The records browser's own surface fills it, and the key
    beside it belongs to no column the statement mentions.

    No `duckdb` import and no store path is in reach of this test, which is the claim: the
    refusal happens while the mapping is being built.
    """
    monkeypatch.setattr(queries, "QUERY_DIR", tmp_path)
    (tmp_path / f"{Page.RECORDS}.sql").write_text("SELECT $preview_chars AS preview_chars")
    with pytest.raises(ValueError, match=r"view_records.*session_id"):
        bound(Page.RECORDS, bounds.RECORDS_WIDTHS, session_id=SPINE)
