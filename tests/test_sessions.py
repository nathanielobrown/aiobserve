from pathlib import Path

import pytest

from aiobserve.sessions import Session, encode_project_path, find_sessions


def make_projects_root(tmp_path: Path, project: Path, session_ids: list[str]) -> Path:
    """Build the on-disk shape Claude Code writes, as observed under ~/.claude/projects.

    A session is `<root>/<encoded-cwd>/<session-id>.jsonl`, with its subagent runs in a
    sibling directory named for the session. Content doesn't matter here — discovery is
    about the layout, and the parser tests own the records.
    """
    root = tmp_path / "projects"
    project_dir = root / encode_project_path(project)
    project_dir.mkdir(parents=True)
    for session_id in session_ids:
        (project_dir / f"{session_id}.jsonl").write_text("")
    return root


def test_encode_project_path_matches_claude_codes_directory_name():
    """A project's directory name is its absolute path with each separator turned into a dash."""
    assert encode_project_path(Path("/Users/nob/repos/mycelia")) == "-Users-nob-repos-mycelia"


def test_encode_project_path_resolves_a_relative_path(tmp_path: Path, monkeypatch):
    """A relative path is resolved first — the directory is named after the absolute one."""
    # If the caller passes a path relative to the working directory...
    monkeypatch.chdir(tmp_path)
    (tmp_path / "myproject").mkdir()
    # ...then the encoding is of the absolute path, so it matches what is on disk.
    encoded = encode_project_path(Path("myproject"))
    assert encoded == encode_project_path(tmp_path / "myproject")
    assert encoded.startswith("-")


def test_find_sessions_returns_every_transcript_for_a_project(tmp_path: Path):
    """Every session transcript in a project's directory is discovered, sorted by id."""
    # If a project has three recorded sessions...
    project = Path("/Users/nob/repos/mycelia")
    root = make_projects_root(tmp_path, project, ["c-third", "a-first", "b-second"])
    # ...then all three come back, in a stable order...
    sessions = find_sessions(project, projects_root=root)
    assert [s.id for s in sessions] == ["a-first", "b-second", "c-third"]
    # ...each carrying the path to its own transcript.
    assert sessions[0] == Session(
        id="a-first",
        transcript=root / "-Users-nob-repos-mycelia" / "a-first.jsonl",
    )


def test_find_sessions_ignores_non_transcript_files(tmp_path: Path):
    """Only `.jsonl` files count as sessions — the tree also holds metadata and scratch."""
    project = Path("/Users/nob/repos/mycelia")
    root = make_projects_root(tmp_path, project, ["real-session"])
    project_dir = root / encode_project_path(project)
    (project_dir / "notes.md").write_text("")
    (project_dir / "agent-abc.meta.json").write_text("")

    assert [s.id for s in find_sessions(project, projects_root=root)] == ["real-session"]


def test_find_sessions_ignores_the_subagent_tree(tmp_path: Path):
    """A subagent run belongs to its session, so it is never returned as a session of its own."""
    # If a session spawned two subagents, whose transcripts sit under the session's own directory...
    project = Path("/Users/nob/repos/mycelia")
    root = make_projects_root(tmp_path, project, ["parent-session"])
    subagents = root / encode_project_path(project) / "parent-session" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-aaa.jsonl").write_text("")
    (subagents / "agent-bbb.jsonl").write_text("")
    # ...then only the parent is a session...
    sessions = find_sessions(project, projects_root=root)
    assert [s.id for s in sessions] == ["parent-session"]
    # ...and its subagent transcripts hang off it.
    assert sessions[0].subagent_transcripts() == [
        subagents / "agent-aaa.jsonl",
        subagents / "agent-bbb.jsonl",
    ]


def test_find_sessions_finds_subagents_nested_under_a_workflow(tmp_path: Path):
    """A parallel fan-out nests its agents a level deeper; they are still the session's."""
    project = Path("/Users/nob/repos/mycelia")
    root = make_projects_root(tmp_path, project, ["parent-session"])
    workflow = root / encode_project_path(project) / "parent-session" / "subagents" / "workflows"
    (workflow / "wf_1").mkdir(parents=True)
    (workflow / "wf_1" / "agent-ccc.jsonl").write_text("")

    session = find_sessions(project, projects_root=root)[0]
    assert session.subagent_transcripts() == [workflow / "wf_1" / "agent-ccc.jsonl"]


def test_a_session_with_no_subagents_has_none(tmp_path: Path):
    """A session that spawned no subagents reports an empty list, not an error."""
    project = Path("/Users/nob/repos/mycelia")
    root = make_projects_root(tmp_path, project, ["solo-session"])

    assert find_sessions(project, projects_root=root)[0].subagent_transcripts() == []


def test_find_sessions_raises_when_the_project_has_no_recorded_sessions(tmp_path: Path):
    """An unknown project is a mistake to surface, not an empty result to quietly return."""
    # If the project has never been opened in Claude Code, no directory exists for it...
    root = tmp_path / "projects"
    root.mkdir()
    # ...so the caller hears about it, with both the path they asked for and the one we looked in.
    with pytest.raises(FileNotFoundError) as excinfo:
        find_sessions(Path("/Users/nob/repos/nonexistent"), projects_root=root)
    assert "-Users-nob-repos-nonexistent" in str(excinfo.value)
    assert str(root) in str(excinfo.value)
