"""What a page can weigh. A viewer that renders a whole transcript is a viewer that hangs.

Three mechanisms, checked separately: the queries behind the pages and fragments never select
an unbounded fat column, what they do select is truncated in SQL rather than in the template,
and every page size is a bound parameter whose production default is pinned here. Together
they are what makes the bound hold by construction rather than by the fixture corpus's luck —
a per-value fetch is the one exception, and it is exempt because its unit *is* one value.

These leaves say a served page fits under its ceiling. What it spends to get there is priced
row by row in `test_bounds__node.py` and `test_bounds__lists.py`, and the exemption is held
to its own terms in `test_bounds__values.py`.
"""

import json
import re
from collections.abc import Iterator

import duckdb
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from hyphae.analyze import macros, queries
from hyphae.analyze.manifest import catalog
from hyphae.analyze.queries import VIEW_PREFIX
from hyphae.view import bounds
from hyphae.view.app import build_app
from hyphae.view.citation import QUERY_URL
from hyphae.view.store import Fragment, Page, Value
from tests.conftest import (
    CONFIG_ONLY,
    MAIN,
    OFFLOAD_FILE,
    RESUME,
    RESUME_LONG_RECORD,
)
from tests.view.budgets import (
    ESCAPED_CHAR_BYTES,
    EXACT_PIN,
    EXPANSION_BYTES,
    FAT,
    MARKED_CHAR_BYTES,
    MEASURED_LIST_CHROME,
    NODE_BYTES,
    PAGE_BYTES,
    exact_pins,
    fits,
    worst_errors_page_bytes,
    worst_expansion_bytes,
    worst_node_bytes,
    worst_projects_page_bytes,
    worst_records_page_bytes,
    worst_session_list_bytes,
    worst_session_row_bytes,
)
from tests.view.conftest import (
    Planter,
    block,
    fields,
    inside,
    one,
)

# The pages that carry a footer, and the reader of one citation line: both are the citation
# tier's, and what the production sizes are read off here.
from tests.view.pages.query.test_query import CITING, bound
from tests.view.scenarios import SCENARIOS

# The library described once for the whole module: every leaf below reads what a query binds,
# and a `Query` is derived from its statement rather than looked up (`analyze/manifest.py`).
CATALOG = catalog()

# What a query may wrap a fat column in and still be bounded: a fixed-width prefix of it, a
# count of what it holds, the check that it parses, the window the model it names answers in,
# or one of the library's own cutting macros.
# Anything else puts the whole value on the page. Read at any depth —
# `substr(coalesce(json_extract_string(input, …), …), 1, $n)` is a cut of whatever it wraps, so
# what a bounding call opens is exempt to its close.
BOUNDING = (
    "substr",
    "length",
    "json_valid",
    # A count of a JSON array is a number however long the array is.
    "json_array_length",
    "context_window",
    *macros.BOUNDING,
)


def _named(sql: str) -> Iterator[str]:
    """Every word a statement names outside a bounding call, however deeply they nest."""
    # Whether each open bracket opened a bounding call, and how many of those are still open.
    opened: list[bool] = []
    bounding = 0
    word = ""
    for token in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|\(|\)", sql):
        found = token.group()
        if found == "(":
            opened.append(word in BOUNDING)
            bounding += opened[-1]
        elif found == ")":
            bounding -= opened.pop() if opened else 0
        elif not bounding:
            yield found
        word = found.lower() if found != ")" else ""


def unbounded(sql: str) -> set[str]:
    """The fat columns a statement selects outside a bounding call — what a page can't afford.

    An output name is not a selected column, so `AS` and what follows it comes out first: a
    cut column keeps the name of the column it cuts, and the cut is what the page shows. A
    quoted string is not a column either — `'$.description'` names a key inside a value.
    """
    without_comments = re.sub(r"--[^\n]*", " ", sql)
    without_strings = re.sub(r"'[^']*'", " ", without_comments)
    named = re.sub(r"\bAS\s+[A-Za-z_][A-Za-z0-9_]*", " ", without_strings, flags=re.IGNORECASE)
    return {word for word in _named(named) if word in FAT}


def test_the_fat_column_scan_catches_one() -> None:
    """The scan below is worth its green: it flags a select the pages must not contain.

    The statements are invented — no shipped query selects a fat column whole, which is
    exactly why the instrument needs its own case.
    """
    assert unbounded("SELECT r.raw FROM raw_records r -- text") == {"raw"}
    assert unbounded("SELECT substr(r.raw, 1, 200) AS raw_head FROM raw_records r") == set()
    # A count of a value is a number, and a page can afford any number.
    assert unbounded("SELECT length(r.raw) AS raw_chars FROM raw_records r") == set()
    # A cut column may keep the name of the column it cuts, and the name is not the value...
    assert unbounded("SELECT substr(e.description, 1, 200) AS description FROM turns e") == set()
    # ...but the column under that name still counts.
    assert unbounded("SELECT e.description AS description FROM turns e") == {"description"}
    # A cut of what a call read out of a fat column is a cut, however deep the call nests...
    parsed = "json_extract_string(t.input, '$.file_path')"
    assert (
        unbounded(f"SELECT substr(coalesce({parsed}, t.input), 1, 200) AS head FROM tools t")
        == set()
    )
    # ...and the check that a value parses hands back a flag rather than the value.
    assert unbounded("SELECT json_valid(t.input) AS ok FROM tools t") == set()
    # As does a count of how many items a value holds, whatever each of them weighs.
    assert unbounded("SELECT json_array_length(t.input, '$.todos') AS n FROM tools t") == set()
    # The window lookup is the same kind of read: a model goes in and a number comes back.
    assert unbounded("SELECT context_window(c.model) AS window FROM api_calls c") == set()
    assert unbounded("SELECT c.model AS model FROM api_calls c") == {"model"}
    # A key inside a JSON path is a string, not the column that happens to share its name...
    assert (
        unbounded("SELECT substr(json_extract_string(t.input, '$.description'), 1, 9) AS a FROM t")
        == set()
    )
    # ...while a fat column read by a call that is not a cut is the whole value on the page.
    assert unbounded(f"SELECT {parsed} AS path FROM tools t") == {"input"}
    assert unbounded("SELECT coalesce(substr(t.input, 1, 9), t.result) AS head FROM tools t") == {
        "result"
    }


def test_every_macro_the_scan_trusts_cuts_the_value_it_reads() -> None:
    """The scan cannot see through a macro call, so what it trusts by name is checked by body.

    Without this the trust is a list: a macro that stopped cutting would go on being read as
    bounding, and every query calling it would keep its green while serving whole values.
    The signature comes off first — a parameter named `input` is a name, not a column read.

    This says a cut is *there*, not that it is the right one: a body cutting at ten thousand
    times the width it was asked for still passes here. The width is the leaf below.
    """
    for name, statement in macros.BOUNDING.items():
        _, cut_at, body = statement.partition(") AS")
        assert cut_at, name
        assert unbounded(body) == set(), name


# What `tool_fields` extracts, read off the macro's own body rather than listed here: `path`
# comes from `file_path` and `todos` answers a number, so the leaf below asks for those two by
# name and feeds every other member a saturating value under its own key.
_FIELD_KEYS = tuple(
    key
    for key in re.findall(r"'(\w+)':", macros.BOUNDING["tool_fields"])
    if key not in ("path", "todos")
)


def test_every_macro_the_scan_trusts_answers_one_character_past_the_width() -> None:
    """Each bounding macro is run at three widths and asked how much it gives back.

    The scan's trust is a bound; this is the protocol on top of it (`view/text/format.py:cut`
    marks a value that came back longer than the width, so a macro that saturates *under* the
    width serves a silently truncated value, and one that saturates over it serves a fat
    column). Every arm gets a value far past the widest width, so each answer is a saturation
    rather than a whole value that happened to fit.

    The struct's keys are read off the macro's own body rather than listed again: the leaf
    is that *every* member cuts, so a member added without a cut has to fail here.

    The paths are invented: the shape — inside the project, outside it, no project at all —
    is the whole point, and no recorded session carries all three at these lengths.
    """
    connection = duckdb.connect(":memory:")
    macros.install(connection)
    project = "/Users/planted/repos/hyphae"
    inside = json.dumps({"file_path": f"{project}/src/{'v' * 400}.py"})
    outside = json.dumps({"file_path": f"/opt/homebrew/{'v' * 400}.py"})

    def answer(expression: str, *params: object) -> str:
        return connection.execute(f"SELECT {expression}", list(params)).fetchall()[0][0]

    for chars in (10, 60, 300):
        # The protocol itself, which every macro below and every bounded column is written in.
        assert len(answer("cut(?, ?)", "v" * 400, chars)) == chars + 1
        # A field read straight.
        assert len(answer("tool_asked(?, 'file_path', ?)", inside, chars)) == chars + 1
        # The relativized path is the arm that spends width on a prefix it then throws away:
        # what comes back is the tail, and it is as long as any other arm's.
        relative = answer("tool_path(?, ?, ?)", inside, project, chars)
        assert len(relative) == chars + 1
        assert relative.startswith("src/")
        # A path the project does not contain, and a session that has no project directory,
        # both take the absolute arm — still at the width, still marked.
        assert len(answer("tool_path(?, ?, ?)", outside, project, chars)) == chars + 1
        assert len(answer("tool_path(?, ?, ?)", inside, None, chars)) == chars + 1
        # And the struct the tool formatters read: every string member of it is a cut of its
        # own, so one member left whole would serve a fat column through a bounded-looking
        # call. Asked with a saturating value under every name it extracts.
        fat = "f" * 400
        fields = json.dumps(dict.fromkeys(_FIELD_KEYS, fat) | {"file_path": f"{project}/{fat}"})
        answered = connection.execute(
            "SELECT tool_fields(?, ?, ?, ?)", [fields, project, fat, chars]
        ).fetchall()[0][0]
        # `todos` is a count and answers a number, which is why it is asked for by name here.
        assert sorted(answered) == sorted([*_FIELD_KEYS, "path", "todos"]), answered
        for member, value in answered.items():
            if member != "todos":
                assert len(value) == chars + 1, member


@pytest.mark.parametrize("name", sorted(Page) + sorted(Fragment))
def test_no_page_or_fragment_query_selects_a_fat_column_whole(name: str) -> None:
    """Every query behind a page or a fragment is bounded in SQL, however large the record."""
    assert unbounded(queries.load(name)) == set()


@pytest.mark.parametrize("value", sorted(Value))
def test_a_per_value_query_returns_the_one_value_it_is_named_for(value: Value) -> None:
    """The per-value queries are the exception, and they are the exception by declaration.

    They select a fat column whole — that is what they are for. What keeps the bound is that
    the unit is one row of one value, so the fetch tops out at the largest value in the store
    rather than at a page's worth of them. Rendering is the other half of that promise, and
    the planted leaf below holds it: what a fragment serves stays proportional to what the
    store holds, however the value nests.
    """
    assert unbounded(queries.load(value)) != set()


def test_every_viewer_query_is_declared_as_a_page_a_fragment_or_a_value() -> None:
    """A viewer query lands in one of the three sets, so the scans above cannot miss it.

    Without this, a query shipped under `view_` but named in no enum is scanned by nothing
    and can select a fat column onto a page with the whole tier still green.
    """
    declared = set(Page) | set(Fragment) | set(Value)
    # Every query the viewer owns is scanned by one of the leaves above...
    assert {name for name in CATALOG if name.startswith(VIEW_PREFIX)} <= declared
    # ...and every name declared is a query that ships, timelines shared with the runner too.
    assert declared <= set(CATALOG)


def ran_at(client: TestClient) -> dict[str, dict[str, set[int]]]:
    """What every query ran at on the pages that cite it: query, parameter, values seen.

    A set rather than a value, because a size belongs to the surface: one parameter runs at
    two widths when two surfaces print it differently, and both are production. Read off the
    citation line each footer carries, which is the page saying what it bound.
    """
    sizes: dict[str, dict[str, set[int]]] = {}
    for path in CITING:
        for name, line in fields(client.get(path).text, "id", "citation").items():
            for parameter, value in bound(line).items():
                if re.fullmatch(r"-?\d+", value):
                    sizes.setdefault(name, {}).setdefault(parameter, set()).add(int(value))
    return sizes


def test_the_pages_run_at_the_production_sizes(client: TestClient) -> None:
    """The page sizes the payload bound is computed from are the ones production runs.

    Read off what the pages cited, not off the manifest: no `view_` query declares a default
    (`analyze/manifest.py`), and back when they did, two of the numbers pinned here were numbers
    no page ever ran — `chip_chars` was declared 60 while the runs log ran it at 300.

    Every other leaf in this file binds fixture-sized values, so without this pin the whole
    section would pass against any size at all — a `page_records` of 5,000 would break the
    bound in production while CI stayed green.

    Where a surface declares its widths (`view/bounds.py`), the number is read off the profile
    and the profile is pinned to its literals below: the first half says the pages run at the
    width their surface names, the second says which width that is. Read off the profile alone,
    the two would be one assertion comparing a number with itself.
    """
    ran = ran_at(client)
    assert ran["view_records"]["page_records"] == {100}
    assert ran["view_records"]["preview_chars"] == {bounds.RECORDS_WIDTHS.preview_chars}
    assert bounds.Records(preview_chars=160) == bounds.RECORDS_WIDTHS
    assert ran["view_offload"]["chunk_chars"] == {50_000}
    # How much of a title a row of the NavTree shows. Wide enough that a draggable tree has
    # something to show when a reader widens it — the cut is what a row can say, and CSS
    # decides how much of it fits. Every level cuts to the same width, whatever kind of child
    # it holds.
    for level in ("view_nav_tree_turns", "view_nav_tree_calls", "view_nav_tree_tools"):
        assert ran[level]["nav_chars"] == {bounds.NAV_TREE_WIDTHS.nav_chars}, level
    # And how much of each string a row of the pane's children log shows, with the page it is
    # read in. Wider than a NavTree row: a log row is a line of a table, with room for the first
    # words of a prompt beside the numbers.
    assert ran["view_turn_calls"]["log_chars"] == {bounds.LOG_WIDTHS.log_chars}
    assert ran["view_call_tools"]["log_chars"] == {bounds.LOG_WIDTHS.log_chars}
    assert ran["view_turn_calls"]["page_calls"] == {queries.LOG_ROWS}
    assert ran["view_call_tools"]["page_tools"] == {queries.LOG_ROWS}
    assert queries.LOG_ROWS == 100
    # A node header cuts every string it carries to a head, and the one fat value its pane
    # previews to a detail — the four kinds that have fields of their own take the same two.
    for header in ("view_turn_header", "view_call_header", "view_tool_header", "view_run_header"):
        assert ran[header]["head_chars"] == {bounds.HEADER_WIDTHS.head_chars}, header
        assert ran[header]["detail_chars"] == {4_000}, header
    # The session header is the widest of the panes: two of its columns are lists that grow
    # with the session, so it cuts the members and caps how many it shows.
    assert ran["view_session_header"]["head_chars"] == {bounds.HEADER_WIDTHS.head_chars}
    assert ran["view_session_header"]["item_chars"] == {bounds.HEADER_WIDTHS.item_chars}
    assert ran["view_session_header"]["head_items"] == {bounds.HEADER_WIDTHS.head_items}
    # And what the header surface declares those three at, plus the width it cuts a
    # compaction's trigger to where the compaction is the node the pane is about.
    assert (
        bounds.Header(head_chars=100, item_chars=60, head_items=5, chip_chars=100)
        == bounds.HEADER_WIDTHS
    )
    # How much of a run row's and a compaction row's three columns the row that prints them
    # shows. One parameter at two widths, which is what a single declared default could never
    # be: the runs log gives a chip a log row's width, and the NavTree gives it a row's.
    assert ran["view_runs"]["chip_chars"] == {bounds.LOG_WIDTHS.chip_chars}
    assert ran["view_compactions"]["chip_chars"] == {bounds.NAV_TREE_WIDTHS.chip_chars}
    # And what those two surfaces declare.
    assert bounds.NavTree(nav_chars=110, chip_chars=110, log_chars=110) == bounds.NAV_TREE_WIDTHS
    assert bounds.Log(log_chars=300, chip_chars=300) == bounds.LOG_WIDTHS
    # A thread's timeline is the one query two surfaces read — the NavTree places the thread's
    # buckets from it at a row's width, the pane's children log lists the same turns at a log's
    # — and the footer keys a citation by query name, so a page quotes whichever ran last. That
    # is the NavTree's, which is why this reads 110 and not 300.
    assert ran["session_timeline"]["log_chars"] == {bounds.NAV_TREE_WIDTHS.log_chars}
    # The list's rows drop the agent types a session spawned, but the query behind them still
    # gathers the names, so a member is cut where the list cuts a skill name.
    assert ran["view_sessions"]["item_chars"] == {bounds.LIST_WIDTHS.item_chars}
    # And what the list surface declares: the row, the two lists a described row adds, the box.
    assert (
        bounds.SessionList(
            head_chars=100,
            item_chars=20,
            head_items=4,
            tag_chars=20,
            kind_chars=20,
            head_kinds=3,
            head_projects=10,
        )
        == bounds.LIST_WIDTHS
    )
    # And the landing page, whose row shows a path at its own head and links by the whole one.
    # The two windows it counts a project in are not sizes, and `tests/view/pages/projects/`
    # pins those against what the page cites.
    assert ran["view_project_rollups"]["head_chars"] == {bounds.PROJECTS_WIDTHS.head_chars}
    assert ran["view_project_rollups"]["projects"] == {bounds.PROJECTS_WIDTHS.projects}
    assert (
        bounds.Projects(recent_days=7, window_days=30, head_chars=100, projects=100)
        == bounds.PROJECTS_WIDTHS
    )
    # And the errors list, bound the same way — a session can fail arbitrarily many calls —
    # and titled at a NavTree row's width, because each of its rows leads to a node.
    assert ran["view_session_errors"]["nav_chars"] == {bounds.ERRORS_WIDTHS.nav_chars}
    assert ran["view_session_errors"]["errors"] == {bounds.ERRORS_WIDTHS.errors}
    assert bounds.Errors(nav_chars=110, errors=100) == bounds.ERRORS_WIDTHS
    # The enrichment block a node page fetches is the one surface no footer quotes — a fragment
    # carries none — so its widths are pinned on the profile alone. The taxonomy is closed and
    # its longest member is nine characters, so the tag cut bounds a hand-edited row.
    assert (
        bounds.Enrichment(
            description_chars=200, tag_chars=20, head_chars=bounds.HEADER_WIDTHS.head_chars
        )
        == bounds.ENRICHMENT_WIDTHS
    )


def test_no_viewer_query_declares_a_default() -> None:
    """A `view_` parameter is required, so the surface that prints the value states its width.

    The viewer never reads a default — every page composes its own bindings, and an unbound
    parameter is a DuckDB error rather than a fallback — so a default here is a number nothing
    runs and nothing checks, free to say 60 while three surfaces run 100, 110 and 300. Dropping
    them puts every width in one place: the constant beside its ceiling, bound by the surface
    and quoted in the citation under the page, which is what the leaf above reads.
    """
    declared = {
        f"{name}.{parameter}"
        for name, query in CATALOG.items()
        if name.startswith(VIEW_PREFIX)
        for parameter, spec in query.params.items()
        if spec.default is not queries.REQUIRED
    }
    assert declared == set()


def test_every_page_fits_under_the_ceiling_it_is_priced_at() -> None:
    """The arithmetic over the sizes above: no route can be asked for more than it affords."""
    # How many children one open level of the NavTree shows. Not a bound parameter — the
    # NavTree composes its window around the query rather than binding it — and every leaf
    # below recomputes from whatever this says, so a literal is the only thing that reds when
    # the window silently narrows back to what it was.
    assert bounds.Bound(200, 200) == bounds.KIN
    # Every ceiling is projected at the largest page a URL can ask for, because a size is
    # something a reader types.
    assert worst_records_page_bytes() < PAGE_BYTES
    # And the record that page opens for a reader who did not click it, which is priced as a
    # page rather than as the per-value fetch it goes to: every character its own token, plus
    # the indentation a JSON record gains, which is whitespace and written out bare.
    assert bounds.OPENED_RECORD_CHARS * MARKED_CHAR_BYTES + bounds.INDENT_CHARS < PAGE_BYTES
    assert bounds.CHUNK.ceiling * ESCAPED_CHAR_BYTES < PAGE_BYTES
    # The list is the page a corpus grows, so its ceiling is the widest page a URL can ask for
    # plus the chrome that rides every page — both bound by construction now, not by how long
    # the titles this corpus happens to hold are.
    assert worst_session_list_bytes() < PAGE_BYTES
    # And it is the most rows that fit, not merely some number that does: the ceiling is
    # derived from the row's cost, so a row that grew has to move it rather than eat the slack
    # silently. The two together are what make `bounds.SESSIONS` a measurement — an upper bound
    # alone is satisfied by any smaller page, including one a stale derivation left behind.
    # It is the only ceiling held from below, for the reason kept beside the constants.
    assert (
        MEASURED_LIST_CHROME + (bounds.SESSIONS.ceiling + 1) * worst_session_row_bytes()
        >= PAGE_BYTES
    )
    # The landing page grows the same way — a project per repository the corpus records — and
    # its ceiling is not a size a URL carries: a reader picks a project rather than paging.
    assert worst_projects_page_bytes() < PAGE_BYTES
    # And a session's errors list, which grows the way both of those do — nothing about a
    # session caps how often its tools fail — and is not a size a URL carries either: a reader
    # jumps to a failure rather than paging through them.
    assert worst_errors_page_bytes() < PAGE_BYTES
    # And the node page, the one page every node URL serves: the NavTree a reader walks down the
    # left, and the pane beside it. Its three sizes are each their own ceiling, so this is the
    # widest response any node URL can be asked for.
    assert worst_node_bytes() < NODE_BYTES
    # And the expansion a row of a log opens in place, which is a click and so has a ceiling of
    # its own: a body, and one page of the level under it at the size the reader is reading logs
    # under. Nothing derives this from `PAGE_BYTES` — it is over it — so the number is declared
    # and the arithmetic checked against it here.
    assert worst_expansion_bytes() < EXPANSION_BYTES
    # And no default asks for more than its own ceiling allows, which nothing else checks: a
    # default above the ceiling serves a 400 to a reader who typed no size at all. Read off the
    # module rather than listed, so a size added later cannot dodge the check.
    declared = {
        name: value for name, value in vars(bounds).items() if isinstance(value, bounds.Bound)
    }
    for name, size in declared.items():
        assert size.default <= size.ceiling, name
    # ...and those are the sizes this leaf priced above: a new one reds here until its ceiling
    # is spent in the arithmetic too, rather than riding a page nobody weighed.
    assert set(declared) == {
        "KIN",
        "LOG",
        "DETAIL",
        "RECORDS",
        "CHUNK",
        "SESSIONS",
        "PROJECTS",
        "ERRORS",
    }
    # The same for the bounds that are not sizes a URL carries: how deep a chain opens, how
    # many turn rows no cursor reaches, how long a value is marked up in its own syntax, and
    # what one row of the NavTree may weigh. A width is not among them — a width belongs to
    # the surface that prints it, and the surfaces are pinned above.
    assert {name for name, value in vars(bounds).items() if isinstance(value, int)} == {
        "DEPTH",
        "CURSORLESS_TURNS",
        "INDENT_CHARS",
        "HIGHLIGHT_CHARS",
        "OPENED_RECORD_CHARS",
        "NAV_TREE_ROW_BYTES",
    }


def test_a_pin_a_page_no_longer_reaches_passes_the_everyday_run_and_reds_the_exact_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mode that turns re-pinning into something a run reds on rather than a step to remember.

    Every measured pin above is read as a ceiling, which is what lets an everyday change make a
    page smaller without re-pinning the page — and is also how a constant quietly stops
    describing what it measured. A change that moves bytes on purpose runs the suite under
    `HYPHAE_PIN_EXACT=1`, where the same pin has to be the measurement, and re-pins whatever the
    run names. That is the run the conversion to components made against every constant in
    `budgets.py`.
    """
    # A page that came in 100 B under its pin. The everyday run has nothing to say about it...
    monkeypatch.delenv(EXACT_PIN, raising=False)
    assert not exact_pins()
    assert fits(measured=8_896, budget=8_996)
    # ...and the exact run reds on it, naming both numbers through the leaf that called it.
    monkeypatch.setenv(EXACT_PIN, "1")
    assert exact_pins()
    assert not fits(measured=8_896, budget=8_996)
    # The pin that run would write instead is the measurement, and it passes both ways.
    assert fits(measured=8_896, budget=8_896)
    monkeypatch.delenv(EXACT_PIN)
    assert fits(measured=8_896, budget=8_896)


def limits(sql: str) -> list[str]:
    """What follows each LIMIT in a statement, comments cut — a parameter, or a number."""
    return re.findall(r"\bLIMIT\s+([^\s;]+)", re.sub(r"--[^\n]*", " ", sql))


def test_the_limit_scan_catches_a_literal_page_size() -> None:
    """The scan below is worth its green: it flags the page size no caller can change.

    Both statements are invented — every shipped query binds its limit, which is exactly why
    the instrument needs a case of its own.
    """
    assert limits("SELECT * FROM raw_records LIMIT 100;") == ["100"]
    assert limits("SELECT * FROM raw_records LIMIT $page_records -- LIMIT 100") == ["$page_records"]


@pytest.mark.parametrize("name", sorted(name for name in CATALOG if name.startswith(VIEW_PREFIX)))
def test_every_page_size_in_a_viewer_query_is_a_bound_parameter(name: str) -> None:
    """No viewer query hides a page size in its text, so every bound is one a reader can see.

    The rule rather than a list of the parameters that exist today: a query landing with a
    literal `LIMIT 100` is a size nobody can bind down to reach its boundary in a test, and
    nobody can bind up when a real corpus needs more.
    """
    for limit in limits(queries.load(name)):
        assert limit.startswith("$"), f"{name} limits by a literal: {limit}"
        assert limit.lstrip("$") in CATALOG[name].params


def test_every_fat_column_is_still_a_column(enriched_store: duckdb.DuckDBPyConnection) -> None:
    """The scan is spelled in column names, so a rename must fail here rather than pass.

    Read against the described corpus rather than the bare one: `description` is a column of
    the enrichment tables, which a store no pass has touched does not have.
    """
    named = {
        row[0]
        for row in enriched_store.execute(
            "SELECT column_name FROM duckdb_columns() WHERE schema_name = 'main'"
        ).fetchall()
    }
    assert set(FAT) <= named


def test_a_served_page_stays_under_its_ceiling(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """No page the viewer serves is large enough to stall a browser, at any corpus size."""
    listing = len(client.get("/sessions").content)
    (count,) = one(store, "SELECT count(*) FROM sessions")
    assert listing < PAGE_BYTES
    # The fixture corpus is smaller than a page, so its own weight proves nothing about a
    # large one. What does is the marginal cost of a row — the whole list less the same page
    # holding one session — which is what a growing corpus multiplies. The rows here are
    # redacted down to a few characters, so this is a smoke check: the worst case a real
    # corpus can reach is the arithmetic above, and the planted leaf below re-measures it.
    chrome = len(client.get("/sessions?size=1").content)
    per_session = (listing - chrome) / (count - 1)
    assert chrome + per_session * bounds.SESSIONS.ceiling < PAGE_BYTES
    # And every session's own node page, which is the widest of the eight the NavTree opens on:
    # the whole main thread is under the selection. A node page's three sizes are each their
    # own ceiling, so the defaults are also the largest response a URL can ask for.
    for session_id in [row[0] for row in store.execute("SELECT id FROM sessions").fetchall()]:
        page = client.get(f"/session/{session_id}")
        assert page.status_code == 200, session_id
        assert len(page.content) < PAGE_BYTES, session_id


def test_every_route_the_viewer_exposes_is_in_the_payload_sweep(client: TestClient) -> None:
    """The sweep covers the routes the app has, not the ones someone remembered to list.

    Without this, a route shipped later is a page nothing weighs — and a route that selects
    a fat column is exactly the kind of thing that arrives quietly.
    """
    exposed = {
        route.path
        for route in client.app.routes  # pyrefly: ignore
        if isinstance(route, APIRoute)
    }
    assert exposed == set(SCENARIOS)


@pytest.mark.parametrize("path", sorted(scenario.url for scenario in SCENARIOS.values()))
def test_no_route_serves_more_than_the_page_ceiling(path: str, enriched_client: TestClient) -> None:
    """Every route answers under the ceiling at the sizes its URL carries.

    A smoke check rather than the proof: the fixture corpus is far smaller than a page, so
    what makes the bound hold is the fat-column scan and the page-size arithmetic above. What
    this catches is the route that ships a whole column anyway.

    Over the described store, because six of the routes fetch what an enrichment pass wrote
    and a store no pass has touched holds no such table — and because a described page is the
    dearer one either way.
    """
    response = enriched_client.get(path)
    assert response.status_code == 200, path
    assert len(response.content) < PAGE_BYTES, path


@pytest.mark.parametrize("name", sorted(CATALOG))
def test_every_query_the_library_ships_serves_under_the_ceiling(
    name: str, client: TestClient
) -> None:
    """A query page weighs its file marked up, and no library file is near the ceiling.

    The one page whose size is a file's rather than a bound's: the SQL is served whole, because
    a statement a reader cannot run is not a citation. Marking it up multiplies it about
    fourfold, so what this pins is that no query in the library is long enough for that to
    matter — and that a query added later is measured rather than assumed.
    """
    page = client.get(f"{QUERY_URL}/{name}")
    assert page.status_code == 200, name
    assert len(page.content) < PAGE_BYTES, name


def test_an_offload_of_nothing_but_escapes_still_serves_under_the_ceiling(
    plant: Planter,
) -> None:
    """The largest chunk anyone can ask for stays under the ceiling however the file escapes.

    Every other bound here rests on a measured cost per row. An offload can't: it holds a file
    a tool wrote, and a chunk of pure `&` weighs five times what the same chunk of prose does.
    The content is invented for exactly that reason — no recorded offload is adversarial, and
    the point of the leaf is the character no corpus happens to contain.
    """
    escapes = "&" * bounds.CHUNK.ceiling
    path = plant(
        ("UPDATE offload_files SET content = ? WHERE session_id = ?", [escapes, CONFIG_ONLY])
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(
            f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}", params={"size": bounds.CHUNK.ceiling}
        )
    assert page.status_code == 200
    # Served whole — the chunk is not silently cut — and still under the ceiling. Counted
    # inside the block rather than over the page, which also carries escaped `&` in its links.
    assert block(page.text, "content").count("&amp;") == bounds.CHUNK.ceiling
    assert len(page.content) < PAGE_BYTES


def escaping_json(chars: int) -> str:
    """Valid JSON of exactly `chars` characters, in the shape a record costs most to mark up.

    A list of one-character strings: every element is its own token, so the formatter writes a
    span around three characters, and the character inside escapes to five bytes. Indented,
    each element also lands on a line of its own. Invented for the same reason the offload's
    content is — no recorded record is adversarial, and a record that parses is the only one
    the page marks up at all.
    """
    elements = ['"&"'] * ((chars - 2) // 4)
    listed = "[" + ",".join(elements) + "]"
    # The slack goes inside the last string, which keeps it valid JSON and one more token.
    return listed[:-2] + "&" * (chars - len(listed)) + listed[-2:]


def test_the_record_a_page_opens_unasked_serves_under_the_ceiling(plant: Planter) -> None:
    """The widest record a page fetches without a click stays under a page's ceiling.

    Every other per-value fetch here is exempt from the page bound: its unit is one value, and
    a reader who clicks for a value has asked for whatever the store holds. This one is not,
    because nobody clicked — the row the browser opens on arrival is a fetch the page starts —
    so `bounds.OPENED_RECORD_CHARS` is what keeps it a page's worth. An expansion is on the
    clicked side of that line and still over a page, which is why it carries a declared ceiling
    of its own rather than an exemption: see `EXPANSION_BYTES`.
    """
    raw = escaping_json(bounds.OPENED_RECORD_CHARS)
    assert len(raw) == bounds.OPENED_RECORD_CHARS
    path = plant(
        (
            "UPDATE raw_records SET raw = ? WHERE session_id = ? AND source = ? AND line_no = ?",
            [raw, RESUME, MAIN, RESUME_LONG_RECORD],
        )
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(
            f"/session/{RESUME}/thread/{MAIN}/records", params={"after": RESUME_LONG_RECORD - 1}
        )
        served = planted.get(
            f"/fragment/record/session/{RESUME}/thread/{MAIN}/line/{RESUME_LONG_RECORD}"
        )
    # The page opens this one on arrival, so what it weighs is what the page's load costs...
    assert inside(page.text, "data-open-record", str(RESUME_LONG_RECORD), "hx-trigger") == ["load"]
    # ...and it is the marked-up path being weighed, not a record served plain because it did
    # not parse — which is the whole reason a character is priced at a span and not an escape.
    assert served.status_code == 200
    assert "<span" in block(served.text, "raw")
    assert len(served.content) < PAGE_BYTES
