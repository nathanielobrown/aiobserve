"""The repo's own generators: the tables the docs cite, written from the code that owns them.

Each module here exposes `generate()` and a `main()` that prints it, and is run by an
`aigarden:cog` block in the document that carries the table. Repo tooling rather than shipped
code — nothing under `src/hyphae/` imports any of it.
"""
