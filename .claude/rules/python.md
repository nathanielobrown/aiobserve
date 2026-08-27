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
- **Never default a field that carries meaning.** `record.get("costUSD", 0.0)` reports a free session; `record["costUSD"]` reports a schema change. Use `.get` only where absence is a documented, meaningful state — and say which in a comment.
- Record what you relied on: when a parser depends on a field's shape, declare that field on its record model in `src/hyphae/extract/records/`, with the recording that shows it — `docs/schema.md` prints what the models carry

## Documenting model members

Each model kind has one mechanism for saying what a member means:

| Kind | Mechanism |
| --- | --- |
| Pydantic field | `Field(description=...)` |
| NamedTuple / dataclass / enum member | a `#` comment on the line above |

Write one only when it carries what the name cannot: units, a range, an invariant, what null or empty means, which of several plausible readings is right, a trap. `user_id: "the user id"` is worse than nothing — visiting a member and adding nothing is a correct outcome.

The record models in `src/hyphae/extract/records/` are the exception: they exist to say what Claude Code's fields mean, so every field there carries a `description` and the `Cited` recording behind it, and one that carries neither fails the schema generator.

Prefer the class docstring when the fact is about the model as a whole.
