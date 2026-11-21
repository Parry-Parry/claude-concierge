"""The tasks page's promise: one Todoist task, one chat, however long he is away."""

from pathlib import Path

from concierge import config, registry, spawn, taskchats


class FakeTmux:
    def __init__(self):
        self.windows = []

    def new_window(self, session, window, shell_command, **kw):
        self.windows.append((window, shell_command))

    def list_windows(self, session, **kw):
        return [w for w, _ in self.windows]

    def build_shell_command(self, cwd, argv, env=None):
        return f"cd {cwd} && exec " + " ".join(argv)


def test_the_same_task_always_comes_back_to_its_first_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(spawn.settings, "render_prompt", lambda name: Path("/p/job.md"))
    monkeypatch.setattr(spawn, "resolve_cwd", lambda cwd: Path(cwd).expanduser())
    state, tmux = tmp_path / "jobs.json", FakeTmux()
    task = {"id": "tt1", "project_id": "inbox1", "content": "Fix the projector", "description": "brown spot"}
    fetch = lambda task_id: task

    first = taskchats.open_chat("tt1", "Inbox", "", "chat", state_path=state, fetch=fetch, tmux=tmux)
    assert first["started"] and "Fix the projector" in tmux.windows[0][1]
    sid = registry.load(state)[first["id"]]["claude_session_id"]
    registry.upsert(first["id"], state, rc_url="https://claude.ai/code/session_01X")

    # Still open: same job, no new window.
    again = taskchats.open_chat("tt1", "Inbox", "", "chat", state_path=state, fetch=fetch, tmux=tmux)
    assert (again["id"], again["started"], len(tmux.windows)) == (first["id"], False, 1)
    assert taskchats.chats(state, tmux=tmux)["tt1"] == {"job": first["id"], "rc_url": "https://claude.ai/code/session_01X",
                                                      "title": "Fix the projector", "live": True}

    # Reaped, then the row pruned a week later: resumes the same session, same link.
    tmux.windows.clear()
    registry.save({}, state)
    back = taskchats.open_chat("tt1", "Inbox", "", "chat", state_path=state, fetch=fetch, tmux=tmux)
    assert not back["started"] and back["rc_url"] == "https://claude.ai/code/session_01X"
    assert tmux.windows[0][1].endswith(f"--resume {sid}")


def test_a_task_chat_starts_in_the_default_repo_with_the_task_in_its_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(spawn.settings, "render_prompt", lambda name: Path("/p/job.md"))
    monkeypatch.setattr(spawn, "resolve_cwd", lambda cwd: Path(cwd).expanduser())
    tmux = FakeTmux()
    taskchats.open_chat("tt2", "Home", "", "chat", state_path=tmp_path / "j.json",
                        fetch=lambda task_id: {"id": "tt2", "content": "EICR", "priority": 4}, tmux=tmux)
    cmd = tmux.windows[0][1]
    assert str(config.SETTINGS.default_repo.path) in cmd
    assert "EICR" in cmd and "app.todoist.com/app/task/tt2" in cmd
