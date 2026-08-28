"""Where a credential is allowed to appear in what GitHub runs, and what guards it there.

`check.yml` opens by promising a gate that needs no secret, store, or network, and every leaf in
the suite is written to hold it to that. The browser tier breaks both halves — Chromatic is a
third party and the archives go over the wire — so it is a second workflow, and this is the file
that keeps the split honest: one workflow with the token, a fork's pull request kept away from
it, and the git history Chromatic needs to find a baseline.

The repo is public, so a fork's pull request is untrusted input that runs here. GitHub withholds
secrets from one, which makes the guard below a clear failure rather than a leak — but a job
that reaches for a token it will not get fails after the sweep, which is worse than not asking.
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# The one workflow allowed to hold a credential (`plans/visual-testing/design.md`).
UPLOADER = "e2e.yml"

# How a workflow spells a reach into the repository's secrets.
SECRET = "secrets."

# What keeps a fork's pull request from reaching for a token GitHub will not hand it: the head
# repository is the fork, not this repo.
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"

# The other half, and why the two are joined by `||` rather than `&&`: a push carries no pull
# request to read, so the clause above is false on one, and an `and` would keep the upload from
# ever running on main — where the baseline every later build is measured against comes from.
PUSH_CLAUSE = "github.event_name != 'pull_request'"


def workflows() -> dict[str, str]:
    """Every workflow GitHub would run, by file name, as text."""
    found = {path.name: path.read_text() for path in sorted(WORKFLOWS.glob("*.yml"))}
    # Both halves have to be there for anything below to mean what it says: a leaf reading one
    # file would pass a repo whose second workflow was deleted, or never written.
    assert found.keys() >= {"check.yml", UPLOADER}, f"{WORKFLOWS} holds only {sorted(found)}"
    return found


def steps(workflow: str) -> list[dict[str, Any]]:
    """Every step of every job in one workflow, in the order the file declares them."""
    jobs = yaml.safe_load(workflows()[workflow])["jobs"]
    return [step for job in jobs.values() for step in job["steps"]]


def test_only_the_browser_tier_workflow_names_a_secret() -> None:
    """`e2e.yml` is the one place a token enters CI, and `check.yml` still asks for none.

    The gate everyone runs promises no secret in its own header. A `secrets.` that appeared in
    it would be invisible — the workflow would go on passing — while quietly widening what a
    pull request from a fork can be run against.
    """
    naming = {name for name, text in workflows().items() if SECRET in text}
    assert naming == {UPLOADER}


def test_the_browser_tier_checks_out_the_history_a_baseline_is_found_in() -> None:
    """The upload's checkout is unshallowed, because Chromatic walks back to find a baseline.

    A build with no ancestor it recognizes has nothing to compare against, so every page comes
    back as new. The default checkout is one commit deep, and nothing about that failure names
    the depth as its cause.
    """
    checkouts = [step for step in steps(UPLOADER) if "actions/checkout" in step.get("uses", "")]
    assert len(checkouts) == 1, f"{UPLOADER} checks out {len(checkouts)} times"
    assert checkouts[0].get("with", {}).get("fetch-depth") == 0


def test_a_fork_runs_the_browser_tier_and_only_the_upload_stands_behind_the_guard() -> None:
    """The step holding the token is guarded, and it is the only guarded step in the workflow.

    That split is the whole shape: a fork's pull request still gets the sweep — the console
    errors and the htmx swaps, which need nothing but a browser — and stops before the upload it
    has no token for. Guarding the job instead would give a fork PR no browser tier at all.
    """
    guarded = [step for step in steps(UPLOADER) if "if" in step]
    assert len(guarded) == 1, f"{len(guarded)} steps in {UPLOADER} carry an `if`"
    # The guard stands on the step that names the secret, and not one step further along.
    assert SECRET in yaml.safe_dump(guarded[0].get("env", {}))
    # The whole expression, not a substring of it: GitHub evaluates this and we cannot, so its
    # exact text is the only evidence here — and a substring passes an `&&` that would silently
    # skip every upload on main.
    assert guarded[0]["if"] == f"{PUSH_CLAUSE} || {FORK_GUARD}"
    # ...and no other step reaches for the token, which would be a reach out from behind it.
    unguarded = [step for step in steps(UPLOADER) if "if" not in step]
    assert not any(SECRET in yaml.safe_dump(step.get("env", {})) for step in unguarded)
