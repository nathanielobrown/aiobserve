"""What the extractor refuses to guess at: a record it cannot read, a directory it cannot.

Claude Code owns both the transcript schema and the layout on disk and changes either without
notice, so anything unrecognised stops the run (`.claude/rules/python.md`). Two classes rather
than one because the response differs: a schema error sends a reader to `docs/schema.md` and a
record model, a layout error to the session directory itself.

No error here carries record content: transcripts are private, and these messages reach logs.
"""

from pydantic import BaseModel, ValidationError


class ExtractionError(Exception):
    """A recorded session held something this extractor will not guess at."""


class TranscriptSchemaError(ExtractionError):
    """A transcript held a shape this parser does not know."""


class SessionLayoutError(ExtractionError):
    """A session's directory holds a file we cannot place, or is missing one we expected."""


def invalid_record(
    error: ValidationError, model: type[BaseModel], session_id: str, line_no: int
) -> TranscriptSchemaError:
    """A pydantic failure as an error a reader can act on: which model, which field, and where.

    Privacy is why this exists rather than `str(error)`: only a fault's location and message are
    copied, so the transcript value that failed validation cannot reach a log. The two `errors`
    arguments say the same thing a second time, at the source.
    """
    faults = "; ".join(
        f"{'.'.join(str(part) for part in fault['loc'])}: {fault['msg']}"
        for fault in error.errors(include_input=False, include_url=False)
    )
    return TranscriptSchemaError(
        f"{model.__name__} in session {session_id}, line {line_no} — {faults}"
    )
