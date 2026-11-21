"""The Morning Brief, kept: each day's run as JSON, read by the Brief pane and the app's home card.

The morning-brief job writes it with `concierge brief-save` (JSON on stdin or a file) and then
sends its usual short message. One file per day in [app] brief_runs (state/briefs by default);
a second save on the same day replaces the first.

    {"date": "2026-11-22", "headline": "Dentist at 10, two replies owed",
     "sections": [{"title": "Today", "items": [{"text": "Dentist", "detail": "10:00, St Paul's",
                                                "level": "amber", "link": "https://..."}]}]}

`level` is red (needs him today), amber (needs him soon) or grey (for information). Everything
except date and headline is optional. The text comes from emails and chats, so the page escapes
all of it and only links http(s).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from concierge import config, registry

DAY = re.compile(r"^\d{4}-\d\d-\d\d$")
LEVELS = ("red", "amber", "grey")
LIMITS = {"headline": 300, "title": 120, "text": 500, "detail": 2000, "link": 2000}


def folder() -> Path:
    return Path(config.SETTINGS.app.brief_runs or config.REPO / "state" / "briefs").expanduser()


def _str(v, key: str, required: bool = False) -> str:
    if v is None or v == "":
        if required:
            raise ValueError(f"{key} is missing")
        return ""
    if not isinstance(v, str) or len(v) > LIMITS[key]:
        raise ValueError(f"{key} must be text under {LIMITS[key]} characters")
    return v.strip()


def clean(run: dict) -> dict:
    """The run as stored: checked, trimmed to what the page draws. Raises ValueError with a reason."""
    if not isinstance(run, dict):
        raise ValueError("a brief is a JSON object")
    date = run.get("date")
    if not (isinstance(date, str) and DAY.match(date)):
        raise ValueError("date is YYYY-MM-DD")
    sections = run.get("sections") or []
    if not isinstance(sections, list) or len(sections) > 20:
        raise ValueError("sections is a list (20 at most)")
    out = []
    for s in sections:
        if not isinstance(s, dict) or not isinstance(s.get("items") or [], list) or len(s.get("items") or []) > 50:
            raise ValueError("each section is {title, items: [...]} with 50 items at most")
        items = []
        for i in s.get("items") or []:
            if not isinstance(i, dict):
                raise ValueError("each item is an object with text")
            link = _str(i.get("link"), "link")
            if link and not link.startswith(("https://", "http://")):
                link = ""
            level = i.get("level") if i.get("level") in LEVELS else "grey"
            items.append({"text": _str(i.get("text"), "text", True), "detail": _str(i.get("detail"), "detail"),
                          "level": level, **({"link": link} if link else {})})
        out.append({"title": _str(s.get("title"), "title"), "items": items})
    return {"date": date, "headline": _str(run.get("headline"), "headline", True), "sections": out}


def save(run: dict, where: Path | None = None) -> Path:
    run = clean(run)
    d = where or folder()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{run['date']}.json"
    registry._atomic_write(path, json.dumps(run, indent=1, ensure_ascii=False))
    return path


def dates(where: Path | None = None) -> list[str]:
    d = where or folder()
    return sorted((p.stem for p in d.glob("*.json") if DAY.match(p.stem)), reverse=True) if d.is_dir() else []


def get(date: str | None = None, where: Path | None = None) -> dict | None:
    """That day's run, or the latest when date is None."""
    known = dates(where)
    if date is None:
        date = known[0] if known else None
    if date is None or date not in known:
        return None
    try:
        return json.loads(((where or folder()) / f"{date}.json").read_text())
    except (OSError, ValueError):
        return None


def card(where: Path | None = None) -> dict | None:
    """The home screen's line: the latest headline and how many items need him."""
    run = get(None, where)
    if not run:
        return None
    items = [i for s in run.get("sections") or [] for i in s.get("items") or []]
    return {"headline": run.get("headline", ""), "date": run.get("date"), "items": len(items),
            "red": sum(1 for i in items if i.get("level") == "red"),
            "url": config.SETTINGS.app.brief_url or "/claude/brief/"}
