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

    # Deciding how something should work, before it is built.
    design = "design"
    # Building something that did not exist.
    implement = "implement"
    # Making broken behaviour correct, with the fix already known.
    fix_bug = "fix_bug"
    # Changing structure without changing behaviour.
    refactor = "refactor"
    # Writing or fixing tests, and running suites.
    test = "test"
    # Hunting an unknown cause: reproducing, instrumenting, bisecting.
    debug = "debug"
    # Reading someone else's change and judging it.
    review = "review"
    # Answering a question from data or code — measurement, census, findings.
    analyze = "analyze"
    # Writing prose: docs, comments, commit messages, reports.
    document = "document"
    # Tooling, dependencies, CI, environment, editor and agent settings.
    configure = "configure"
    # Branches, commits, rebases, PRs, merges — version control as the work itself.
    vcs_ops = "vcs_ops"
    # Finding out what is there, with no change intended yet.
    explore = "explore"
    # Conversation that drives no work: a question, an aside, an interruption.
    chat = "chat"
    # Fits none of the above. A growing share here is the signal to revise the taxonomy.
    other = "other"


class Outcome(StrEnum):
    """How the item ended, as the transcript shows it — not whether the work was good."""

    completed = "completed"
    # Some of what was asked landed; the rest did not.
    partial = "partial"
    # It was attempted and did not work.
    failed = "failed"
    # Dropped before an answer — interrupted, or redirected onto something else.
    abandoned = "abandoned"
    # The records do not say how it ended.
    unclear = "unclear"


# Bumped whenever a member above changes meaning, arrives, or leaves. Rows record the
# version they were written under, so a bump re-enriches rather than corrupting a mixed set.
TAXONOMY_VERSION = 1
