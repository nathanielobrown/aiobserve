"""The enrichment layer: what a model wrote about each run, turn and session in the store.

One pass reads the telemetry bottom-up and writes a description, a category, an outcome and
whatever friction it saw beside every item (`docs/enrichment.md`).
"""
