"""Claude Code's raw record shapes, described field by field with the recording behind each claim.

These models describe; they do not parse. The extractor still reads records as dicts, and
nothing here runs during an extract. What they carry is the meaning of every field
`docs/schema.md` ever stated, plus the fixture and Claude Code version that proves it — which
`tools/gen_schema.py` renders as that document's field tables.

Two rules hold the description honest:

- Every declared field carries a `description` and at least one `Cited`. A blank one crashes the
  generator rather than printing an empty cell
- Nothing is closed except the registries in `registry`. Every model allows extra keys,
  because Claude Code adds fields without notice and a validation error would be a worse answer
  than an undocumented field

The modules, in dependency order: `registry` (every type, subtype, block and tag the parser
knows), `evidence` (what proves a claim), `blocks` (content blocks and the messages holding
them), `shapes` (the record models), and `field_tables` (the walk that turns all of it into
table rows).
"""
