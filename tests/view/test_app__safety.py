"""What a page does with text nobody here wrote: it prints it, and nothing runs.

A transcript can hold anything an agent read, so the markup a session recorded has to arrive as
characters rather than as elements. Planting it is the only way to see that: no recorded
fixture carries a `<script>`, and the content security policy is what would catch the rest.
"""

import json

import pytest
from fastapi.testclient import TestClient

from hyphae.view import nodes
from hyphae.view.app import CSP, build_app
from tests.conftest import (
    ANCESTOR,
    DENSE_CALL,
    DENSE_CALL_TURN,
    DENSE_TOOL,
    DENSE_TURN,
    DENSE_TURN_CALL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    SLASH_TURN,
    SPINE,
    SPINE_RUN,
)
from tests.view.conftest import (
    MISSING,
    Planter,
    inside,
)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/sessions",
        "/sessions?sort=bogus",
        f"/session/{SPINE}",
        f"/session/{MISSING}",
        f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}",
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{MISSING}",
        "/static/style.css",
    ],
)
def test_every_response_carries_the_content_security_policy(path: str, client: TestClient) -> None:
    """The policy rides every response, error pages and static files included."""
    assert client.get(path).headers["content-security-policy"] == CSP


def test_planted_markup_arrives_inert(plant: Planter) -> None:
    """Text from a transcript is escaped everywhere it lands on a page or a fragment.

    The sentinels are invented — no redacted fixture carries markup — and each lands on a
    real row, so this checks the template chain rather than a hand-built page. `render.py`'s
    own leaves cannot stand in for this one: a template that piped a value through `|safe`
    would bypass them entirely, and only a rendered response shows it.
    """
    sentinel = "<script>alert('planted')</script>"
    path = plant(
        ("UPDATE sessions SET title = ? WHERE id = ?", [sentinel, SPINE]),
        # Both columns a turn's heading can read: a plain turn shows the prompt, a slash turn
        # shows what followed the command instead, and neither may reach the page as markup.
        (
            "UPDATE turns SET prompt = ?, command_args = ? WHERE session_id = ?",
            [sentinel, sentinel, SPINE],
        ),
        ("UPDATE agent_runs SET brief = ? WHERE session_id = ?", [sentinel, SPINE]),
        # The markdown path: what a model wrote, which is the one value the viewer renders
        # rather than escapes, and the tool arguments beside it.
        (
            "UPDATE api_calls SET text = ?, thinking = ? WHERE session_id = ?",
            [sentinel] * 2 + [ANCESTOR],
        ),
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ?",
            [sentinel] * 2 + [FORK_ORIGIN],
        ),
        # And the two a run's pane reads off the call that spawned it — the ask inside that
        # call's arguments, and the answer beside it. The input stays JSON here because that
        # is where the ask lives: a run whose spawning call carries no readable arguments has
        # no ask to render, and this leaf is about the one that does.
        (
            "UPDATE tool_calls SET input = ?, result = ? WHERE session_id = ? AND id IN"
            " (SELECT tool_use_id FROM agent_runs WHERE session_id = ?)",
            [json.dumps({"prompt": sentinel}), sentinel, SPINE, SPINE],
        ),
        # The transcript itself, which the records browser previews and serves whole. Raw
        # records are the least filtered thing the viewer shows: what Claude Code wrote.
        ("UPDATE raw_records SET raw = ? WHERE session_id = ?", [sentinel, ANCESTOR]),
    )
    with TestClient(build_app(path)) as client:
        served = (
            client.get("/sessions").text,
            # The session pane, whose NavTree rows are named by the turn prompts and the run
            # descriptions the plant rewrote.
            client.get(f"/session/{SPINE}").text,
            # A turn pane, whose children log previews the calls' text.
            client.get(f"/session/{ANCESTOR}/thread/{MAIN}/turn/{DENSE_TURN}").text,
            client.get(
                f"/fragment/text/session/{ANCESTOR}/thread/{MAIN}/call/{DENSE_TURN_CALL}"
            ).text,
            client.get(
                f"/fragment/input/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ).text,
            client.get(
                f"/fragment/result/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}"
            ).text,
            # What followed a slash command, which is rendered rather than escaped, like the
            # prompt a plain turn shows in its place.
            client.get(f"/fragment/args/session/{SPINE}/thread/{MAIN}/turn/{SLASH_TURN}").text,
            # The turn whose calls each made tool calls, where a row names the tools its call
            # went on to make: the titles are read out of the arguments the plant rewrote.
            client.get(
                f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/turn/{DENSE_CALL_TURN}"
            ).text,
            # And that call's body opened as an expansion, which is the same arguments again,
            # a row a tool this time and rendered by a fragment rather than by a page.
            client.get(
                f"{nodes.BODY_URL}/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}"
            ).text,
            # A run pane, whose ask and answer are rendered as the markdown they were
            # written in, and each of their fetches.
            client.get(f"/session/{SPINE}/run/{SPINE_RUN}").text,
            client.get(f"/fragment/prompt/session/{SPINE}/run/{SPINE_RUN}").text,
            client.get(f"/fragment/result/session/{SPINE}/run/{SPINE_RUN}").text,
            client.get(f"/session/{ANCESTOR}/thread/{MAIN}/records").text,
            client.get(f"/fragment/record/session/{ANCESTOR}/thread/{MAIN}/line/1").text,
        )
        for page in served:
            # The sentinel survives to the page as text — angle brackets escaped, the one form
            # Jinja, markdown-it and markupsafe all agree on...
            assert "&lt;script&gt;alert(" in page
            # ...and never as markup the browser would run.
            assert "<script>alert" not in page


def test_a_pr_link_is_a_link_only_when_a_browser_should_follow_it(plant: Planter) -> None:
    """A session's PR links are followable URLs; anything else on that list renders as text.

    A `pr_url` is the one transcript value that reaches an attribute the browser acts on, so
    escaping alone does not settle it — an escaped `javascript:` URL is still a `javascript:`
    URL in an `href`. Both values are planted and invented: the recorded sessions carry PR
    links redaction flattened to a placeholder.
    """
    followable = "https://example.test/org/repo/pull/1"
    unfollowable = "javascript:alert('planted')"
    path = plant(
        (
            "INSERT INTO pr_links VALUES"
            " (?, 900001, 1, ?, 'planted/repo', '2026-01-01T00:00:00Z'),"
            " (?, 900002, 2, ?, 'planted/repo', '2026-01-01T00:00:00Z')",
            [SPINE, followable, SPINE, unfollowable],
        ),
    )
    with TestClient(build_app(path)) as planted:
        page = planted.get(f"/session/{SPINE}").text
    # The http URL is a link the reader can click...
    assert inside(page, "data-pr", followable, "href") == [followable]
    # ...and the other reaches no href at all, while still being shown for what it is.
    assert inside(page, "data-pr", unfollowable, "href") == []
    assert "javascript:alert(&#39;planted&#39;)" in page
