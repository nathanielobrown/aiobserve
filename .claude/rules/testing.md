---
description: Unit testing
paths:
  - "tests/**/*.py"
---

# Organization

Test files mirror the package layout: tests for `src/hyphae/extract/claude_code.py` live in `tests/extract/test_claude_code.py`. Keep test structure and names consistent with the code as you refactor.

Split a long file by **behavior or topic**, not by private sub-module — we test the public interface, so topics track user-facing feature areas. Name the pieces `test_<unit>__<topic>.py`.

Fixtures go in `conftest.py` — pytest resolves one by name, so a fixture imported from anywhere else reads as an unused import and ruff deletes it. Everything else a split leaves shared — measurements, readers, a fake several files drive — may be a plain module beside them, named for what it holds (`tests/view/budgets.py`, `tests/enrich/fake_cli.py`). Say in its docstring why it is not the conftest. Two reasons recur: a process outside pytest imports it too (the gallery reads `tests/view/scenarios.py`), or the readers of one subject belong in one place a reader can open.

# Fixtures: use real sessions

This project reads data another program wrote, so an invented fixture proves nothing about the real thing. **Prefer a recorded session.**

- Save real transcript samples as files under `tests/**/fixtures/`, never as concatenated strings in the test
- **Redact before committing.** A transcript carries whatever the agent read — file contents, paths, tokens. Trim it to the records the test needs and scrub the rest.
- Record which Claude Code version produced a fixture, in a sidecar or the file's own name. A parser test passes against a schema, not against a file.
- Invented data is allowed where the shape is the whole point (a malformed record, an edge case no real session contains). Say so in a comment — an unlabeled invented fixture reads as evidence.

# Comments

Tell a story. Use `...` to weave code through the comments — read the comments end to end as one sentence, with code spliced in mid-clause. Comments can sit between statements or **inline within data structures**, wherever the story flows best.

- **Declarative voice** ("If a session ends mid-tool-call…"), not imperative
- **Match granularity to test complexity.** One comment per logical step, not per statement. Simple test, simple narration.
- **Skip the scaffold for trivial tests** (one assertion, obvious intent)

```python
def test_something():
    """`something` returns exceptional results."""
    # If <some condition>...
    <setup>
    # ...when <some action happens>...
    <action>
    # ...then the result has the expected shape...
    assert result == ExpectedShape(
        ...,
        # ...with <some salient detail>...
        salient_field=value,
    )
    # ...and <some other expected result>.
    <more assertions>
```

# Docstrings

State plainly **what the test demonstrates**, in user-facing terms — not internal mechanics. One sentence is usually enough; the function name already names the test.

**Do not repeat the test name.**

```python
# ❌ Restates the name.
def test_subagent_spans_nest_under_parent():
    """Tests that subagent spans nest under the parent."""

# ❌ Explains internals — those belong in the code, not the test docstring.
def test_subagent_spans_nest_under_parent():
    """A sidechain nests because `_link_parent` reads `parentUuid` before `_index`."""

# ✅ Plain-English statement of behavior.
def test_subagent_spans_nest_under_parent():
    """A subagent's spans hang off the Agent tool call that spawned them, not off the session root."""
```

For `@pytest.mark.parametrize`, the docstring describes the invariant checked **across all cases**, not any individual case.

# Assertions

When verifying a complex object (dict, list, set, dataclass, Pydantic model), prefer comparing against the WHOLE object, so the test verifies every field and shows the output to its reader.

## Lifting fields

Lift a field from actual into expected when it's **non-deterministic** (UUID, clock) or **deterministic but verbose** (span ids, absolute paths):

```python
expected = Session(id=actual.id, path=actual.path, ...)
assert actual == expected
```

For non-determinism specifically, prefer designing a seam (an id factory, an injected clock) so production code and tests share a deterministic path.

Avoid `mock.ANY` for shape-laden fields — it accepts wrong types silently. If you need a sentinel, use a type-checked one.

# Waiting

Every wait carries a deadline and names what it waited for. A wait with no ceiling doesn't fail when the awaited thing never arrives — it hangs, burns the whole job budget, and prints no failure line. Never hand-roll a `while True`, a bare `Event.wait()`, or a `subprocess.run` with no `timeout=`.

The `timeout` setting in `pyproject.toml` is a last-resort backstop, not the mechanism. A trip means nothing here was bounded.

# Speed

Keep the suite fast enough to run on every edit. A test that has *earned* its time — a real subprocess, a live backend call, a large corpus — gets `@pytest.mark.slow` with a why-comment. Prefer speeding a test up over marking it.

Every run prints a `--durations=10` footer. A pure-parsing test landing there is usually doing accidental I/O or carrying an oversized fixture; fix that rather than accept it.

Never let a test hit a real telemetry backend by default. Backend calls go behind a marker and an explicit env var, so a bare `mise run test` works offline.

# Mutation testing

A green suite proves the tests ran, not that they would notice the code being wrong. `mise run mutate` answers the second question: it breaks one expression at a time and reports which breaks no test caught.

- With no argument, it scopes to the source files this branch changed against `main`. On `main` itself, that scope is empty, so it says so and exits 1 — a mutation run that tested nothing must not read as a pass
- Pass mutant globs to scope it yourself: `mise run mutate 'hyphae.view.format.*'`. A mutant is named `<module path>.x_<function>__mutmut_<n>`, and a method's `<module path>.xǁ<Class>ǁ<method>__mutmut_<n>` — mutmut mangles the name it wraps, so a glob written against the plain function name matches nothing
- 🎉 is a killed mutant, 🙁 a survivor. Read the survivors with `uv run mutmut browse`
- Out of `check`, because it re-runs the covering tests once per mutant

**Every run is cold and serial, so the number reproduces.** The task deletes `mutants/` first, because mutmut caches verdicts there, and passes `--max-children 1`. Run in parallel the same cold scope reported three different survivor counts; serial it reports the same one every time, and always the largest — concurrency scores kills the suite did not earn. A survivor count from a parallel run is a hypothesis. Both cost wall time, which is the price of quoting the number.

A survivor is a claim no leaf makes. Usually the fix is an assertion, not a new test — but a survivor over a branch nothing can reach is a finding about the code, not the suite.
