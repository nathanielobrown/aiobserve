"""The header above a node: the store's own facts about it, labelled in words.

A fact is printed under the word `view/labels.py` gives its column, so a page and a log column
call one store column the same thing. The lists among them — the skills a session used, the PRs
it touched — say when they cut what they hold, and a PR is a link only where a browser can
follow one.
"""

import re
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view import app as view_app
from aiobserve.view import columns as view_columns
from aiobserve.view import format as fmt
from aiobserve.view.app import build_app
from aiobserve.view.labels import LABELS
from aiobserve.view.templating import TEMPLATES
from tests.conftest import (
    MAIN,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import (
    Planter,
    Statement,
    fields,
    inside,
    money,
    one,
    reads,
)


def test_the_session_header_holds_what_the_store_says_about_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """The session page's header is that session's own rollup and identity."""
    page = client.get(f"/session/{SPINE}").text
    pane = fields(page, "data-body", "session")
    title, turns, agent_runs, cost = one(
        store,
        "SELECT s.title, r.turns, r.agent_runs, r.cost_usd FROM sessions s"
        " JOIN session_rollups r ON r.session_id = s.id WHERE s.id = ?",
        [SPINE],
    )
    # The title heads the pane as what the node is called — a derivation, which on a described
    # session is the pass's words instead — and sits under it again as the store's own column,
    # which is the one place a reader learns what the session was recorded as.
    assert (pane["title"], pane["recorded_title"]) == (title, title)
    assert pane["turns"] == str(turns)
    assert pane["agent_runs"] == str(agent_runs)
    assert pane["cost_usd"] == money(cost)


def test_a_header_labels_its_facts_in_words(client: TestClient) -> None:
    """A header names each fact the way a reader says it, with the store's column beside it.

    Both halves, because they answer to different readers: the `<dt>` is what a person reads
    and the `data-field` is what the rest of this suite reads a header by, so neither can drift
    into the other. `wall_ms` is the case that forces the split — the value under it already
    prints as `24h 25m`, and a label ending in `_ms` contradicts the cell it stands over.
    """
    # The formatter is free to put the two tags on lines of their own, so the pattern reads
    # across whatever it left between them; what it may not do is pair a label with the value
    # of some other fact, which is why nothing but whitespace is allowed there.
    labelled = dict(
        re.findall(
            r"<dt>([^<]*)</dt>\s*<dd data-field=\"([^\"]+)\"",
            client.get(f"/session/{SPINE}").text,
        )
    )
    assert labelled["Wall time"] == "wall_ms"
    assert labelled["Session"] == "session_id"
    assert labelled["Cost"] == "cost_usd"
    # And a label reads off its own value with a space between the two — `Cost $1.48`, never
    # `Cost$1.48`. The formatter stands the two tags on lines of their own, where before the
    # stylesheet was the only thing holding them apart.
    page = client.get(f"/session/{SPINE}").text
    shown = fields(page, "data-body", "session")
    said = reads(page, "data-body", "session")
    assert f"Cost {shown['cost_usd']}" in said
    assert f"Wall time {shown['wall_ms']}" in said


def test_every_fact_a_header_asks_for_has_a_label() -> None:
    """The label registry is closed over the templates: no extra entries, and no missing ones.

    A header field with no label would reach a reader as a column name, which is the thing
    `LABELS` exists to stop, and an entry nothing asks for is a word nobody sees. Read off the
    templates, the panes and the log's column table rather than listed here, so a fact added
    to any of them lands in this check. The panes are a source because a previewed value is
    labelled by the name the route passed it under, which no template holds; the column table
    is one because a children log heads itself from a variable, which no regex over a template
    can see. Every module of the view package is read rather than `app.py` alone, so a pane
    that moves to a module of its own keeps its previews in the check.
    """
    asked = {
        name
        for path in TEMPLATES.rglob("*.html")
        for name in re.findall(r"(?:parts\.fact|label)\('([a-z_]+)'", path.read_text())
    }
    previewed = {
        name
        for path in Path(view_app.__file__).parent.glob("*.py")
        for name in re.findall(r'detail_of\(\s*"([a-z_]+)"', path.read_text())
    }
    headed = {column.field for shape in view_columns.COLUMNS.values() for column in shape}
    assert asked | previewed | headed == set(LABELS)


def test_a_column_that_prints_a_length_says_so_in_its_heading() -> None:
    """A column of bare numbers has to name its unit, or the number is unreadable.

    A children log prints lengths where the page under it prints the values — `text_chars` is
    how much the model said, `result_chars` how much a tool answered. Heading either with the
    word the pane gives the value itself leaves a reader deciding whether the column counts
    characters, calls or answers. Read off the column table, so a length column added to any
    shape lands in this check.
    """
    lengths = {
        column.field
        for shape in view_columns.COLUMNS.values()
        for column in shape
        if column.field.endswith("_chars")
    }
    assert lengths, "the log heads no length column, so this contract has no subject"
    for field in lengths:
        assert "chars" in LABELS[field].lower(), field


def test_every_number_a_header_prints_carries_its_separators(plant: Planter) -> None:
    """A header's counts go through the same formatter every count on a page does.

    Both panes, because they show the same rollup of two different threads: a session's, and
    one run's. Planted, because the busiest thread the corpus records made a handful of
    calls — under a thousand a formatted count and a bare one are the same string. The clones
    are of recorded rows, so what a header counts stays the `live_*` population it counts today.
    """
    over = 1_000

    def cloned(table: str, source: str) -> Statement:
        # One recorded row of that thread, cloned past the point the two spellings diverge.
        return (
            f"INSERT INTO {table} (SELECT t.* REPLACE (t.id || '-planted-' || i AS id)"
            f" FROM {table} t, range(1, ?) r(i) WHERE t.session_id = ? AND t.id ="
            f" (SELECT min(id) FROM {table} WHERE session_id = ? AND source = ?))",
            [over + 1, SPINE, SPINE, source],
        )

    path = plant(
        *(
            cloned(table, source)
            for table in ("turns", "api_calls", "tool_calls")
            for source in (MAIN, SPINE_RUN)
        )
    )
    with TestClient(build_app(path)) as planted:
        session = fields(planted.get(f"/session/{SPINE}").text, "data-body", "session")
        run = fields(planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text, "data-body", "run")
    # Every number either header prints is grouped in threes or the dash a NULL prints...
    counted = ("turns", "api_calls", "tool_calls", "tool_errors", "compactions", "output_tokens")
    for header, name in ((session, "session"), (run, "run")):
        for field in (*counted, "unpriced_api_calls"):
            assert re.fullmatch(r"\d{1,3}(,\d{3})*|—", header[field]), (name, field, header[field])
        # ...and the plant pushed three of them past the point where that is a claim.
        assert all("," in header[field] for field in counted[:3]), name


def test_a_headers_list_marks_a_member_it_cut_and_links_only_a_whole_url(
    plant: Planter,
) -> None:
    """The pane's two lists cut every member, and a member cut in silence is a value misread.

    A skill name is prose a reader compares; a PR URL is the one transcript value that reaches
    an `href`, and half a URL in an `href` is a link somewhere else — so a cut one is shown
    for what it is and followed by nothing. Both values are planted and invented: redaction
    flattened the recorded PR links, and no recorded skill has a name near the width.
    """
    width = queries.HEADER_ITEM_CHARS
    skill = "planted-skill-" + "s" * width
    fits = "https://example.test/org/repo/pull/1"
    over = f"{fits}?planted={'q' * width}"
    path = plant(
        ("UPDATE api_calls SET attribution_skill = ? WHERE session_id = ?", [skill, SPINE]),
        (
            "INSERT INTO pr_links VALUES"
            " (?, 900003, 3, ?, 'planted/repo', '2026-01-01T00:00:00Z'),"
            " (?, 900004, 4, ?, 'planted/repo', '2026-01-01T00:00:00Z')",
            [SPINE, fits, SPINE, over],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # The skill's name ends at the width with the mark that says it went on...
    assert fields(page, "data-body", "session")["skills"] == skill[:width] + fmt.ELLIPSIS
    # ...the URL that fit is a link the reader can follow...
    assert inside(page, "data-pr", fits, "href") == [fits]
    # ...and the one that did not is marked the same way and reaches no href at all.
    assert inside(page, "data-pr", over[:width] + fmt.ELLIPSIS, "href") == []
