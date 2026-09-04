"""The repo's own tooling: what the code already owns written back out, and what runs the gates.

Each `gen_*` module exposes `generate()` and a `main()`. Most print a table an `aigarden:cog`
block splices into the document that carries it; `gen_e2e_routes` writes a file another runtime
loads instead. `gate.py` generates nothing — it wraps one `mise.toml` gate and reports whether
it passed. Repo tooling rather than shipped code: nothing under `src/hyphae/` imports any of it.
"""
