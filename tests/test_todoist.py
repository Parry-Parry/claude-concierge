"""Todoist behind the Tasks page: the page's shape, safe writes, the token, and the app's own origin."""

import dataclasses
import json
import os

import pytest

from concierge import dashboard, settings, todoist


class FakeTodoist:
    def __init__(self, task):
        self.task, self.sent = dict(task), []

    def __call__(self, method, path, query=None, body=None):
        self.sent.append((method, path, body))
        if path == "/sync":
            for c in body["commands"]:
                if c["type"] == "item_update":
                    self.task |= {k: v for k, v in c["args"].items() if k != "id"} | {"updated_at": "later"}
                if c["type"] in ("item_close", "item_complete"):
                    self.task["checked"] = True
            return {"sync_status": {c["uuid"]: "ok" for c in body["commands"]}}
        if path.startswith("/tasks/"):
            return dict(self.task)
        return {"results": []}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(todoist, "TOKEN_FILE", tmp_path / "token")
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    todoist._state.update(tasks=[], projects={}, at=0.0, error=None, failed_at=0.0)


TASK = {"id": "6X7", "project_id": "p1", "content": "Renew the MOT", "description": "garage on Fri",
        "priority": 4, "labels": ["car"], "updated_at": "v1", "added_at": "v0",
        "due": {"date": "2026-11-21", "string": "every year", "is_recurring": True, "lang": "en"}}


def test_a_todoist_task_becomes_what_the_page_draws():
    t = todoist.slim(TASK, {"p1": {"name": "Home", "color": "berry_red"}})
    assert (t["title"], t["content"], t["priority"], t["tags"], t["list"]) == ("Renew the MOT", "garage on Fri", 5, ["car"], "Home")
    assert t["dueDate"] == "2026-11-21T00:00:00.000+0000" and t["isAllDay"] and t["repeatFlag"] == "every year"
    assert t["listColor"] == "#b8255f" and t["etag"] == "v1" and t["status"] == 0
    assert todoist.slim({**TASK, "priority": 1, "due": None})["priority"] == 0


def test_a_stale_page_never_overwrites_todoist(monkeypatch):
    fake = FakeTodoist(TASK)
    monkeypatch.setattr(todoist, "call", fake)
    with pytest.raises(todoist.Conflict):
        todoist.update("6X7", "old", {"content": "mine"})
    assert [p for _, p, _ in fake.sent] == ["/tasks/6X7"]


def test_moving_a_repeating_task_keeps_its_repeat_and_done_closes_it(monkeypatch):
    fake = FakeTodoist(TASK)
    monkeypatch.setattr(todoist, "call", fake)
    todoist.update("6X7", "v1", {"day": "2026-11-28", "priority": 3, "status": 2})
    (sync,) = [b for _, p, b in fake.sent if p == "/sync"]
    upd, close = sync["commands"]
    assert upd["args"]["due"] == {"date": "2026-11-28", "string": "every year", "is_recurring": True, "lang": "en"}
    assert upd["args"]["priority"] == 3 and close["type"] == "item_close"  # close: a repeating task advances


def test_moving_a_timed_task_keeps_its_time_and_no_date_clears_it(monkeypatch):
    fake = FakeTodoist({**TASK, "due": {"date": "2026-11-21T09:30:00", "string": "21 Nov 9:30"}})
    monkeypatch.setattr(todoist, "call", fake)
    todoist.update("6X7", "v1", {"day": "2026-11-22"})
    assert fake.sent[1][2]["commands"][0]["args"]["due"]["date"] == "2026-11-22T09:30:00"
    fake = FakeTodoist(TASK)
    monkeypatch.setattr(todoist, "call", fake)
    todoist.update("6X7", "v1", {"day": None})
    assert fake.sent[1][2]["commands"][0]["args"]["due"] is None


def test_wont_do_labels_it_and_ends_it(monkeypatch):
    fake = FakeTodoist(TASK)
    monkeypatch.setattr(todoist, "call", fake)
    todoist.update("6X7", "v1", {"status": -1})
    upd, done = fake.sent[1][2]["commands"]
    assert upd["args"]["labels"] == ["car", "wont-do"] and done["type"] == "item_complete"


def test_the_token_is_stored_for_this_user_only_and_read_back(tmp_path):
    todoist.save_token("  abc123  ")
    assert todoist.token() == "abc123"
    assert oct(os.stat(todoist.TOKEN_FILE).st_mode & 0o777) == "0o600"
    with pytest.raises(ValueError):
        todoist.save_token("two words")
    assert todoist.token({"TODOIST_API_TOKEN": "fromenv"}) == "fromenv"


def test_no_token_tells_the_page_how_to_connect():
    code, body = dashboard.tasks_get("/api/tasks/all", {})
    assert code == 503 and body["setup"] and "Settings" in body["error"]


# --- the app's own origin ------------------------------------------------------------------

def test_the_app_accepts_posts_from_its_own_tailnet_address_and_no_other():
    app = dataclasses.replace(settings.App(), base_url="https://box.example.ts.net/claude",
                              extra_origins=("https://other.example.ts.net",))
    ok = dashboard.allowed_origins(app)
    assert "https://box.example.ts.net" in ok and "https://other.example.ts.net" in ok
    assert "http://127.0.0.1:8787" in ok
    assert not any("fish-grouper" in o for o in ok)
    assert dashboard.origin_of("https://h.ts.net:8443/x/y") == "https://h.ts.net:8443"


def test_a_post_from_the_configured_origin_gets_through_and_a_foreign_one_does_not(monkeypatch):
    monkeypatch.setattr(dashboard, "SPAWN_ORIGINS", {"https://box.example.ts.net"})
    good = {"Origin": "https://box.example.ts.net", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
    assert dashboard._read_post(good, json.dumps({"a": 1}).encode()) == {"a": 1}
    code, body = dashboard._read_post({**good, "Origin": "https://evil.example"}, b"{}")
    assert code == 403 and body["error"] == "cross-site request refused"


def test_settings_reads_panes_and_extra_origins(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[app]\nbase_url = "https://x.ts.net/claude/"\npanes = ["brief"]\nextra_origins = ["https://y.ts.net/"]\n')
    app = settings.load(p).app
    assert app.base_url == "https://x.ts.net/claude" and app.panes == ("brief",) and app.extra_origins == ("https://y.ts.net",)
