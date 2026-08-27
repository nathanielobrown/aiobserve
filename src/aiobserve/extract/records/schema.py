"""How the models become `docs/schema.md`'s rows: one walk, one row per documented field.

`documentation()` is the whole interface. It reads the models rather than any list, so a field
added to a model is a row and a field deleted is a row gone, with no second place to edit.
"""

from collections.abc import Iterator
from typing import Any, ForwardRef, NamedTuple, get_args

from aiobserve.extract.record_types import RecordType
from aiobserve.extract.records.blocks import Message
from aiobserve.extract.records.evidence import Among, Cited, Described, Step
from aiobserve.extract.records.shapes import RECORD_MODELS, Record


class Documentation(NamedTuple):
    """One row of a `docs/schema.md` field table, derived from the models."""

    # What the table prints in its Field column: the field under its container, as
    # `usage.cache_creation`. `_name` says which containers a row carries.
    path: str
    meaning: str
    evidence: tuple[Cited, ...]
    # Every record model that reaches this field. The Records column is `spell(carriers)`.
    carriers: tuple[type[Record], ...]
    # How to reach the field inside a record, which is what lets a test check the citation.
    locate: tuple[Step, ...]


# What the Records column says instead of naming all twelve.
EVERY_RECORD = "every record"


def spell(carriers: tuple[type[Record], ...]) -> tuple[str, ...]:
    """How the Records column names one field's carriers.

    Every record is said once; the system subtypes collapse to `system` when they all carry the
    field, and name themselves when only some do.
    """
    if set(carriers) == set(RECORD_MODELS):
        return (EVERY_RECORD,)
    system = {m for m in RECORD_MODELS if m.RECORD_TYPE is RecordType.SYSTEM}
    whole_system = system <= set(carriers)
    said: list[str] = []
    for model in RECORD_MODELS:
        if model not in carriers:
            continue
        if model in system and not whole_system and model.SUBTYPE is not None:
            name = f"{model.RECORD_TYPE.value} / {model.SUBTYPE.value}"
        else:
            name = model.RECORD_TYPE.value
        if name not in said:
            said.append(name)
    return tuple(said)


def _nested(annotation: Any) -> Iterator[type[Described]]:
    """Every described model an annotation can hold, unions and containers included."""
    if isinstance(annotation, type) and issubclass(annotation, Described):
        yield annotation
        return
    if isinstance(annotation, ForwardRef):
        raise TypeError(f"{annotation} was never resolved, so its fields would go undocumented")
    for argument in get_args(annotation):
        yield from _nested(argument)


def _prose(text: str | None) -> str:
    """One table cell: lines joined, indentation dropped, and no closing period.

    A block's meaning is its docstring, which ends in a period the way a docstring should; a
    field's is a `description`, which does not. The cells read the same either way.
    """
    joined = " ".join(line.strip() for line in (text or "").split("\n") if line.strip())
    return joined.removesuffix(".")


def _describe(
    model: type[Described], locate: tuple[Step, ...]
) -> Iterator[tuple[tuple[Step, ...], str, tuple[Cited, ...]]]:
    """Every documented field reachable from one model, with where it sits and what it claims."""
    for name, info in model.model_fields.items():
        here = (*locate, info.alias or name)
        evidence = tuple(item for item in info.metadata if isinstance(item, Cited))
        yield here, _prose(info.description), evidence
        for nested in _nested(info.annotation):
            yield from _describe(nested, here)
        if issubclass(model, Message) and name == "content":
            for block in model.BLOCKS:
                inside = (*here, Among(block.BLOCK))
                yield inside, _prose(block.__doc__), block.EVIDENCE
                yield from _describe(block, inside)


def _name(locate: tuple[Step, ...]) -> str:
    """The Field column's spelling: the field under its container, as `usage.cache_creation`.

    A field inside a block is spelled from the block — `advisor_tool_result.content.type` — because
    a block is identified by name rather than by position, and more than one holds a `content`.
    Outside a block the container is a record's own field, unique across the document.
    """
    last = locate[-1]
    if isinstance(last, Among):
        return last.kind.value
    blocks = [at for at, step in enumerate(locate) if isinstance(step, Among)]
    start = blocks[-1] if blocks else max(len(locate) - 2, 0)
    return ".".join(step.kind.value if isinstance(step, Among) else step for step in locate[start:])


def documentation(models: tuple[type[Record], ...] = RECORD_MODELS) -> tuple[Documentation, ...]:
    """Every field the models document, in walk order, each with the records that carry it.

    `models` defaults to the registry, which is the only answer a document wants; naming other
    models is how a test asks what a model that is not registered would print.

    One field reached from several records is one row: the meaning and the evidence must be the
    same object of thought, so a second declaration that says something different crashes here
    rather than printing two rows with one name.
    """
    rows: dict[str, Documentation] = {}
    for model in models:
        for locate, meaning, evidence in _describe(model, ()):
            path = _name(locate)
            row = rows.get(path)
            if row is None:
                rows[path] = Documentation(path, meaning, evidence, (model,), locate)
                continue
            if (row.meaning, row.evidence, row.locate) != (meaning, evidence, locate):
                raise ValueError(f"`{path}` is documented twice, and the two disagree")
            rows[path] = row._replace(carriers=(*row.carriers, model))
    return tuple(rows.values())
