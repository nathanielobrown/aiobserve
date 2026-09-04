---
description: Python house style
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# Python

- Use type annotations everywhere, including in test code
- Prefer `NamedTuple` over an anonymous `tuple[...]` return type when a function returns several named values — field names document the slots at the call site without positional unpacking
- Prefer **enums** (`enum.StrEnum` for string-valued sets) over `Literal` aliases or bare magic strings
- Imports at the top of the file. Relative imports are banned (ruff `TID`); import from `hyphae.<module>`.
- If a lint rule conflicts with a load-bearing pattern, **disable the rule** in `pyproject.toml` rather than scattering `# noqa`. Per-line `noqa` is only correct when one site genuinely deviates.

## Parsing telemetry

The parsing layer is where a schema we don't own meets code that assumes one, so it's the place where fail-fast matters most:

- **Crash on an unrecognized shape.** A record whose `type` we don't handle is a schema change we need to see, not a row to skip. Silently dropping it turns schema drift into a quietly wrong count months later.
- **Read a record through its model, never as a dict.** `read_lines` validates every line into a record model, so a reader takes attributes and narrows kinds with `isinstance` — `isinstance(line.record, UserRecord)`, not `record["type"] == "user"`. A leaf in `tests/extract/test_records.py` pins it: nothing under `src/hyphae/extract/` indexes a record by string.
- **Never default a field that carries meaning.** Every model field is optional, so a reader that cannot build its row without one passes it through `transcript.required`, which crashes naming the session, line, record kind and field. `record.costUSD or 0.0` reports a free session; `required(record.costUSD, …)` reports a schema change. Default only where absence is a documented, meaningful state — and say which in a comment.
- **Record what you relied on.** A field a reader opens is declared on its record model in `src/hyphae/extract/records/`, with the recording that shows it, so the parser cannot rely on a field `docs/schema.md` does not print. A field no model declares crashes the suite and is tallied after `hp extract`; the walk that finds it stops at a model marked `OPAQUE`, whose interior nobody claims.

## Documenting model members

Each model kind has one mechanism for saying what a member means:

| Kind | Mechanism |
| --- | --- |
| Pydantic field | `Field(description=...)` |
| NamedTuple / dataclass / enum member | a `#` comment on the line above |

Write one only when it carries what the name cannot: units, a range, an invariant, what null or empty means, which of several plausible readings is right, a trap. `user_id: "the user id"` is worse than nothing — visiting a member and adding nothing is a correct outcome.

The record models in `src/hyphae/extract/records/` are the exception: they exist to say what Claude Code's fields mean, so every field there carries a `description` and the `Cited` recording behind it, and one that carries neither fails the schema generator.

Prefer the class docstring when the fact is about the model as a whole.
