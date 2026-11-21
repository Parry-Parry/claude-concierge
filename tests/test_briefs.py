"""The Brief pane: what the morning job keeps, and what the page and the home card read back."""

import dataclasses

import pytest

from concierge import app_api, briefs, config, dashboard, settings

RUN = {"date": "2026-11-22", "headline": "Dentist at 10, two replies owed",
       "sections": [{"title": "Today", "items": [{"text": "Dentist", "detail": "10:00", "level": "amber"}]},
                    {"title": "People", "items": [{"text": "Sam asked about Friday", "level": "red",
                                                    "link": "javascript:alert(1)"}]}]}


def test_a_saved_brief_reads_back_and_the_latest_is_the_default(tmp_path):
    briefs.save({**RUN, "date": "2026-11-21"}, tmp_path)
    briefs.save(RUN, tmp_path)
    assert briefs.dates(tmp_path) == ["2026-11-22", "2026-11-21"]
    got = briefs.get(None, tmp_path)
    assert got["date"] == "2026-11-22" and got["sections"][0]["items"][0]["level"] == "amber"
    assert "link" not in got["sections"][1]["items"][0]  # only http(s) links are kept
    assert briefs.get("2026-01-01", tmp_path) is None


def test_the_home_card_counts_what_needs_him(tmp_path):
    briefs.save(RUN, tmp_path)
    card = briefs.card(tmp_path)
    assert (card["headline"], card["items"], card["red"], card["url"]) == (RUN["headline"], 2, 1, "/claude/brief/")


@pytest.mark.parametrize("bad", [{}, {"date": "22/11/2026", "headline": "x"}, {"date": "2026-11-22"},
                                 {**RUN, "sections": [{"items": [{"detail": "no text"}]}]},
                                 {**RUN, "headline": "x" * 301}])
def test_a_bad_brief_is_refused_with_a_reason(tmp_path, bad):
    with pytest.raises(ValueError):
        briefs.save(bad, tmp_path)
    assert briefs.dates(tmp_path) == []


def test_the_api_serves_runs_only_when_the_pane_is_on(tmp_path, monkeypatch):
    on = dataclasses.replace(config.SETTINGS.app, panes=("brief",), brief_runs=str(tmp_path))
    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, app=on))
    briefs.save(RUN)
    code, body = app_api.get("/api/brief", {})
    assert code == 200 and body["run"]["date"] == "2026-11-22" and body["dates"] == ["2026-11-22"]
    assert app_api.brief()["red"] == 1
    off = dataclasses.replace(on, panes=())
    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, app=off))
    assert app_api.brief() is None


def test_the_browser_pane_proxies_to_the_configured_novnc():
    app = dataclasses.replace(settings.App(), browser_upstream="127.0.0.1:6080")
    assert dashboard.browser_upstream(app) == ("127.0.0.1", 6080)
    assert dashboard.browser_upstream(settings.App()) == ("127.0.0.1", 16180)
