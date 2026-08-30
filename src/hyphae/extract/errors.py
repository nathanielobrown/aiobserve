"""What the extractor refuses to guess at: a record it cannot read, a directory it cannot.

Claude Code owns both the transcript schema and the layout on disk and changes either without
notice, so anything unrecognised stops the run (`.claude/rules/python.md`). Two classes rather
than one because the response differs: a schema error sends a reader to `docs/schema.md` and a
record model, a layout error to the session directory itself.

No error here carries record content: transcripts are private, and these messages reach logs.
"""


class ExtractionError(Exception):
    """A recorded session held something this extractor will not guess at."""


class TranscriptSchemaError(ExtractionError):
    """A transcript held a shape this parser does not know."""


class SessionLayoutError(ExtractionError):
    """A session's directory holds a file we cannot place, or is missing one we expected."""
