"""The output side: what a valid enrichment is, and how an item fails to produce one.

Nothing here ever repeats what the model wrote. The descriptions are derived from private
transcripts, so a failure record carries the item's key and a kind and has nowhere to put
prose — the crash summary is keys-only by construction rather than by discipline.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hyphae.enrich.taxonomy import Category, Outcome


class FailureKind(StrEnum):
    """Why an item produced no row. The crash summary groups by these."""

    # The CLI refused or errored on the request.
    api_error = "api_error"
    # The call was still running at the client's per-item deadline.
    timeout = "timeout"
    # The CLI's answer envelope was not the shape the client is pinned to. Only after the
    # round's canary has proved the shape once — the canary itself crashes the run.
    drift = "drift"
    # Never attempted: the client's breaker ended the round before this item was sent.
    aborted = "aborted"
    # The model answered, but not in the shape the output schema requires.
    invalid_output = "invalid_output"
    # The answer carried something shaped like a credential.
    secret_shape = "secret_shape"  # noqa: S105 — the name of a failure, not a credential


# Shapes that mean a credential leaked from a transcript into a description. A heuristic,
# not a guarantee (`plans/enrichment/design.md` books that as an open question) — but the
# instruction to describe rather than quote is not a control on its own.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # Anthropic and OpenAI style API keys
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub token
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained token
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API key
)


@dataclass(frozen=True)
class Enrichment:
    """One accepted model answer about one item."""

    # One or two sentences saying what the item did.
    description: str
    category: Category
    outcome: Outcome
    # One line naming visible struggle — retries, errors, backtracking. None when the
    # records show none, which is the common case.
    friction: str | None


@dataclass(frozen=True)
class ItemFailure:
    """An item that wrote no row, and why. Carries no model output — there is no field for it."""

    key: str
    kind: FailureKind


class InvalidOutput(Exception):
    """The model's answer was rejected. The message names the field, never its value."""

    def __init__(self, kind: FailureKind, reason: str) -> None:
        super().__init__(f"{kind}: {reason}")
        self.kind = kind


def validate(output: Mapping[str, Any]) -> Enrichment:
    """Turn one model answer into an `Enrichment`, or raise `InvalidOutput`.

    Raises rather than returns a failure so the caller, which holds the item's key, is the
    only thing that can build a failure record.
    """
    description = _required_text(output, "description")
    friction = _optional_text(output, "friction")
    for field, value in (("description", description), ("friction", friction)):
        if value is not None and any(shape.search(value) for shape in _SECRET_SHAPES):
            raise InvalidOutput(FailureKind.secret_shape, f"the {field} matched a credential shape")
    return Enrichment(
        description=description,
        category=_member(output, "category", Category),
        outcome=_member(output, "outcome", Outcome),
        friction=friction,
    )


def _required_text(output: Mapping[str, Any], field: str) -> str:
    value = output.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidOutput(FailureKind.invalid_output, f"{field} is missing or not text")
    return value.strip()


def _optional_text(output: Mapping[str, Any], field: str) -> str | None:
    """Absent, null, and blank all mean the same thing, and all become None."""
    value = output.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidOutput(FailureKind.invalid_output, f"{field} is not text")
    return value.strip() or None


def _member[T: StrEnum](output: Mapping[str, Any], field: str, vocabulary: type[T]) -> T:
    value = output.get(field)
    if not isinstance(value, str) or value not in set(vocabulary):
        raise InvalidOutput(FailureKind.invalid_output, f"{field} is not a member of the taxonomy")
    return vocabulary(value)
