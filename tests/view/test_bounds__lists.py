"""What the list pages cost: one row of a session, a project or a failed tool call.

The same arithmetic as `test_bounds__node.py` over the pages a corpus grows rather than a
session: chrome measured once, then a row priced against what `tests/view/budgets.py` says the
widest one can hold, times the ceiling the page admits.
"""

import re
from html import unescape
from itertools import pairwise
from urllib.parse import quote

import duckdb
import pytest
from fastapi.testclient import TestClient

from hyphae.analyze import queries
from hyphae.analyze.queries import ParamValue
from hyphae.enrich.taxonomy import Category, Outcome
from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.store import TURN_CURSOR, Page, cursorless_rows
from hyphae.view.text.format import ELLIPSIS
from tests.conftest import (
    FORK_ORIGIN,
    RESUME,
)
from tests.view.budgets import (
    DESCRIBED_AT_EVERY_CAP,
    MEASURED_ERRORS_CHROME,
    MEASURED_LIST_CHROME,
    MEASURED_PROJECTS_CHROME,
    PAGE_BYTES,
    fits,
    worst_error_row_bytes,
    worst_project_row_bytes,
    worst_session_row_bytes,
)
from tests.view.conftest import (
    Planter,
    fields,
    inside,
    one,
    suggestions,
    values,
)


def test_a_session_list_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    enriched_plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A list row and the chrome around it weigh no more than the arithmetic gives them.

    The list is the page a corpus grows: every string in a row is one a transcript wrote, and
    its skills and the filter box's project suggestions both grow with what the store holds.
    So `&` is planted at every cap — the character that escapes to five bytes — and both halves
    of the ceiling are measured against it: one more row, and the page with no rows at all. The
    described store rather than the bare one, because a row of a store a pass has run over
    carries what the pass said as well, and that is the row the ceiling has to budget.
    """
    # Every string goes in one character past what a row prints, because that character is the
    # whole of how the page knows a value was stopped rather than ended: at the cap exactly,
    # nothing is marked and the row costs less than the arithmetic gives it.
    head = "&" * (queries.LIST_CHARS + 1)
    # Except a project path, which the filter box offers whole or not at all
    # (`view_projects.sql`): the paths that fill the box sit exactly at the width, and every
    # path past those goes one over it, where the row's own mark is. Two digits tell them
    # apart, so both halves of the page are measured against the same plant.
    root = "&" * (queries.LIST_CHARS - 2)
    name = "&" * queries.LIST_ITEM_CHARS
    kind = "&" * queries.TAG_CHARS
    over = queries.LIST_ITEMS + 2
    kinds = queries.LIST_CATEGORIES + 2
    path = enriched_plant(
        # A project path per session, each one longer than the filter box offers, so the box
        # has more suggestions than it shows. The two digits that tell them apart are the only
        # characters on the page that are not an escape...
        (
            "UPDATE sessions SET title = ?, project_dir = ? || printf('%02d', r.n)"
            " || CASE WHEN r.n > ? THEN '&' ELSE '' END"
            " FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM sessions) r"
            " WHERE r.id = sessions.id",
            [head, root, queries.LIST_PROJECTS],
        ),
        # ...and every session runs more skills than a row shows, cloning a live api call rather
        # than inventing one: `live_api_calls` is the population a row's skill list counts.
        (
            "INSERT INTO api_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            " ? || i AS attribution_skill)"
            " FROM (SELECT DISTINCT ON (l.session_id) l.* FROM live_api_calls l) c,"
            " range(1, ?) t(i))",
            [name, over + 1],
        ),
        # ...and every session spawns more kinds of subagent than a row shows. The names have
        # to differ inside the *shown* width, not merely inside the one the query cuts to:
        # the query groups the runs after its cut, and the row cuts a character off that again
        # to make room for the mark. So two digits sit at the end of what a row shows, with one
        # more escape behind them — which puts every name past the cut and still tells them
        # apart, at 19 escapes in every 21 characters.
        (
            "INSERT INTO agent_runs (SELECT r.* REPLACE (s.id AS session_id,"
            " s.id || '-planted-' || i AS id, ? || printf('%02d', i) || '&' AS agent_type)"
            " FROM (SELECT * FROM live_agent_runs ORDER BY session_id, id LIMIT 1) r,"
            " sessions s, range(1, ?) t(i))",
            [name[:-2], over + 1],
        ),
        *DESCRIBED_AT_EVERY_CAP,
        # The pass described every turn as the same kind of work, so the Work cell would show
        # one name; a described turn per session per kind fills it the way a real pass would,
        # cloning a described row rather than inventing one. Categories are cut and grouped
        # like the agent types, so they are told apart the same way.
        (
            "INSERT INTO turn_enrichments (SELECT e.* REPLACE (s.id AS session_id,"
            " s.id || '-planted-' || i AS turn_id, ? || printf('%02d', i) AS category)"
            " FROM (SELECT * FROM turn_enrichments ORDER BY session_id, turn_id LIMIT 1) e,"
            " sessions s, range(1, ?) t(i))",
            [kind[:-2], kinds + 1],
        ),
    )
    (sessions,) = one(store, "SELECT count(*) FROM sessions")
    assert sessions > queries.LIST_PROJECTS, "the fixture corpus no longer fills the filter box"
    with TestClient(build_app(path)) as planted:

        def served(size: int) -> str:
            response = planted.get("/sessions", params={"size": size})
            assert response.status_code == 200, response.text[:200]
            return response.text

        pages = [served(size) for size in range(1, sessions + 1)]
    one_row = pages[0]
    # One more row costs its markup and every head it shows, all `&` — priced at every row the
    # list holds rather than at whichever one lands second, because the ceiling multiplies the
    # dearest row and which session that is depends only on how the list happens to be sorted.
    weights = [len(page.encode()) for page in pages]
    assert max(b - a for a, b in pairwise(weights)) <= worst_session_row_bytes()
    # ...and what the page carries whatever its size fits the allowance the ceiling gives it,
    # with the row the arithmetic counts separately stripped out.
    chrome = re.sub(r"<tr data-session-id=.*?</tr>", "", one_row, flags=re.DOTALL)
    assert not values(chrome, "data-session-id") and 'id="sessions"' in chrome
    assert fits(measured=len(chrome.encode()), budget=MEASURED_LIST_CHROME), len(chrome.encode())
    # The plant reached every cap, which is what makes those two numbers a worst case: each
    # string cut to its head, the skills cut to their first names and saying how many were
    # left, and the filter box offering as many projects as it has room for. Read off the row
    # the budget above is priced at — the dearest one — rather than off whichever sorted first.
    markup = re.findall(r"<tr data-session-id=.*?</tr>", pages[-1], flags=re.DOTALL)
    dearest = max(markup, key=lambda one: len(one.encode()))
    row = fields(dearest, "data-session-id", values(dearest, "data-session-id")[0])
    # Each of the row's own strings cut to its head and marked there, which is what says the
    # cut bit rather than the plant happening to end at the width.
    assert row["title"] == "&" * queries.LIST_CHARS + ELLIPSIS
    assert len(row["project_dir"]) == queries.LIST_CHARS + len(ELLIPSIS)
    assert row["project_dir"].endswith(ELLIPSIS)
    assert row["skills"].count(name + ELLIPSIS) == queries.LIST_ITEMS
    assert row["skills"].endswith("more")
    # The two counted lists reached their own caps, each name cut to the head it is grouped
    # under — the last two characters of one are the digits that tell the plants apart, and
    # the mark behind them is the escape the plant put past the cut.
    assert row["agent_types"].count(name[:-2]) == queries.LIST_ITEMS
    assert row["agent_types"].count(ELLIPSIS) == queries.LIST_ITEMS
    # The kinds of work are the one cut column with no mark, and the plant cannot show why:
    # `$kind_chars` has no character to spare (`view_described_sessions.sql`), so a name
    # arrives at the width whatever was planted behind it and a mark could not fire. What
    # holds the budget is the vocabulary itself — closed, and every member of both of them
    # short of the cut — which is the claim the row above prices at no mark at all.
    assert max(len(member) for member in (*Category, *Outcome)) < queries.TAG_CHARS
    assert row["work"].count(kind[:-2]) == queries.LIST_CATEGORIES
    assert row["agent_types"].endswith("more") and row["work"].endswith("more")
    assert len(suggestions(one_row)) == queries.LIST_PROJECTS
    # And the pass's own line reached the head the list cuts it to, with both tags beside it —
    # the whole description is on the session's page, which is a page ceiling of its own.
    assert row["description"] == "&" * queries.LIST_CHARS + ELLIPSIS
    assert len(row["category"]) == len(row["outcome"]) == queries.TAG_CHARS
    assert "stale" not in row


def test_a_projects_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """The landing page at its ceiling weighs no more than the arithmetic gives it.

    A project path is a directory someone named, so `&` is planted at the cap the page shows —
    the character that escapes to five bytes in a cell and to twelve in the link beside it —
    and the store is filled past the page's own ceiling. That is the page the arithmetic
    bounds and the one no corpus recorded so far comes near: the fixtures hold four projects.
    """
    over = bounds.PROJECTS.ceiling + 20
    # Three digits tell the paths apart inside the head the page shows, so 97 of every 100
    # characters are escapes — and no path is a prefix of another, so none folds into another's
    # row. The sessions are clones of a recorded one rather than invented rows.
    head = "&" * (queries.LIST_CHARS - 3)
    path = plant(
        (
            "INSERT INTO sessions (SELECT s.* REPLACE (s.id || '-planted-' || i AS id,"
            " ? || printf('%03d', i) AS project_dir) FROM (SELECT * FROM sessions LIMIT 1) s,"
            " range(1, ?) t(i))",
            [head, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        response = planted.get("/")
    assert response.status_code == 200, response.text[:200]
    page = response.text
    # A page a reader lands on stays under the ceiling with every path at its cap...
    assert len(response.content) < PAGE_BYTES
    shown = values(page, "data-project")
    assert len(shown) == bounds.PROJECTS.ceiling
    # ...the planted ones at the cap, and the corpus's own short paths beside them. The
    # attribute is read back through the escaping the page wrote it with, which is the point:
    # every character of a planted path is one of the five-byte ones.
    widest = unescape(max(shown, key=len))
    assert len(fields(page, "data-project", widest)["project_dir"]) == queries.LIST_CHARS
    # ...each row linking by the whole path it shows, which is what the encoded head budgets...
    assert inside(page, "data-project", widest, "href") == [
        f"/sessions?sort=started_at&direction=desc&project={quote(widest, safe='')}"
    ]
    # ...and what it left out said rather than dropped. Every planted path is a root of its
    # own, so the store's distinct directories are the rows the page had to choose between.
    with duckdb.connect(str(path), read_only=True) as connection:
        (projects,) = one(
            connection, "SELECT count(DISTINCT coalesce(project_dir, '')) FROM sessions"
        )
    assert values(page, "data-more-projects") == [str(projects - bounds.PROJECTS.ceiling)]
    # What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    # rows the arithmetic counts separately stripped out...
    chrome = re.sub(r"<tr data-project=.*?</tr>", "", page, flags=re.DOTALL)
    assert not values(chrome, "data-project") and 'id="projects"' in chrome
    assert fits(measured=len(chrome.encode()), budget=MEASURED_PROJECTS_CHROME), len(
        chrome.encode()
    )
    # ...and one row costs no more than its markup and the two copies of its path.
    row_bytes = (len(page.encode()) - len(chrome.encode())) / bounds.PROJECTS.ceiling
    assert row_bytes <= worst_project_row_bytes()


def test_an_errors_page_of_nothing_but_escapes_costs_what_the_ceiling_budgets(
    plant: Planter,
) -> None:
    """A session's errors list at its ceiling weighs no more than the arithmetic gives it.

    Nothing about a session caps how often its tools fail, so the store is filled past the
    page's own ceiling and every title planted full of `&` — the character that escapes to
    five bytes. The failures are clones of a recorded tool call rather than invented rows;
    what is planted on each is the flag the store already records on two of them.
    """
    over = bounds.ERRORS.ceiling + 20
    # A title longer than the width a row cuts it to, so the cut bites on every row. The index
    # differs per clone because it is half of what orders the list: a page showing the first
    # `ERRORS` of a partial order is a page that cannot say what it cut.
    title = "&" * (queries.NAV_CHARS + 1)
    path = plant(
        (
            "INSERT INTO tool_calls (SELECT c.* REPLACE (c.id || '-planted-' || i AS id,"
            ' ? AS name, ? AS input, true AS is_error, 9000 + i AS "index")'
            " FROM (SELECT * FROM live_tool_calls WHERE session_id = ? LIMIT 1) c,"
            " range(1, ?) g(i))",
            [title, title, FORK_ORIGIN, over + 1],
        ),
    )
    with TestClient(build_app(path)) as planted:
        response = planted.get(f"/session/{FORK_ORIGIN}/errors")
    assert response.status_code == 200, response.text[:200]
    page = response.text
    # A page a reader jumps to stays under the ceiling with every title at its cap...
    assert len(response.content) < PAGE_BYTES
    shown = values(page, "data-error")
    assert len(shown) == bounds.ERRORS.ceiling
    # ...every one of them a planted failure cut to the width a row reads it at...
    titles = {len(fields(page, "data-error", key)["title"]) for key in shown}
    assert max(titles) == queries.NAV_CHARS + len(ELLIPSIS)
    # ...and what it left out said rather than dropped, against the store's own count.
    with duckdb.connect(str(path), read_only=True) as connection:
        (failures,) = one(
            connection,
            "SELECT count(*) FROM live_tool_calls WHERE session_id = ? AND is_error",
            [FORK_ORIGIN],
        )
    assert values(page, "data-more-errors") == [str(failures - bounds.ERRORS.ceiling)]
    # What the page carries whatever it holds fits the allowance the ceiling gives it, with the
    # rows the arithmetic counts separately stripped out...
    chrome = re.sub(r"<li data-error=.*?</li>", "", page, flags=re.DOTALL)
    assert not values(chrome, "data-error") and 'id="errors"' in chrome
    assert fits(measured=len(chrome.encode()), budget=MEASURED_ERRORS_CHROME), len(chrome.encode())
    # ...and one row costs no more than its markup and the title it carries.
    row_bytes = (len(page.encode()) - len(chrome.encode())) / bounds.ERRORS.ceiling
    assert row_bytes <= worst_error_row_bytes()


def test_the_timeline_rows_no_window_reaches_are_capped_at_what_a_page_budgets(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """The rows that ride the last page outside its window are bounded, not counted afterwards.

    A timeline row with no turn index cannot be windowed, so it arrives on the last page
    whatever `turns` a reader asked for — which is why the arithmetic above budgets
    `bounds.CURSORLESS_TURNS` turn rows on top of the size the route admits. `RESUME` answers turns
    that live in the session it resumed, so every one of its api calls is unattributed and
    its timeline carries exactly this row. The cap is bound down to zero to reach a boundary no
    recorded timeline crosses: more of these rows than the ceiling budgets raises rather than
    riding a page nothing counted them on.
    """
    bound: dict[str, ParamValue] = {"session_id": RESUME, "log_chars": queries.LOG_CHARS}
    rows = cursorless_rows(store, Page.TIMELINE, TURN_CURSOR, bounds.CURSORLESS_TURNS, **bound)
    assert [row["turn_id"] for row in rows] == [queries.UNATTRIBUTED]
    with pytest.raises(ValueError, match="more than 0"):
        cursorless_rows(store, Page.TIMELINE, TURN_CURSOR, 0, **bound)
