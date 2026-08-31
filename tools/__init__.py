"""The repo's own generators: what the code already owns, written back out for another reader.

Each module here exposes `generate()` and a `main()`. Most print a table an `aigarden:cog` block
splices into the document that carries it; the rest write a file another runtime loads instead,
each with a test beside it that regenerates and compares bytes. Repo tooling rather than shipped
code — nothing under `src/hyphae/` imports any of it.
"""
