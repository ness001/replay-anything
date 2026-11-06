"""transport: browser (fetch inside a real page), one shared page per run, headed and pacing options."""

import pytest
import yaml
from conftest import SITE, save_login

from replay_anything import api, runtime
from replay_anything import recipe as recipes
from replay_anything.errors import RecipeInvalid


def _guarded(tmp_path, **extra):
    """A recipe for an API that answers only when the page's own script has set a cookie."""
    p = tmp_path / "guarded.yaml"
    data = {
        "name": "guarded",
        "description": "d",
        "site": f"{SITE}/guarded",
        "auth": "session",
        "inputs": {"text": {"type": "string"}, "doc": {"type": "file", "optional": True}},
        "steps": [
            {
                "id": "echo",
                "request": "POST /api/echo",
                "multipart": {"text": "{{inputs.text}}", "file": "{{inputs.doc}}"},
                "extract": {"user": "$.user", "ua": "$.user_agent", "text": "$.text", "file": "$.file"},
            }
        ],
        **extra,
    }
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_http_transport_is_blocked_by_a_js_cookie_check(site, tmp_path):
    save_login()
    r = api.run_recipe(str(_guarded(tmp_path)), {"text": "hi"}, allow_path=True)
    assert r["ok"] is False and r["error"] == "session_expired", r  # 403 from the bot check


@pytest.mark.browser
def test_browser_transport_passes_the_js_cookie_check(site, tmp_path):
    save_login()
    doc = tmp_path / "note.txt"
    doc.write_text("file body", encoding="utf-8")
    p = _guarded(tmp_path, transport="browser")
    r = api.run_recipe(str(p), {"text": "hi", "doc": str(doc)}, allow_path=True)
    assert r["ok"] and r["via"] == "browser", r
    out = r["output"]["echo"]
    assert out["user"] == "alice" and out["text"] == "hi" and out["file"] == "file body"
    assert "python-httpx" not in out["ua"]  # sent by the browser itself


@pytest.mark.browser
def test_browser_transport_sees_an_expired_session(site, tmp_path):
    import httpx

    save_login()
    httpx.post(f"{SITE}/admin/expire_sessions")
    r = api.run_recipe(str(_guarded(tmp_path, transport="browser")), {"text": "hi"}, allow_path=True)
    assert r["error"] == "session_expired", r


@pytest.mark.browser
def test_browser_steps_share_one_page(site, tmp_path, monkeypatch):
    save_login()
    launched = []
    from replay_anything import browser

    real = browser.launch
    monkeypatch.setattr(browser, "launch", lambda *a, **k: launched.append(k) or real(*a, **k))
    p = tmp_path / "two.yaml"
    step = [{"goto": "/c/general"}, {"extract": {"h": {"selector": "h1"}}}]
    p.write_text(
        yaml.safe_dump(
            {
                "name": "two",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "steps": [{"id": "a", "browser": step}, {"id": "b", "browser": step}],
                "headed": False,
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["ok"], r
    assert len(launched) == 1 and launched[0]["headless"] is True


def test_pacing_human_waits_between_steps(monkeypatch):
    waits = []
    monkeypatch.setattr(runtime.time, "sleep", waits.append)
    base = {"name": "x", "description": "d", "site": "https://a", "steps": [{"id": "a", "request": "GET /"}]}
    runtime._pause(recipes.parse(base))
    assert waits == []
    runtime._pause(recipes.parse({**base, "pacing": "human"}))
    assert len(waits) == 1 and 0.5 <= waits[0] <= 2.0


@pytest.mark.parametrize(
    "extra, msg",
    [({"transport": "ws"}, "transport"), ({"headed": "yes"}, "headed"), ({"pacing": "slow"}, "pacing")],
)
def test_new_options_are_validated(extra, msg):
    base = {"name": "x", "description": "d", "site": "https://a", "steps": [{"id": "a", "request": "GET /"}]}
    with pytest.raises(RecipeInvalid, match=msg):
        recipes.parse({**base, **extra})


@pytest.mark.browser
def test_browser_transport_never_follows_a_redirect(site, tmp_path):
    save_login()
    p = tmp_path / "r.yaml"
    step = {"id": "r", "request": "POST /go", "query": {"to": "http://localhost:1/steal"}, "body": "secret"}
    p.write_text(
        yaml.safe_dump(
            {"name": "r", "description": "d", "site": SITE, "auth": "session", "transport": "browser", "steps": [step]}
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["error"] == "session_expired" and "redirected" in r["message"], r


@pytest.mark.browser
def test_browser_transport_lowercase_content_type_is_not_doubled(site, tmp_path):
    save_login()
    p = tmp_path / "j.yaml"
    step = {
        "id": "s",
        "request": "POST /guarded/api/json",
        "headers": {"content-type": "application/json"},
        "json": {"text": "hi"},
        "extract": {"ct": "$.content_type"},
    }
    p.write_text(
        yaml.safe_dump(
            {"name": "j", "description": "d", "site": SITE, "auth": "session", "transport": "browser", "steps": [step]}
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["ok"] and r["output"]["s"]["ct"] == "application/json", r
