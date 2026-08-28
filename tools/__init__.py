"""The repo's own generators: what the code already owns, written back out for another reader.

Each module here exposes `generate()` and a `main()`. Most print a table an `aigarden:cog` block
splices into the document that carries it; `gen_e2e_routes` writes a file another runtime loads
instead. Repo tooling rather than shipped code — nothing under `src/hyphae/` imports any of it.
"""
