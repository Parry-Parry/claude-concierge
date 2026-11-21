"""Todoist for the Tasks page, on Todoist's API v1.

Shapes Todoist tasks into what tasks.html already draws (the fields it used to get from
TickTick: title, content, dueDate, priority 5/3/1/0, list, listColor, etag...) so the page and
its search did not have to change, and keeps the page fast: it answers from an in-memory copy and
refreshes it behind the reply.

The token is never in config or the repo. It comes from $TODOIST_API_TOKEN or from TOKEN_FILE,
which `concierge todoist-token` (a terminal prompt) or the app's Settings page writes, mode 600.
Find it in Todoist: Settings → Integrations → Developer → API token.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = "https://api.todoist.com/api/v1"
TOKEN_FILE = Path.home() / ".config" / "claude-concierge" / "todoist-token"
CACHE_SECONDS = 60
TIMEOUT = 20

# The page's Today and Next tabs: queries in search.js's syntax (see tasks.html's help).
VIEWS = {"today": "due:today | due:overdue", "next": "due:week | !high"}

# Todoist's API counts priority backwards from its apps: 4 is p1 (red). The page uses TickTick's
# 5 High, 3 Medium, 1 Low, 0 None, which its colours, groups and search (!high) are built on.
TO_PAGE = {4: 5, 3: 3, 2: 1, 1: 0}
TO_TODOIST = {v: k for k, v in TO_PAGE.items()}

# Todoist names its project colours; the page wants a hex for the stripe.
COLORS = {
    "berry_red": "#b8255f", "red": "#db4035", "orange": "#ff9933", "yellow": "#fad000",
    "olive_green": "#afb83b", "lime_green": "#7ecc49", "green": "#299438", "mint_green": "#6accbc",
    "teal": "#158fad", "sky_blue": "#14aaf5", "light_blue": "#96c3eb", "blue": "#4073ff",
    "grape": "#884dff", "violet": "#af38eb", "lavender": "#eb96eb", "magenta": "#e05194",
    "salmon": "#ff8d85", "charcoal": "#808080", "grey": "#b8b8b8", "taupe": "#ccac93",
}
WONT_DO = "wont-do"  # Todoist has no won't-do: the label says why it was closed


class NotSetUp(RuntimeError):
    """No token on this node."""


class Conflict(Exception):
    """The task changed in Todoist since the page loaded it. Carries the task as it is now."""

    def __init__(self, msg: str, task: dict):
        super().__init__(msg)
        self.task = task


def token(env: dict | None = None, path: Path | None = None) -> str:
    env = os.environ if env is None else env
    if env.get("TODOIST_API_TOKEN", "").strip():
        return env["TODOIST_API_TOKEN"].strip()
    p = path or TOKEN_FILE
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def save_token(value: str, path: Path | None = None) -> None:
    """Write the token where token() reads it, readable by this user only."""
    value = value.strip()
    if not value or len(value) > 200 or any(c.isspace() for c in value):
        raise ValueError("that doesn't look like a Todoist API token")
    p = path or TOKEN_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    os.replace(tmp, p)
    _state.update(tasks=[], projects={}, at=0.0, error=None, failed_at=0.0)


def _http(method: str, path: str, *, query: dict | None = None, body: dict | None = None) -> dict:
    """One API call. A body goes form-encoded (each value JSON), which is what /sync documents."""
    tok = token()
    if not tok:
        raise NotSetUp("Todoist isn't connected yet: add the API token in Concierge → Settings")
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = urllib.parse.urlencode({k: json.dumps(v) for k, v in body.items()}).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}", **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Todoist refused the API token (it may have been reset)") from e
        if e.code == 404:
            raise LookupError("no such task in Todoist") from e
        raise RuntimeError(f"Todoist said HTTP {e.code}: {e.read()[:200].decode(errors='replace')}") from e
    return json.loads(raw) if raw else {}


# The one seam the tests replace.
call = _http


def _pages(path: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(50):  # 10,000 tasks; a loop that never ends would be worse than a short list
        page = call("GET", path, query={"limit": 200, **({"cursor": cursor} if cursor else {})})
        out += page.get("results") or []
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return out


def _due(due: dict | None) -> dict:
    if not due or not due.get("date"):
        return {"dueDate": None, "isAllDay": False, "timeZone": None, "repeatFlag": None}
    d = due["date"]
    if len(d) == 10:  # all day: midnight UTC in the zone the page reads it in
        return {"dueDate": f"{d}T00:00:00.000+0000", "isAllDay": True, "timeZone": "UTC",
                "repeatFlag": due.get("string") if due.get("is_recurring") else None}
    return {"dueDate": d, "isAllDay": False, "timeZone": due.get("timezone"),
            "repeatFlag": due.get("string") if due.get("is_recurring") else None}


def slim(t: dict, projects: dict[str, dict] | None = None) -> dict:
    """A Todoist task as the page wants it."""
    proj = (projects or {}).get(t.get("project_id")) or {}
    done = bool(t.get("checked") or t.get("completed_at") or t.get("is_deleted"))
    return {
        "id": t.get("id"), "projectId": t.get("project_id"), "title": t.get("content") or "",
        "content": t.get("description") or "", "items": [], "priority": TO_PAGE.get(t.get("priority") or 1, 0),
        "tags": list(t.get("labels") or []), "parentId": t.get("parent_id"), "kind": "TEXT",
        # updated_at moves on every edit made anywhere, which is all the conflict check needs.
        "etag": t.get("updated_at") or t.get("added_at") or "", "status": 2 if done else 0,
        "modifiedTime": t.get("updated_at"), "createdTime": t.get("added_at"),
        "list": proj.get("name", ""), "listColor": COLORS.get(proj.get("color") or ""),
        "url": f"https://app.todoist.com/app/task/{t.get('id')}",
        **_due(t.get("due")),
    }


_state: dict = {"tasks": [], "projects": {}, "at": 0.0, "error": None, "failed_at": 0.0}
_refreshing = threading.Lock()


def _refresh() -> None:
    try:
        projects = {p["id"]: p for p in _pages("/projects")}
        tasks = _pages("/tasks")
        _state.update(tasks=tasks, projects=projects, at=time.time(), error=None)
    except Exception as exc:  # noqa: BLE001 - the page shows it beside the older copy
        _state.update(error=str(exc), failed_at=time.time())
    finally:
        _refreshing.release()


def all_open(fresh: bool = False) -> dict:
    """Every open task, the page's views, and when Todoist was read.
    Only the very first call (no copy yet) waits for Todoist."""
    if not token():
        raise NotSetUp("Todoist isn't connected yet: add the API token in Concierge → Settings")
    at = _state["at"]
    backing_off = time.time() - _state["failed_at"] < 30
    if (fresh or time.time() - at > CACHE_SECONDS) and not backing_off and _refreshing.acquire(blocking=False):
        if at:
            threading.Thread(target=_refresh, daemon=True).start()
        else:
            _refresh()
    if not _state["at"] and _state["error"]:
        raise RuntimeError(_state["error"])
    projects = _state["projects"]
    return {"tasks": [slim(t, projects) for t in _state["tasks"]], "views": VIEWS,
            "at": _state["at"], "error": _state["error"]}


def raw(task_id: str) -> dict:
    return call("GET", f"/tasks/{urllib.parse.quote(task_id, safe='')}")


def one(task_id: str) -> dict:
    return slim(raw(task_id), _state["projects"])


def _sync(*commands: dict) -> None:
    cmds = [{"uuid": str(uuid.uuid4()), **c} for c in commands]
    status = call("POST", "/sync", body={"commands": cmds}).get("sync_status") or {}
    bad = [status.get(c["uuid"]) for c in cmds if status.get(c["uuid"]) != "ok"]
    if bad:
        err = bad[0] if isinstance(bad[0], dict) else {}
        raise RuntimeError(f"Todoist didn't take the change: {err.get('error') or bad[0] or 'no answer'}")


def _put(task: dict) -> None:
    """The copy follows a write straight away, so the next page load doesn't undo it on screen."""
    rest = [t for t in _state["tasks"] if t.get("id") != task.get("id")]
    _state["tasks"] = rest if task.get("checked") or task.get("is_deleted") else [*rest, task]


def update(task_id: str, etag: str, changes: dict) -> dict:
    """Write the page's edits: content (the notes), priority, day (YYYY-MM-DD or None), status
    (2 done, -1 won't do). Refuses if the task changed in Todoist since the page read it.
    Returns the task as Todoist now has it."""
    cur = raw(task_id)
    if cur.get("checked") or cur.get("is_deleted"):
        raise Conflict("It's already done or deleted in Todoist.", slim(cur, _state["projects"]))
    if etag != (cur.get("updated_at") or cur.get("added_at") or ""):
        raise Conflict("It changed in Todoist since this page loaded it. This is the latest; try again.",
                       slim(cur, _state["projects"]))
    args: dict = {"id": task_id}
    if "content" in changes:
        args["description"] = changes["content"]
    if "priority" in changes:
        args["priority"] = TO_TODOIST[changes["priority"]]
    if "day" in changes:
        if changes["day"] is None:
            args["due"] = None
        else:
            # Keep the rest of the due (its repeat rule, its time of day): only the date moves.
            old = cur.get("due") or {}
            time_part = old.get("date", "")[10:] if len(old.get("date") or "") > 10 else ""
            args["due"] = ({k: old[k] for k in ("string", "lang", "is_recurring", "timezone") if old.get(k) is not None}
                           | {"date": changes["day"] + time_part})
    if changes.get("status") == -1:
        args["labels"] = sorted({*(cur.get("labels") or []), WONT_DO})
    cmds = [{"type": "item_update", "args": args}] if len(args) > 1 else []
    if changes.get("status") == 2:  # close, not complete: a repeating task moves to its next date
        cmds.append({"type": "item_close", "args": {"id": task_id}})
    elif changes.get("status") == -1:  # won't do ends it, repeating or not
        cmds.append({"type": "item_complete", "args": {"id": task_id}})
    if cmds:
        _sync(*cmds)
    try:
        now = raw(task_id)
    except LookupError:  # completed tasks can drop out of /tasks/{id}
        now = cur | {"checked": True}
    _put(now)
    return slim(now, _state["projects"])
