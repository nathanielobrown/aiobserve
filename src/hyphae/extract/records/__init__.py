"""Claude Code's raw record shapes, described field by field with the recording behind each claim.

These models are the parser's types: `read_lines` validates every transcript line into one, and
every reader downstream takes attributes off it (`extract/transcript.py`). Beside the shape they
carry the meaning of every field `docs/schema.md` ever stated, plus the fixture and Claude Code
version that proves it — which `tools/gen_schema.py` renders as that document's field tables.

Two rules hold the description honest:

- Every declared field carries a `description` and at least one `Cited`. A blank one crashes the
  generator rather than printing an empty cell
- Nothing is closed except the registries in `registry`. Every model allows extra keys, because
  Claude Code adds fields without notice and a stopped extract would be a worse answer than an
  undocumented field — `unknown` walks those extras, so an undeclared field is reported rather
  than unnoticed

The modules, in dependency order: `registry` (every type, subtype, block and tag the parser
knows), `evidence` (what proves a claim), `blocks` (one model per content-block kind),
`messages` (the messages whose content lists dispatch to them), `base` (the ladder of mixins),
one module per record family — `conversation`, `system`, `bookkeeping` — then `shapes` (the
roster and the dispatch onto it) and `field_tables` (the walk that turns all of it into table
rows).
"""
