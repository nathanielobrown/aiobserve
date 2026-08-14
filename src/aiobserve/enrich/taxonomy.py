"""The one vocabulary every enrichment level is written in.

Closed and code-resident on purpose: `GROUP BY category` only means something over a fixed
set, and the code that validates a model's answer is the code a reviewer reads. A member
added here is a taxonomy change — bump `TAXONOMY_VERSION` with it, which makes every
existing row stale without invalidating it, so the viewer can render version-N rows while
version-N+1 backfills.
"""

from enum import StrEnum


class Category(StrEnum):
    """What kind of work an item was. One member per kind of session, turn, or agent run."""

    design = "design"
    implement = "implement"
    fix_bug = "fix_bug"
    refactor = "refactor"
    test = "test"
    debug = "debug"
    review = "review"
    analyze = "analyze"
    document = "document"
    configure = "configure"
    vcs_ops = "vcs_ops"
    explore = "explore"
    chat = "chat"
    other = "other"


class Outcome(StrEnum):
    """How the item ended, as the transcript shows it — not whether the work was good."""

    completed = "completed"
    partial = "partial"
    failed = "failed"
    abandoned = "abandoned"
    unclear = "unclear"


# What each member means, in one line. Data rather than comments because the prompt is
# written from these: a definition the classifier never sees is a definition that drifts.
# Every member needs an entry — nothing here may be defaulted or skipped.
CATEGORY_DEFINITIONS: dict[Category, str] = {
    Category.design: "Deciding how something should work, before it is built.",
    Category.implement: "Building something that did not exist.",
    Category.fix_bug: "Making broken behaviour correct, with the fix already known.",
    Category.refactor: "Changing structure without changing behaviour.",
    Category.test: "Writing or fixing tests, and running suites.",
    Category.debug: (
        "Hunting the cause of a failure that already happened — reproducing, instrumenting, "
        "bisecting. Not searching a change for defects it might have."
    ),
    Category.review: (
        "Judging a change someone else made — reading it, probing it, testing it for defects."
    ),
    Category.analyze: "Answering a question from data or code — measurement, census, findings.",
    Category.document: "Writing prose: docs, comments, commit messages, reports.",
    Category.configure: "Tooling, dependencies, CI, environment, editor and agent settings.",
    Category.vcs_ops: "Branches, commits, rebases, PRs, merges — version control as the work.",
    Category.explore: "Finding out what is there, with no change intended yet.",
    Category.chat: "Conversation that drives no work: a question, an aside, an interruption.",
    Category.other: "Fits none of the above. A growing share here says the taxonomy needs work.",
}

OUTCOME_DEFINITIONS: dict[Outcome, str] = {
    Outcome.completed: "What was asked was done.",
    Outcome.partial: "Some of what was asked landed; the rest did not.",
    Outcome.failed: "It was attempted and did not work.",
    Outcome.abandoned: "Dropped before an answer — interrupted, or redirected onto something else.",
    Outcome.unclear: "The records do not say how it ended.",
}


# Bumped whenever a member above changes meaning, arrives, or leaves. Rows record the
# version they were written under, so a bump re-enriches rather than corrupting a mixed set.
TAXONOMY_VERSION = 2
