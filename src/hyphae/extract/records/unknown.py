"""Fields a modelled record carried that no model declares.

The models are a claim of completeness over the shapes Claude Code owns: the envelope of every
record kind a reader opens, and every container inside it that has a model. This walk is what
holds them to it. It stops at an opaque model, because a tool's own report and an archived kind
are shapes nobody here claims (`plans/records-as-parser/design.md`).

Strict in tests, so an undeclared field stops the run where a person is looking; a tally in
production, so a field Claude Code added yesterday is information rather than a stopped extract.
"""

from dataclasses import dataclass, field
from typing import NamedTuple

from hyphae.extract.errors import TranscriptSchemaError
from hyphae.extract.records.blocks import Kinded
from hyphae.extract.records.evidence import Described
from hyphae.extract.records.shapes import Record


class Sighting(NamedTuple):
    """Where one undeclared field was first seen."""

    session_id: str
    line_no: int


@dataclass
class _Tally:
    """What one undeclared field path has been seen doing."""

    first: Sighting
    # Distinct sessions, because a field Claude Code writes on every record would otherwise
    # report a count of the corpus rather than of the sessions that carry it.
    sessions: set[str] = field(default_factory=set)


class UnknownFields:
    """Fields a modelled record carried that no model declares, over one extraction run.

    One per extractor instance: the tally aggregates across the sessions that run refreshes,
    which is what makes a session count meaningful.
    """

    def __init__(self, *, strict: bool) -> None:
        # Strict raises on the first sighting; lax collects. There is no sensible default:
        # the caller knows whether it is a test or an extract.
        self.strict = strict
        self._tally: dict[str, _Tally] = {}

    def note(self, record: Record, session_id: str, line_no: int) -> None:
        """Walk one validated record for keys no model declares."""
        self._walk(record, record.type, session_id, line_no)

    def report(self) -> str:
        """One line per undeclared path, or the empty string when nothing was seen."""
        return "\n".join(
            f"{path}: first in session {tally.first.session_id} line {tally.first.line_no}, "
            f"{len(tally.sessions)} session(s)"
            for path, tally in sorted(self._tally.items())
        )

    def _walk(self, model: Described, path: str, session_id: str, line_no: int) -> None:
        # An opaque model declares the fields a reader opens and claims nothing about the rest,
        # so neither its extras nor anything under them is this walk's business.
        if model.OPAQUE:
            return
        for name in model.model_extra or {}:
            self._seen(f"{path}.{name}", session_id, line_no)
        # A dict-typed field is a leaf by the same rule: the declaration says Claude Code writes
        # an object there, and its interior is a further claim nobody has read. Only a value that
        # became a model is descended into.
        for name, info in type(model).model_fields.items():
            value = getattr(model, name)
            step = f"{path}.{info.alias or name}"
            if isinstance(value, Described):
                self._walk(value, step, session_id, line_no)
            elif isinstance(value, list):
                self._blocks(value, step, session_id, line_no)

    def _blocks(self, values: list[object], path: str, session_id: str, line_no: int) -> None:
        """Every block of a content list, named by its kind rather than its position.

        The only lists of models a transcript holds are content lists, and the schema tables
        name a block's fields from the kind it carries — `message.content.tool_use.caller` —
        so two blocks of one kind report one path. A list of anything else holds no model and
        no claim, so it is a leaf.
        """
        for value in values:
            if isinstance(value, Kinded):
                self._walk(value, f"{path}.{value.BLOCK.value}", session_id, line_no)

    def _seen(self, path: str, session_id: str, line_no: int) -> None:
        if self.strict:
            raise TranscriptSchemaError(
                f"Undeclared field `{path}` in session {session_id}, line {line_no}"
            )
        tally = self._tally.get(path)
        if tally is None:
            tally = _Tally(first=Sighting(session_id, line_no))
            self._tally[path] = tally
        tally.sessions.add(session_id)
