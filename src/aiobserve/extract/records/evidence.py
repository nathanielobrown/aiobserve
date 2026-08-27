"""What proves a claim about Claude Code's format, and the base every model here shares.

A field's meaning is worth nothing without the recording behind it, so `Cited` travels beside
every declaration and `tools/gen_schema.py` refuses to print a field that carries none.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from aiobserve.extract.record_types import ContentBlock

# The fixtures the claims below cite, spelled the way a reader would type them.
COMPACTION = "tests/fixtures/compaction/"
DUP_UUID = "tests/fixtures/dup_uuid/"
FORK_BYREF = "tests/fixtures/fork_byref/"
FORK_ORIGIN = "tests/fixtures/fork_origin/"
LEGACY_ENTRYPOINT = "tests/fixtures/legacy_entrypoint/"
LEGACY_TITLE = "tests/fixtures/legacy_title/"
MODEL_ONLY = "tests/fixtures/model_only/"
OFFLOAD = "tests/fixtures/offload/"
PARALLEL_TOOLS = "tests/fixtures/parallel_tools/"
REGISTRY_ZOO = "tests/fixtures/registry_zoo/"
SERVER_TOOLS = "tests/fixtures/server_tools/"
SPINE = "tests/fixtures/spine/"
WORKFLOW = "tests/fixtures/workflow/"


@dataclass(frozen=True)
class Cited:
    """One recording behind one claim, or the corpus scan that stands in for a recording.

    `fixture` is a repository-relative fixture directory; its README names the session. `absent`
    inverts the claim — the fixture is evidence that the field is *missing* there, which is how
    a field Claude Code added later is dated.
    """

    fixture: str = ""
    # The Claude Code version that wrote the cited records. Bookkeeping records carry no
    # `version` field of their own, so for those this is the fixture README's version.
    version: str = ""
    # A named corpus scan, for a claim no fixture can hold — always with its date.
    scan: str = ""
    # What the fixture shows beyond holding the field, printed after the citation.
    note: str = ""
    absent: bool = False


@dataclass(frozen=True)
class Among:
    """A step into every block of one kind inside a `message.content` list."""

    kind: ContentBlock


# One step of a field's locator: a key to read, or a block kind to select within a content list.
type Step = str | Among


class Described(BaseModel):
    """Base of everything here: extra keys ride along, and aliases work by field name."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
