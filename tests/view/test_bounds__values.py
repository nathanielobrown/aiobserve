"""The one exemption from the page ceiling: a fetch whose unit is a single stored value.

A per-value fragment serves what the store holds, so no page size can bound it. What holds it
instead is that it serves that value and nothing rendering could multiply out of it, and that
every value on its way to a page or a log is cut in SQL before it gets there.
"""

import json
import re
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from aiobserve.analyze import queries
from aiobserve.view.app import build_app
from aiobserve.view.format import ELLIPSIS
from tests.conftest import (
    ANCESTOR,
    DENSE_TOOL,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    SPINE,
    SPINE_RUN,
)
from tests.view.budgets import (
    PAGE_BYTES,
)
from tests.view.conftest import (
    Planter,
    block,
    fields,
    inside,
    one,
    plain,
    suggestions,
)


def test_a_deeply_nested_value_is_served_at_the_size_it_was_stored(plant: Planter) -> None:
    """A per-value fetch serves the value it names, not what indenting could turn it into.

    Indenting is the one thing that can break the per-value exemption above, because it is
    quadratic in nesting: 10 KB of nothing but `[` indents to 50 MB, and past the parser's
    own stack the fragment answered 500 rather than anything at all. Both values are invented
    and have to be — nothing recorded nests remotely this deep, which is the point.
    """
    indents_huge = "[" * 5_000 + "]" * 5_000
    overflows_the_parser = "[" * 10_000 + "]" * 10_000
    path = plant(
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
            [indents_huge, indents_huge, FORK_ORIGIN],
        ),
        ("UPDATE raw_records SET raw = ? WHERE session_id = ?", [overflows_the_parser, ANCESTOR]),
    )
    with TestClient(build_app(path)) as planted:
        tool = f"/fragment/{{}}/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
        fetched = [
            (planted.get(tool.format("input")), len(indents_huge)),
            (planted.get(tool.format("result")), len(indents_huge)),
            (
                planted.get(f"/fragment/record/session/{ANCESTOR}/thread/main/line/1"),
                len(overflows_the_parser),
            ),
        ]
    # Each fragment answers, and weighs the value it names plus a page of chrome at most.
    for response, stored in fetched:
        assert response.status_code == 200
        assert len(response.content) < stored + PAGE_BYTES


def printed(html: str) -> list[str]:
    """Every value a children log's rows print, as a reader sees it — the marks and all.

    Any attribute may sit in front of the field's own: the second line of a wide column is
    classed as well as named, and a pattern anchored on `data-field` reads past it.
    """
    return [
        value
        for row in re.findall(r"<tr data-child=.*?</tr>", html, flags=re.DOTALL)
        for value in re.findall(r'<span [^>]*data-field="[^"]*">(.*?)</span>', row, flags=re.DOTALL)
    ]


def test_a_long_value_is_cut_before_it_reaches_a_page_or_a_fragment(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """Every preview is truncated before it reaches a page, so no one huge value can bloat it.

    The four widths the viewer cuts to, checked at once against one planted store: a list
    row's, a tree row's title, a children log row's, and a pane's — a header's strings at one
    cut and the one value it is about at another, wider one. The oversized values are
    invented: redaction flattened every recorded string to a few characters, so no fixture
    reaches a cap.
    """
    # One turn of each kind, because a pane shows one arm or the other: a plain turn's prompt,
    # and a slash turn's command with its arguments.
    turn_id, _ = one(
        store,
        "SELECT id, \"index\" FROM turns WHERE session_id = ? AND source = 'main'"
        ' AND command_name IS NULL ORDER BY "index"',
        [SPINE],
    )
    command_id, _ = one(
        store,
        "SELECT id, \"index\" FROM turns WHERE session_id = ? AND source = 'main'"
        ' AND command_name IS NOT NULL ORDER BY "index"',
        [SPINE],
    )
    # And one tool call to dress as a command, on a page of its own: what a tool row shows is
    # read out of the input JSON rather than selected, so the two strings a command row prints
    # are cut on the way out and nowhere else. It has to be a second call, because the one
    # below keeps an input that is not JSON — the arm that shows the input as stored.
    asked_session, asked_source, asked_call, asked_id = one(
        store,
        "SELECT session_id, source, api_call_id, id FROM live_tool_calls WHERE session_id <> ?"
        ' ORDER BY session_id, source, api_call_id, "index"',
        [ANCESTOR],
    )
    # And one tool call whose own page the sweep below reads, on the session whose tool rows
    # the plant overflows.
    named_source, named_id = one(
        store,
        'SELECT source, id FROM live_tool_calls WHERE session_id = ? ORDER BY source, "index"',
        [ANCESTOR],
    )
    # Each value is planted well past its own cap, onto the real row a fixture recorded...
    long = "x" * (queries.DETAIL_CHARS + 5_000)
    path: Path = plant(
        (
            "UPDATE sessions SET title = ?, project_dir = ?, git_branch = ?, version = ?,"
            " entrypoint = ? WHERE id = ?",
            [long, long, long, long, long, SPINE],
        ),
        ("UPDATE turns SET prompt = ? WHERE session_id = ? AND id = ?", [long, SPINE, turn_id]),
        (
            "UPDATE turns SET command_name = ?, command_args = ? WHERE session_id = ? AND id = ?",
            [long, long, SPINE, command_id],
        ),
        (
            "UPDATE agent_runs SET brief = ?, agent_type = ?, model = ? WHERE session_id = ?",
            [long, long, long, SPINE],
        ),
        (
            "UPDATE api_calls SET text = ?, model = ?, fallback_from = ? WHERE session_id = ?",
            [long, long, long, ANCESTOR],
        ),
        ("UPDATE tool_calls SET input = ?, name = ? WHERE session_id = ?", [long, long, ANCESTOR]),
        (
            "UPDATE tool_calls SET name = ?, input = ? WHERE id = ?",
            ["Bash", json.dumps({"description": long, "command": long}), asked_id],
        ),
    )
    with TestClient(build_app(path)) as planted:
        listing = planted.get("/sessions").text
        session = planted.get(f"/session/{SPINE}").text
        turn = planted.get(f"/session/{SPINE}/thread/main/turn/{turn_id}").text
        slash = planted.get(f"/session/{SPINE}/thread/main/turn/{command_id}").text
        run = planted.get(f"/session/{SPINE}/run/{SPINE_RUN}").text
        call = planted.get(f"/session/{ANCESTOR}/thread/main/call/{DENSE_TURN_CALL}").text
        asked = planted.get(
            f"/session/{asked_session}/thread/{asked_source}/call/{asked_call}"
        ).text
        ran = planted.get(f"/session/{asked_session}/thread/{asked_source}/tool/{asked_id}").text
        named = planted.get(f"/session/{ANCESTOR}/thread/{named_source}/tool/{named_id}").text
    # ...and what each of them shows is its cap, not the value. The list's cuts are the
    # viewer's own composition rather than its query's, because its filters read the whole
    # values — a project path cut to a head would match no session under a longer one.
    row = fields(listing, "data-session-id", SPINE)
    # Marked as cut, not merely short enough: a row's strings are the ones a page multiplies,
    # so a value that ended at the width and one that was stopped there have to read apart.
    assert row["title"] == row["project_dir"] == "x" * queries.LIST_CHARS + ELLIPSIS
    # And each member of the lists beside them, at the narrower width a member takes.
    assert row["agent_types"].startswith("x" * queries.LIST_ITEM_CHARS + ELLIPSIS)
    # A path too long for the filter box to suggest whole is left out of it rather than cut:
    # half a path fills the filter in with a value that matches nothing. Bounded by the box
    # still being full — an absence read off an empty list is no absence at all.
    offered = suggestions(listing)
    assert offered and not [path for path in offered if "x" in path]
    # A tree row is a line in the tree, so its title takes the narrowest cut of the four —
    # the same one whatever kind of node the row stands for. Read off the tree half of the
    # page: the same `title` field names the node in three places, each at its own width.
    tree, pane = session.split('<article id="reading-pane">')
    titles = re.findall(r'<span data-field="title">(.*?)</span>', tree, flags=re.DOTALL)
    # Cut and marked as cut: every column a title is composed from comes back one character
    # past the width, so a row that fills the line says the value went on.
    assert max(titles, key=len) == "x" * queries.NAV_CHARS + ELLIPSIS
    # A children log row is a line of a table, so it takes the next cut up — and every value
    # the plant reached is marked where it was cut, not merely short enough. Per value and not
    # at the maximum: a maximum is satisfied by whichever sibling overflowed furthest, which
    # is how a whole column of silently-truncated values hid behind a marked neighbour here.
    # What the three pages between them print: a plain turn's prompt and a slash turn's command
    # with its arguments, a tool's name, the head of what it was asked read out of an input
    # that is not JSON and out of one that is, and the command that head describes.
    reached = [value for value in printed(pane) + printed(call) + printed(asked) if "x" in value]
    assert len(reached) == 6
    assert set(reached) == {"x" * queries.LOG_CHARS + ELLIPSIS}
    # And the pane heads the node it is about at the widest of the three, because nothing on
    # the page repeats it. Every kind, not the session alone: the tree built the row the pane
    # stands on and cut its words to a tree row's width, and a title that took the tree's
    # word for it would head a turn with a third of the prompt it is about.
    #
    # Every string a header prints is cut at that width and says so, whether it heads the pane
    # or sits in the facts under it — a value that ends at the width with no mark is one a
    # reader cannot tell from a value that simply ended there.
    #
    # Swept over the whole header rather than field by field: which fields a header prints
    # grows with the store, and a list written out here would go on passing while the field
    # added beside it truncated in silence.
    headed = "x" * queries.HEADER_CHARS + ELLIPSIS
    for shown, kind in (
        (session, "session"),
        (turn, "turn"),
        (slash, "turn"),
        (call, "call"),
        (run, "run"),
        (named, "tool"),
    ):
        filled = {
            field: value
            for field, value in fields(shown, "data-body", kind).items()
            if "x" in value
        }
        # The plant reached this pane at all, so a sweep finding nothing is a sweep that
        # proves nothing...
        assert filled, kind
        # ...and everything it reached is cut to the header's width and marked there.
        assert set(filled.values()) == {headed}, (kind, filled)
    # A pane reads one node, so its strings take a header's cut — and the one value the node
    # is about takes the widest of the four, with the rest of it offered as its own fetch.
    assert fields(turn, "data-detail", "prompt")["prompt"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert inside(turn, "data-detail", "prompt", "data-whole") == ["prompt"]
    # A slash turn shows the same two widths on one page: the command it ran is a word the
    # pane leads with, cut to a header's width, and what followed it is a second value of the
    # turn, cut to a pane's and offering the rest of itself like the prompt does.
    assert fields(slash, "data-command", command_id)["command_name"] == headed
    arguments = fields(slash, "data-detail", "command_args")
    assert arguments["command_args"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert inside(slash, "data-detail", "command_args", "data-whole") == ["command_args"]
    brief = fields(run, "data-detail", "brief")["brief"]
    assert brief == "x" * queries.DETAIL_CHARS + ELLIPSIS
    assert fields(call, "data-detail", "text")["text"] == "x" * queries.DETAIL_CHARS + ELLIPSIS
    # A detail the page marks up is cut the same way and says so the same way, which no other
    # assertion here reaches: the mark lands inside the highlighted block, where it is one
    # more character for the lexer to make of what it will. Read back through the markup,
    # because a value that came back marked up is only cut if a reader still sees the cut.
    assert plain(block(ran, "command")) == "x" * queries.DETAIL_CHARS + ELLIPSIS
