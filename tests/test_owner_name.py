"""Who the concierge works for comes from [app] owner_name, never a hardcoded name."""

import dataclasses

from concierge import app_api, config, dashboard, settings, taskchats


def test_owner_name_is_read_from_config_with_a_neutral_default(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[app]\nowner_name = "Andrew"\n')
    assert settings.load(p).app.owner_name == "Andrew"
    p.write_text("[app]\n")
    assert settings.load(p).app.owner_name == "Owner"


def test_briefs_prompts_and_the_page_use_the_configured_name(monkeypatch):
    app = dataclasses.replace(config.SETTINGS.app, owner_name="Andrew")
    monkeypatch.setattr(config, "SETTINGS", dataclasses.replace(config.SETTINGS, app=app))
    assert "by Andrew." in dashboard.job_brief("p", "", "")
    assert "by Andrew." in taskchats.brief({"id": "1", "content": "x"}, "Inbox", "")
    page = app_api.app_html().decode()
    assert 'data-owner="Andrew"' in page and "{{OWNER}}" not in page
    rendered = settings.render_prompt("job", config.SETTINGS).read_text()
    assert "Andrew says:" in rendered and "{{OWNER}}" not in rendered
    for f in ("app_api.py", "dashboard.py", "taskchats.py", "threads.py", "reaper.py", "app.html"):
        assert "Kelvin" not in (config.REPO / "concierge" / f).read_text()
