"""The offload page: a tool result Claude Code wrote to a file instead of the transcript.

Two things make this page different from the rest of the viewer. The content has no ceiling —
the canonical store holds one over 50 MB — so it is served in chunks rather than whole. And
the file's *name* comes from the transcript, so it is a value the page carries, never a path
the server follows.
"""

from urllib.parse import quote

import duckdb
from fastapi.testclient import TestClient

from aiobserve.view import bounds
from aiobserve.view.app import build_app
from tests.conftest import (
    CONFIG_ONLY,
    FORK_ORIGIN,
    OFFLOAD_CHARS,
    OFFLOAD_FILE,
    OFFLOAD_TOOL,
)
from tests.view.conftest import MISSING, Planter, fields, inside, one, values


def test_an_offloaded_result_is_served_in_chunks_that_reassemble_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Following an offload's chunks hands back the file, once and in order.

    Chunked at 64 characters over the corpus's recorded 159-character file, so the boundary
    is a real overflow of a recorded value rather than a staged one — three chunks, the last
    a short one.
    """
    (stored,) = one(
        store,
        "SELECT content FROM offload_files WHERE session_id = ? AND name = ?",
        [CONFIG_ONLY, OFFLOAD_FILE],
    )
    assert len(stored) == OFFLOAD_CHARS, "the recorded offload moved: re-pick the file"
    url = f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}"
    # Walking from the start, taking the offset each page hands back...
    read = ""
    after = 0
    for _ in range(4):
        page = client.get(url, params={"after": after, "size": 64})
        assert page.status_code == 200
        chunk = fields(page.text, "data-offload", OFFLOAD_FILE)["content"]
        read += chunk
        following = values(page.text, "data-more-offload")
        if not following:
            break
        after = int(following[0])
    else:
        raise AssertionError("the offload never ran out of chunks")
    # ...reassembles the file. Compared with whitespace collapsed, because the HTML reader
    # strips each chunk it lifts and a chunk boundary can land inside a run of spaces — what
    # this leaf is about is the partition, not the `pre` the browser renders.
    assert "".join(read.split()) == "".join(stored.split())
    # Three chunks over 159 characters, so a boundary was really crossed twice.
    assert len(read) > 2 * 64


def test_the_page_says_what_the_store_holds_and_how_it_decoded(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """An offload page carries the file's real size and whether the decode lost anything.

    `size_bytes` is what was on disk; the chunks are characters. A page that showed only the
    chunk would leave a reader unable to tell a truncated read from a small file.
    """
    size, lossy = one(
        store,
        "SELECT size_bytes, lossy_decode FROM offload_files WHERE session_id = ? AND name = ?",
        [CONFIG_ONLY, OFFLOAD_FILE],
    )
    shown = fields(
        client.get(f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}").text,
        "data-offload",
        OFFLOAD_FILE,
    )
    assert shown["size_bytes"] == str(size)
    assert shown["content_chars"] == str(OFFLOAD_CHARS)
    # The recorded file decoded cleanly, so the page says nothing about a lossy one.
    assert lossy is False
    assert "lossy_decode" not in shown


def test_a_tool_call_links_to_the_file_its_result_went_to(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A tool call whose result was offloaded reaches the file from the call's own fragment."""
    (source,) = one(store, "SELECT source FROM tool_calls WHERE id = ?", [OFFLOAD_TOOL])
    fragment = client.get(f"/fragment/tool/{CONFIG_ONLY}/{source}/{OFFLOAD_TOOL}").text
    link = inside(fragment, "data-tool-value", OFFLOAD_TOOL, "href")
    assert link == [f"/session/{CONFIG_ONLY}/offload/{quote(OFFLOAD_FILE)}"]
    assert client.get(link[0]).status_code == 200


def test_a_name_needing_escaping_survives_the_round_trip(
    plant: Planter, store: duckdb.DuckDBPyConnection
) -> None:
    """A file name with a space and a percent in it still reaches its own page.

    The name is Claude Code's to choose, and the two characters here are the ones that break
    a URL built by concatenation: a space ends the attribute, a percent starts an escape.
    Planted onto the recorded row — no fixture carries an awkward name today, and the point
    is that one arriving tomorrow is a link that works rather than a 404.
    """
    awkward = "run 100% output.txt"
    path = plant(
        ("UPDATE offload_files SET name = ? WHERE session_id = ?", [awkward, CONFIG_ONLY]),
        ("UPDATE tool_calls SET offload_file = ? WHERE id = ?", [awkward, OFFLOAD_TOOL]),
    )
    with TestClient(build_app(path)) as planted:
        # The link the tool fragment renders is the one the test follows — built by the app,
        # not by the test, so a template that forgot to quote fails here.
        (source,) = one(store, "SELECT source FROM tool_calls WHERE id = ?", [OFFLOAD_TOOL])
        fragment = planted.get(f"/fragment/tool/{CONFIG_ONLY}/{source}/{OFFLOAD_TOOL}").text
        (link,) = inside(fragment, "data-tool-value", OFFLOAD_TOOL, "href")
        page = planted.get(link)
    assert page.status_code == 200
    assert fields(page.text, "data-offload", awkward)["name"] == awkward


def test_a_name_that_looks_like_a_path_is_a_404(client: TestClient) -> None:
    """A traversal in the name buys nothing: the name is a key, never a path to open.

    The viewer reads the store and nothing else — `offload_files` holds the content — so a
    name shaped like a path is simply a name no row carries.
    """
    for name in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", MISSING):
        response = client.get(f"/session/{CONFIG_ONLY}/offload/{name}")
        assert response.status_code == 404, name
        assert "root:" not in response.text
    # And a real name under a session that never offloaded anything is a 404 too, as is one
    # under a session the store has never held: the key is the pair, not either half.
    assert client.get(f"/session/{FORK_ORIGIN}/offload/{OFFLOAD_FILE}").status_code == 404
    assert client.get(f"/session/{MISSING}/offload/{OFFLOAD_FILE}").status_code == 404


def test_a_chunk_size_outside_its_bounds_is_refused(client: TestClient) -> None:
    """A hand-typed chunk size past the ceiling is a 400, not a whole 50 MB file."""
    for size in (0, bounds.CHUNK.ceiling + 1):
        response = client.get(
            f"/session/{CONFIG_ONLY}/offload/{OFFLOAD_FILE}", params={"size": size}
        )
        assert response.status_code == 400, size
