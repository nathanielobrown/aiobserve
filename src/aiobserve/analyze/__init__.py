"""The analysis layer: a versioned SQL library and the runner that binds and cites it.

Read-only by construction — the store is opened read-only and no query file writes. The
process these queries serve is `plans/mycelia-analysis/design.md`.
"""
