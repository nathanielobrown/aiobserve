"""The popover behind a NavTree row: the exact numbers its bar and its badge stand for.

A row draws two summaries and can print neither — a bar is twenty steps of a window, and a
badge is a dollar figure at cent precision. The popover is the numbers themselves, fetched
when a reader points at a row or tabs to it (`docs/viewer.md`).

The expectations are built out of `live_api_calls` in the test's own SQL rather than out of
the columns the page reads, so a derivation that drifted between the bar and the popover has
nothing to agree with. The spend is priced one call at a time, which is the reading the page
cannot take: it groups a node's tokens by model and prices each group once, and that is the
same arithmetic only if the group's cache write splits the way every call in it did.

The dollars that cross a thread boundary — what the agent runs under a node spent, and the
total the two come to — are `test_numbers__spend.py`, which reads back through the helpers
below.
"""

import re
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from hyphae.extract.pricing import CONTEXT_WINDOWS, CostSplit, TokenUsage, split_cost
from hyphae.view.app import build_app
from hyphae.view.format import ABSENT
from hyphae.view.nodes import NUMBERS_URL, Kind
from tests.conftest import (
    ANCESTOR,
    DENSE_CALL,
    DENSE_TOOL,
    FORK_ORIGIN,
    FORK_ORIGIN_RUN,
    MAIN,
    SEARCH_BASH_TOOL,
    SEARCH_TOOL,
    SPINE,
    SPINE_LEAF,
    SPINE_RUN,
)
from tests.view.conftest import (
    Bar,
    Planter,
    bar,
    fields,
    inside,
    one,
    step,
    wired,
)

# Where a node left the model's window: the last call it made that went to one. Ordered by
# `"index"`, which is unique and ascending inside a thread.
LAST = (
    "SELECT model, cache_read_tokens, cache_creation_tokens, input_tokens, output_tokens"
    " FROM live_api_calls WHERE session_id = ? AND source = ? AND NOT synthetic {extra}"
    ' ORDER BY "index" DESC LIMIT 1'
)

# And what its calls cost, a call at a time.
CHARGED = (
    "SELECT model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,"
    " cache_5m_tokens, cache_1h_tokens, cost_usd"
    " FROM live_api_calls WHERE session_id = ? {extra}"
)


def popped(client: TestClient, path: str) -> str:
    """One row's popover, as it was served."""
    answer = client.get(f"{NUMBERS_URL}{path}")
    assert answer.status_code == 200, answer.text
    return answer.text


def popover(client: TestClient, path: str, key: str) -> dict[str, str]:
    """One row's popover, read back as its labelled fields."""
    return fields(popped(client, path), "data-popover", key)


def held(
    store: duckdb.DuckDBPyConnection, session_id: str, source: str, extra: str = ""
) -> dict[str, str]:
    """The window half of a popover, as the store's own columns give it."""
    model, cached, creation, sent, out = one(store, LAST.format(extra=extra), [session_id, source])
    return {
        "model": model,
        "cached": f"{cached:,}",
        "new_input": f"{creation + sent:,}",
        "output": f"{out:,}",
        "fill": f"{cached + creation + sent + out:,}",
        "window": f"{CONTEXT_WINDOWS[model]:,}",
    }


def charged(
    store: duckdb.DuckDBPyConnection, session_id: str, extra: str = ""
) -> tuple[CostSplit, float]:
    """What a node's calls cost, priced one at a time, beside the total the store holds."""
    split = CostSplit(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0)
    stored = 0.0
    for model, sent, out, read, creation, five, hour, cost in store.execute(
        CHARGED.format(extra=extra), [session_id]
    ).fetchall():
        priced = split_cost(model, TokenUsage(sent, out, read, creation, five, hour))
        assert priced is not None, model
        split = CostSplit(*(part + other for part, other in zip(split, priced, strict=True)))
        stored += cost
    return split, stored


# The dollars the popover prints beside its token counts, in the order they stand.
CHARGES = ("cost_cached", "cost_new_input", "cost_output")


# How far a printed dollar may sit from the oracle's: one unit in the last place it prints.
# The two price the same tokens in a different order — the page sums a model's tokens and
# prices once, the oracle prices each call and sums the dollars — so a figure that lands on a
# tie rounds either way. `SPINE`'s output comes to exactly $0.27305 and does.
PRINTED_PLACE = 1e-4


def legend(split: CostSplit) -> dict[str, float]:
    """The dollar beside each token count, before it is printed.

    Three charges and not the price table's four: the cache a call wrote is counted in the
    tokens on the new-input line (`view_numbers.sql`), so its dollar is charged there too. A
    row of its own would leave a column of dollars that does not come to the total under it,
    which is the one arithmetic a reader can do in their head.
    """
    return dict(
        zip(
            CHARGES,
            (split.cache_read, split.input + split.cache_write, split.output),
            strict=True,
        )
    )


def misread(printed: dict[str, str], split: CostSplit) -> dict[str, tuple[str, str]]:
    """The charges whose printed dollar and priced dollar disagree, printed side by side."""
    return {
        field: (printed[field], f"${dollars:.4f}")
        for field, dollars in legend(split).items()
        if abs(amount(printed[field]) - dollars) > PRINTED_PLACE
    }


def amount(shown: str) -> float:
    """A printed dollar figure read back as the number it is."""
    return float(shown.removeprefix("$"))


def reached(store: duckdb.DuckDBPyConnection, session_id: str, source: str, turn_id: str) -> bool:
    """Whether a turn was answered by a model at all, or only by Claude Code's placeholder."""
    (answered,) = one(
        store,
        "SELECT count(*) FROM live_api_calls"
        " WHERE session_id = ? AND source = ? AND turn_id = ? AND NOT synthetic",
        [session_id, source, turn_id],
    )
    return answered > 0


def tokens(printed: dict[str, str], field: str) -> int:
    """One printed count read back as the number it is."""
    return int(printed[field].replace(",", ""))


def test_a_session_reads_its_window_and_its_dollars_off_the_thread_a_reader_is_on(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A session popover is the main thread's, and the runs it spawned stand under it.

    Both summaries are one thread's: the window because that is the thread a reader of the
    session is in — a run holds one of its own — and now the dollars too. They used to be
    every thread's, which made the session the one node whose three charges answered a
    different question from the three on the row under it. What the subagents cost is the
    line below instead, where a reader can see it is a different set of calls.
    """
    printed = popover(client, f"/session/{SPINE}", f"{Kind.SESSION}:{SPINE}")
    assert printed | held(store, SPINE, MAIN) == printed
    # Nothing came before a session for it to have added to, so the figure is the dash a
    # missing number prints rather than the whole of the fill dressed as a delta.
    assert printed["added"] == ABSENT
    # The three charges price the main thread's calls and nothing else, which on a session
    # that ran subagents is strictly less than what the session spent.
    split, main = charged(store, SPINE, extra=f"AND source = '{MAIN}'")
    (whole,) = one(store, "SELECT cost_usd FROM session_rollups WHERE session_id = ?", [SPINE])
    assert main < whole, "the reversal is only visible on a session with subagents"
    assert not misread(printed, split)
    # And they come to the total under them, which is now the main thread's own. Printed to the
    # place a cost is stored at rather than to the badge's cents: the popover is where a reader
    # adds the column up, and a column of cents would not come to a total in cents.
    assert printed["cost_usd"] == f"${main:.4f}"
    assert round(split.total, 4) == round(main, 4)
    # What left the column is the breakout line under it — every thread but the main one —
    # and the two of them come back to what the store says the session spent.
    (under,) = one(
        store,
        "SELECT round(sum(cost_usd), 4) FROM live_api_calls WHERE session_id = ? AND source <> ?",
        [SPINE, MAIN],
    )
    assert printed["cost_subagents"] == f"${under:.4f}"
    assert amount(printed["cost_total"]) == round(main + under, 4)
    assert round(amount(printed["cost_total"]), 2) == round(whole, 2)


def test_a_turn_says_what_it_put_into_the_window_since_the_turn_before_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Every turn of one thread, each measured against the turn that answered before it.

    The spine's main thread covers both readings its four turns hold: two that reached a model,
    and two that did not. A turn Claude Code answered with a placeholder has no window at all
    (`docs/schema.md`), and a popover that stood it where the turn before it stood would invent
    a reading — so its fill and its delta are the dash a NULL prints.
    """
    turns = [
        turn
        for (turn,) in store.execute(
            'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
            [SPINE, MAIN],
        ).fetchall()
    ]
    assert len(turns) > 1, "a delta needs a turn before it"
    stood = 0
    silent = 0
    for turn_id in turns:
        printed = popover(
            client, f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}", f"{Kind.TURN}:{turn_id}"
        )
        where = f"AND turn_id = '{turn_id}'"
        if not reached(store, SPINE, MAIN, turn_id):
            assert printed["fill"] == ABSENT, turn_id
            assert printed["added"] == ABSENT, turn_id
            silent += 1
            continue
        assert printed | held(store, SPINE, MAIN, extra=where) == printed, turn_id
        # Signed, always: what a turn added is a change, and a change that prints bare reads
        # as a total.
        assert printed["added"] == f"{tokens(printed, 'fill') - stood:+,}", turn_id
        stood = tokens(printed, "fill")
        split, _ = charged(store, SPINE, extra=f"AND source = '{MAIN}' {where}")
        assert not misread(printed, split), turn_id
    assert silent, "the spine is meant to hold a turn that never reached a model"


def test_a_run_reads_the_window_it_built_on_its_own_thread(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A run starts on an empty window, so what it added is the whole of what it holds."""
    for run_id in (SPINE_RUN, SPINE_LEAF):
        printed = popover(client, f"/session/{SPINE}/run/{run_id}", f"{Kind.RUN}:{run_id}")
        assert printed | held(store, SPINE, run_id) == printed, run_id
        assert printed["added"] == f"+{printed['fill']}", run_id
        split, _ = charged(store, SPINE, extra=f"AND source = '{run_id}'")
        assert not misread(printed, split), run_id


def test_a_call_says_the_cache_it_read_apart_from_the_context_it_sent(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """One api call's numbers are its own: what it added is its fill less the cache it read."""
    where = f"AND source = '{FORK_ORIGIN_RUN}' AND id = '{DENSE_CALL}'"
    printed = popover(
        client,
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/call/{DENSE_CALL}",
        f"{Kind.CALL}:{DENSE_CALL}",
    )
    assert printed | held(store, FORK_ORIGIN, FORK_ORIGIN_RUN, extra=where) == printed
    assert printed["added"] == f"{tokens(printed, 'fill') - tokens(printed, 'cached'):+,}"
    # One call is one model, so the dollars beside the counts are that call's own.
    split, stored = charged(store, FORK_ORIGIN, extra=where)
    assert not misread(printed, split)
    assert printed["cost_usd"] == f"${stored:.4f}"
    # One call answered, so the line saying how many did is absent: `over 1 api call` is a
    # sentence that says nothing, and the popover is already the node's own numbers.
    assert "api_calls" not in printed


def test_the_popovers_two_columns_come_to_the_totals_under_them(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """Both columns add up, which is what makes the block one reading rather than five numbers.

    The counts are the node's last answering call and come to the window it left; the dollars
    are every call the node made and come to the total under them. That is why the cache a call
    wrote is charged on the new-input line rather than on one of its own: its tokens are counted
    there, and a fourth dollar would leave a column that sums to nothing a reader can see.

    Over a turn that answered more than once, so the two columns are read over different sets
    of calls — which is what the line under them says out loud.
    """
    turn_id, answered = one(
        store,
        "SELECT turn_id, count(*) FROM live_api_calls"
        " WHERE session_id = ? AND source = ? AND NOT synthetic"
        ' GROUP BY turn_id ORDER BY count(*) DESC, min("index") LIMIT 1',
        [SPINE, MAIN],
    )
    assert answered > 1, "the columns are read over different sets only where several answered"
    printed = popover(
        client, f"/session/{SPINE}/thread/{MAIN}/turn/{turn_id}", f"{Kind.TURN}:{turn_id}"
    )
    # The counts: the cache the last call read, what it sent, and what it said back.
    assert sum(tokens(printed, name) for name in ("cached", "new_input", "output")) == tokens(
        printed, "fill"
    )
    # The dollars: to the cent, because each is rounded before it is printed and the total is
    # rounded off the store's own sum rather than off these three.
    dollars = [amount(printed[name]) for name in CHARGES]
    assert round(sum(dollars), 2) == round(amount(printed["cost_usd"]), 2)
    # And the line that says the dollars cover more calls than the counts do.
    (made,) = one(
        store,
        "SELECT count(*) FROM live_api_calls WHERE session_id = ? AND source = ? AND turn_id = ?",
        [SPINE, MAIN, turn_id],
    )
    assert printed["api_calls"] == f"{made:,}"


def test_the_popovers_placement_rides_a_file_the_policy_allows(client: TestClient) -> None:
    """Where a popover stands and where the NavTree opens are a script's, and it is a real file.

    The stylesheet places the popover's left edge and can do nothing about its top, which
    follows the row a reader is pointing at; nothing in CSS scrolls the selected row into view
    either. Both are `static/nav-tree.js`, which is a file because `app.CSP` allows no inline
    script — so what a served page can prove is that the page asks for it, that it arrives, and
    that no page carries a line of script of its own.
    """
    answer = client.get("/static/nav-tree.js")
    assert answer.status_code == 200
    assert "javascript" in answer.headers["content-type"]
    page = client.get(f"/session/{SPINE}").text
    assert '<script src="/static/nav-tree.js"' in page
    # Every script on the page is a `src` with an empty body, and no attribute holds a handler.
    bodies = re.findall(r"<script[^>]*>(.*?)</script>", page, re.DOTALL)
    assert all(not body.strip() for body in bodies)
    assert not re.search(r"<script(?![^>]*\ssrc=)", page)
    assert not re.search(r"\son[a-z]+=", page)


def test_a_tool_call_says_what_it_gave_back_and_what_was_asked_beside_it(
    client: TestClient, store: duckdb.DuckDBPyConnection
) -> None:
    """A tool call carries no usage, so its popover is a size and the company it kept.

    Its tokens are its api call's (`docs/schema.md`), which is why there is no window and no
    price here: either one would charge everything a call did to one of the things it did.
    """
    printed = popover(
        client,
        f"/session/{FORK_ORIGIN}/thread/{FORK_ORIGIN_RUN}/tool/{DENSE_TOOL}",
        f"{Kind.TOOL}:{DENSE_TOOL}",
    )
    result_chars, api_call_id = one(
        store,
        "SELECT length(result), api_call_id FROM live_tool_calls"
        " WHERE session_id = ? AND source = ? AND id = ?",
        [FORK_ORIGIN, FORK_ORIGIN_RUN, DENSE_TOOL],
    )
    assert printed["result_chars"] == f"{result_chars:,}"
    # And none of the numbers a tool call has no business printing.
    assert not {"fill", "window", "cost_usd"} & set(printed)
    # The other tool calls the same api call made, named the way every other surface names one:
    # the glyph that stands for the tool, then the field that tells two of its calls apart.
    # Restated here rather than read through the registry — an oracle that imported it would
    # agree with whatever it said. Every sibling of this one is a `Read`.
    beside = [
        f"📖 {path}"
        for (path,) in store.execute(
            "SELECT json_extract_string(t.input, '$.file_path') FROM live_tool_calls t"
            " WHERE t.session_id = ? AND t.source = ? AND t.api_call_id = ? AND t.id <> ?"
            ' ORDER BY t."index"',
            [FORK_ORIGIN, FORK_ORIGIN_RUN, api_call_id, DENSE_TOOL],
        ).fetchall()
    ]
    assert beside, "the fixture's dense call is meant to have made more than one tool call"
    # Named exactly, and in the order the api call asked for them: redaction flattens most of
    # these titles to one word, so anything less than an exact match would pass on a popover
    # that named the wrong calls.
    assert printed["siblings"] == ", ".join(beside[:5])

    # And the one recorded api call that asked for two different tools at once, which is what
    # says the list is named per row rather than by whatever the first row was: `SPINE`'s tool
    # search was made beside a `Bash` call, so each of the two popovers names the other under
    # its own glyph (`tests/fixtures/spine/README.md`).
    searched = popover(
        client,
        f"/session/{SPINE}/thread/{MAIN}/tool/{SEARCH_TOOL}",
        f"{Kind.TOOL}:{SEARCH_TOOL}",
    )
    # A long command arrives at the width a header's list is read at, so it is a head.
    assert searched["siblings"].startswith("⚡ ls -la ")
    ran = popover(
        client,
        f"/session/{SPINE}/thread/{MAIN}/tool/{SEARCH_BASH_TOOL}",
        f"{Kind.TOOL}:{SEARCH_BASH_TOOL}",
    )
    assert ran["siblings"] == "🧰 select:PushNotification"


def test_a_turn_that_compacted_says_the_window_it_gave_back(plant: Planter) -> None:
    """A compaction inside a turn leaves the window below where the turn before it stood.

    The NavTree clamps that at nothing, because a bar has no way to draw a negative tip — so the
    real delta is the popover's alone, and a popover that clamped too would print a turn that
    dropped thirty thousand tokens as one that added none.

    INVENTED number, recorded shape: no session in the fixture corpus compacted mid-turn, so
    the drop is planted onto a recorded turn by cutting the cache its calls read. Everything
    around it — the thread, the order of its turns, the calls under them — is what the
    transcript recorded.
    """
    keys = [SPINE, MAIN]
    planted = plant(
        (
            "UPDATE api_calls SET cache_read_tokens = 100, cache_creation_tokens = 50,"
            " input_tokens = 10, output_tokens = 10"
            " WHERE session_id = ? AND source = ? AND turn_id ="
            '  (SELECT id FROM turns WHERE session_id = ? AND source = ? ORDER BY "index"'
            "   LIMIT 1 OFFSET 2)",
            [*keys, *keys],
        ),
    )
    # The spine's first turn never reached a model, so the pair to compare is the two after it.
    first, second = _turns(planted, SPINE, MAIN)[1:3]
    with TestClient(build_app(planted)) as client:
        before = popover(
            client, f"/session/{SPINE}/thread/{MAIN}/turn/{first}", f"{Kind.TURN}:{first}"
        )
        after = popover(
            client, f"/session/{SPINE}/thread/{MAIN}/turn/{second}", f"{Kind.TURN}:{second}"
        )
        page = client.get(f"/session/{SPINE}").text
    assert tokens(after, "fill") < tokens(before, "fill"), "the plant is meant to drop the window"
    assert after["added"] == f"{tokens(after, 'fill') - tokens(before, 'fill'):+,}"
    assert after["added"].startswith("-")
    # And the row the popover opened from draws that same turn with no band of its own: the
    # edge its growth begins at is held up at the fill, because a band has no way to run
    # backwards. This is the one place the two seams are meant to disagree — the tree clamps
    # where the popover prints the drop.
    drawn = bar(page, f"{Kind.TURN}:{second}")
    assert drawn.fill == step(tokens(after, "fill"), after["model"])
    assert drawn.prior == drawn.fill, drawn


def test_a_turn_is_measured_against_the_last_turn_that_answered(
    store: duckdb.DuckDBPyConnection, plant: Planter
) -> None:
    """A turn a model never answered is stepped over, not counted as an empty window.

    What a turn added is the window it left less the window the turn before it left — and a
    turn Claude Code answered with a placeholder left none at all (`docs/schema.md`). Neither
    seam may read that nothing as a floor: a turn measured against it would read as having
    built the whole window from scratch, and the thread would look like it started over every
    time the reader interrupted it.

    INVENTED arrangement of recorded rows: the corpus's one silent turn sits at the end of its
    thread, so the spine's last two turns trade calls — the model's answers move to the last
    turn, and the interrupt to the turn before it.
    """
    turns = [
        turn
        for (turn,) in store.execute(
            'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
            [SPINE, MAIN],
        ).fetchall()
    ]
    stood_at, quiet, last = turns[-3:]
    assert reached(store, SPINE, MAIN, stood_at), "the turn the delta reaches back to answered"
    assert not reached(store, SPINE, MAIN, last), "the spine's last turn holds the interrupt"
    # Where the two turns stood before the swap: the answers land on `last`, and the window
    # they left is measured against the turn two places behind it.
    stood = tokens(held(store, SPINE, MAIN, extra=f"AND turn_id = '{stood_at}'"), "fill")
    moved = held(store, SPINE, MAIN, extra=f"AND turn_id = '{quiet}'")
    keys = [SPINE, MAIN]
    planted = plant(
        (
            "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ?"
            " AND turn_id = ? AND NOT synthetic",
            [last, *keys, quiet],
        ),
        (
            "UPDATE api_calls SET turn_id = ? WHERE session_id = ? AND source = ? AND synthetic",
            [quiet, *keys],
        ),
    )
    with TestClient(build_app(planted)) as swapped:
        printed = popover(
            swapped, f"/session/{SPINE}/thread/{MAIN}/turn/{last}", f"{Kind.TURN}:{last}"
        )
        silent = popover(
            swapped, f"/session/{SPINE}/thread/{MAIN}/turn/{quiet}", f"{Kind.TURN}:{quiet}"
        )
        page = swapped.get(f"/session/{SPINE}").text
    # The turn holds the window its own calls left...
    assert printed | moved == printed
    # ...and what it added is measured over the interrupted turn, back to the last answer.
    assert printed["added"] == f"{tokens(printed, 'fill') - stood:+,}"
    # The row says the same thing as an edge rather than as a delta: its growth begins where
    # the turn that answered left the window, and never at the base band under it.
    drawn = bar(page, f"{Kind.TURN}:{last}")
    assert drawn.fill == step(tokens(printed, "fill"), moved["model"])
    assert drawn.prior == max(step(stood, moved["model"]) or 0, drawn.base or 0), drawn
    # The interrupted turn itself says neither number at either seam, which is what makes the
    # delta above a step over something rather than a step from it.
    assert silent["fill"] == ABSENT
    assert silent["added"] == ABSENT
    assert bar(page, f"{Kind.TURN}:{quiet}") == Bar(None, None, None)


def test_a_model_we_hold_no_window_for_says_so_rather_than_scaling_to_a_guess(
    plant: Planter,
) -> None:
    """An unknown window is stated, and the token counts print beside it anyway.

    A `[1m]` session names its base model in `message.model`, so a window larger than the
    table's is invisible to it (`extract/pricing.py`). The tokens are still the store's, and a
    popover that withheld them for want of a scale would drop the honest numbers it has.
    """
    planted = plant(
        # Cost goes with the model: `compute_cost` answers None for a model the table lacks,
        # so the exporter would have stored no cost for these calls either (`extract/pricing.py`).
        (
            "UPDATE api_calls SET model = 'claude-mythos-9', cost_usd = NULL WHERE session_id = ?",
            [SPINE],
        )
    )
    with TestClient(build_app(planted)) as client:
        printed = popover(client, f"/session/{SPINE}", f"{Kind.SESSION}:{SPINE}")
    assert printed["window"] == "unknown"
    assert int(printed["fill"].replace(",", "")) > 0
    # A model our price table lacks shows no legend rather than four zeroes, and the count of
    # what went unpriced is what says why.
    assert [name for name in CHARGES if name in printed] == []
    assert printed["unpriced_api_calls"] == printed["api_calls"]


def test_a_row_fetches_its_numbers_when_a_pointer_arrives_and_when_a_key_does(
    client: TestClient,
) -> None:
    """Hover and keyboard reach the same fetch, once per row, and the row's link is untouched.

    The trigger listens on the row — `focusin` bubbles where `focus` does not, so a trigger on
    the row hears the link inside it being tabbed to — but it is *carried* by a sibling of that
    link. htmx inherits its attributes down the NavTree, so the overrides a popover needs would be
    inherited by the link if they sat on the row itself, and a click would swap a popover's
    markup where the pane belongs. The last assertion here is that trap.
    """
    page = client.get(f"/session/{SPINE}").text
    key = f"{Kind.SESSION}:{SPINE}"
    (trigger,) = inside(page, "data-nav-tree", key, "hx-trigger")
    pointer, keyboard = trigger.split(", ")
    # Heard on the row, once apiece: the popover is markup that stays, and a second fetch
    # would stack another under the first.
    assert pointer.startswith("mouseenter from:closest li once")
    assert keyboard == "focusin from:closest li once"
    # Delayed on the pointer alone, so running one down the NavTree does not fetch every row it
    # crossed. A key press is deliberate and waits for nothing.
    assert re.search(r"delay:\d+m?s", pointer)
    wiring = dict(wired(page, "data-nav-tree"))
    fetched = {
        row: at for row, at in wired(page, "data-nav-tree") if at["hx-get"].startswith(NUMBERS_URL)
    }
    assert fetched[key]["hx-get"] == f"{NUMBERS_URL}/session/{SPINE}"
    assert fetched[key]["hx-target"] == "this"
    assert fetched[key]["hx-swap"] == "beforeend"
    assert fetched[key]["hx-push-url"] == "false"
    # A pane's own selectors would take the popover apart, so both are unset.
    assert fetched[key]["hx-select"] == "unset"
    assert fetched[key]["hx-select-oob"] == "unset"
    # And only the kinds that have numbers carry one — every kind that stands for a row of the
    # store, which is all of them but the two buckets.
    assert {row.split(":")[0] for row in fetched} <= {
        Kind.SESSION,
        Kind.TURN,
        Kind.RUN,
        Kind.CALL,
        Kind.TOOL,
        Kind.COMPACTION,
    }
    # The link a row is still a link: it swaps the pane out of `#nav-tree-rows`'s own wiring, and
    # nothing the popover wrote reached it.
    assert wiring[key]["hx-target"] == "#reading-pane"
    assert wiring[key]["hx-select"] == "#reading-pane"


def test_a_kind_with_no_numbers_is_a_route_that_answers_nothing(client: TestClient) -> None:
    """A bucket has nothing to print, so the route 404s rather than serving an empty popover.

    A bucket is a place rather than a node — it stands for no row of the store — so there is
    nothing to count under it. Every kind that does stand for a row now carries a popover, the
    compaction included: what it shows is `tests/view/test_numbers__compaction.py`.
    """
    for path in (
        f"/session/{ANCESTOR}/thread/{MAIN}/{Kind.UNATTRIBUTED}/{MAIN}",
        f"/session/{ANCESTOR}/thread/{MAIN}/{Kind.UNATTACHED}/{ANCESTOR}",
    ):
        assert client.get(f"{NUMBERS_URL}{path}").status_code == 404


def test_a_popover_is_hidden_until_its_row_is_pointed_at_or_tabbed_into(
    client: TestClient,
) -> None:
    """One stylesheet rule shows it, and it covers the keyboard as well as the pointer.

    `:focus-within` rather than `:focus`, because the row itself is not focusable — and it is
    also what holds the popover open while a reader selects the numbers out of it, which is
    the copy affordance a pin would otherwise have to be built for.
    """
    style = client.get("/static/style.css").text
    assert re.search(r"\.popover\s*\{[^{}]*display: none", style)
    # Fixed rather than absolute: `#nav-tree` scrolls under `overflow: auto`, which clips anything
    # positioned inside it — and a popover of numbers is wider than the NavTree.
    assert re.search(r"\.popover\s*\{[^{}]*position: fixed", style)
    # And it stands where the reading pane does: the NavTree's width, the grip between the columns,
    # and the gutter on either side of it. Measured from the same `--grip-width` the grip is
    # drawn at, so a popover cannot come to rest on top of the handle a reader drags.
    left = re.search(r"\.popover\s*\{[^{}]*left:([^;]*);", style)
    assert left is not None, "the popover names no left edge"
    assert "--nav-tree-width" in left.group(1) and "--grip-width" in left.group(1), left.group(1)
    assert re.search(r"#nav-tree-grip\s*\{[^{}]*width: var\(--grip-width\)", style)
    shown = [
        selector
        for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", style)
        if ".popover" in selector and "display: block" in body
    ]
    assert shown, "nothing shows the popover"
    assert all(":hover" in selector and ":focus-within" in selector for selector in shown), shown


def _turns(db_path: Path, session_id: str, source: str) -> list[str]:
    """One thread's turn ids in the order they were recorded, read off a planted store."""
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            turn
            for (turn,) in connection.execute(
                'SELECT id FROM live_turns WHERE session_id = ? AND source = ? ORDER BY "index"',
                [session_id, source],
            ).fetchall()
        ]
    finally:
        connection.close()
