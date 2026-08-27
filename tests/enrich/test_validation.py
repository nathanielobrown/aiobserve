"""What the enricher accepts back from the model, and how an item fails.

Every payload here is **invented**, and labelled as such at each call site: model output is
not transcript data, so there is no recorded session to draw it from, and a real credential
could never be committed.
"""

import pytest

from hyphae.enrich.taxonomy import (
    CATEGORY_DEFINITIONS,
    OUTCOME_DEFINITIONS,
    TAXONOMY_VERSION,
    Category,
    Outcome,
)
from hyphae.enrich.validation import Enrichment, FailureKind, InvalidOutput, validate


def payload(**overrides: object) -> dict[str, object]:
    """A well-formed model output (invented), with fields replaced per test."""
    return {
        "description": "Fixed a failing parser test and re-ran the suite.",
        "category": "fix_bug",
        "outcome": "completed",
        "friction": None,
    } | overrides


def test_every_taxonomy_member_validates() -> None:
    """The vocabulary the validator accepts is exactly the taxonomy's members, both ways."""
    # If every member is round-tripped through the validator as a raw string...
    accepted_categories = {validate(payload(category=str(member))).category for member in Category}
    accepted_outcomes = {validate(payload(outcome=str(member))).outcome for member in Outcome}
    # ...then the accepted set is the enum, exactly — a member added without a definition
    # cannot widen the vocabulary quietly, and one dropped from the enum cannot linger.
    assert accepted_categories == set(Category)
    assert accepted_outcomes == set(Outcome)
    # ...every member has the one-line definition the prompt is written from, since a member
    # the classifier is never told about is a member it will not use...
    assert set(CATEGORY_DEFINITIONS) == set(Category)
    assert set(OUTCOME_DEFINITIONS) == set(Outcome)
    # ...and the version rows are stamped with is a number they can be compared against.
    assert isinstance(TAXONOMY_VERSION, int)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # A plausible synonym of a real member, which is exactly what an open vocabulary
        # would fragment into...
        ("category", "refactoring"),
        # ...and an outcome the taxonomy does not carry.
        ("outcome", "succeeded"),
    ],
)
def test_out_of_vocabulary_value_fails_the_item(field: str, value: str) -> None:
    """A category or outcome outside the taxonomy fails the item instead of widening it."""
    with pytest.raises(InvalidOutput) as failure:
        validate(payload(**{field: value}))
    assert failure.value.kind is FailureKind.invalid_output
    # ...and the raised text names the field but not what the model said, since anything the
    # model wrote may be quoted from a private transcript.
    assert value not in str(failure.value)


@pytest.mark.parametrize(
    "secret",
    [
        # Invented credentials — obviously fake, in the shapes the screen knows.
        "sk-ant-api03-0000000000000000000000000000000000000000000000",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
        "ghp_0000000000000000000000000000000000",
    ],
)
def test_a_secret_shape_fails_the_item_without_repeating_it(secret: str) -> None:
    """A description carrying a credential shape fails, and the failure never repeats it.

    This screen is the one control between a credential sitting in a transcript and a
    description pasted into a committed report.
    """
    with pytest.raises(InvalidOutput) as failure:
        validate(payload(description=f"Rotated the key {secret} and re-deployed."))
    assert failure.value.kind is FailureKind.secret_shape
    assert secret not in str(failure.value)


def test_a_secret_in_the_friction_line_fails_too() -> None:
    """The screen covers every string the model wrote, not just the description."""
    with pytest.raises(InvalidOutput) as failure:
        validate(payload(friction="Retried after AKIAIOSFODNN7EXAMPLE was rejected."))
    assert failure.value.kind is FailureKind.secret_shape


def test_a_well_formed_output_validates_with_null_friction() -> None:
    """A model that reports no friction produces no friction, rather than an empty string."""
    # If the model reports no friction — as null, and as the empty string it sometimes
    # sends instead...
    for absent in (None, "  "):
        assert validate(payload(friction=absent)) == Enrichment(
            description="Fixed a failing parser test and re-ran the suite.",
            category=Category.fix_bug,
            outcome=Outcome.completed,
            # ...then friction stays absent, so `friction IS NULL` means what it says.
            friction=None,
        )


@pytest.mark.parametrize(
    "broken",
    [
        # A field the schema requires, missing...
        {"category": None},
        # ...a description of the wrong type...
        {"description": 12},
        # ...and one that is present but says nothing.
        {"description": "   "},
    ],
)
def test_a_malformed_output_fails_the_item(broken: dict[str, object]) -> None:
    """Output that does not fit the schema fails the item rather than writing a partial row."""
    payload_ = payload(**broken)
    if broken.get("category") is None:
        del payload_["category"]
    with pytest.raises(InvalidOutput) as failure:
        validate(payload_)
    assert failure.value.kind is FailureKind.invalid_output
