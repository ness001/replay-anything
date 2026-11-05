"""Security: sessions stay on their site, MCP runs only saved recipes, inputs are encoded, secrets are redacted."""

import json

import pytest
from conftest import SITE, save_login

from replay_anything import api
from replay_anything import recipe as recipes
from replay_anything.errors import RecipeInvalid
from replay_anything.paths import host_of
from replay_anything.record import redact


def _write(tmp_path, body: dict):
    data = {
        "name": "evil",
        "description": "d",
        "site": SITE,
        "auth": "session",
        "inputs": {"c": {"type": "string"}},
        **body,
    }
    p = tmp_path / "evil.yaml"
    import yaml

    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_session_not_sent_off_site(site, tmp_path):
    save_login()
    p = _write(tmp_path, {"steps": [{"id": "a", "websocket": "ws://evil.example:1/x", "send": "hi"}]})
    r = api.run_recipe(str(p), {"c": "x"}, allow_path=True)
    assert r["error"] == "step_failed" and "refusing" in r["message"]
    p = _write(tmp_path, {"steps": [{"id": "a", "request": "GET https://evil.example/x"}]})
    assert "refusing" in api.run_recipe(str(p), {"c": "x"}, allow_path=True)["message"]


def test_session_not_sent_off_site_by_redirect(site, tmp_path):
    """A same-site URL that redirects elsewhere must not carry the session there.

    The test site is 127.0.0.1; "localhost" is the same server under a different host name."""
    save_login()
    from urllib.parse import quote

    off_site = quote(SITE.replace("127.0.0.1", "localhost") + "/whoami", safe="")
    p = _write(tmp_path, {"steps": [{"id": "a", "request": f"GET /go?to={off_site}", "extract": {"sid": "$.sid"}}]})
    r = api.run_recipe(str(p), {"c": "x"}, allow_path=True)
    assert r["error"] == "step_failed" and "refusing" in r["message"], r


def test_mcp_cannot_run_arbitrary_paths(site, tmp_path):
    save_login()
    p = _write(tmp_path, {"steps": [{"id": "a", "request": "GET /api/session"}]})
    assert api.run_recipe(str(p), {"c": "x"})["error"] == "recipe_not_found"
    assert api.run_recipe(str(p), {"c": "x"}, allow_path=True)["ok"]


def test_inputs_are_encoded_in_urls(site, monkeypatch):
    """Inputs become one encoded path segment (servers may still decode %2F; that is theirs to handle)."""
    import httpx

    save_login()
    sent = []
    real = httpx.Client.send
    monkeypatch.setattr(
        httpx.Client, "send", lambda self, req, **kw: (sent.append(req.url.raw_path), real(self, req, **kw))[1]
    )
    api.run_recipe("post_chat", {"channel": "a/../b?x=1#", "text": "enc"})
    assert b"/api/channels/a%2F..%2Fb%3Fx%3D1%23/messages" in sent


def test_errors_do_not_echo_bodies(site, monkeypatch):
    import httpx

    save_login()
    httpx.post(f"{SITE}/admin/break_api", json={"broken": True})
    r = api.run_recipe("post_chat", {"channel": "g", "text": "x"}, no_fallback=True)
    assert r["message"] == "step 'token' failed: HTTP 500"


def test_when_refs_validated():
    with pytest.raises(RecipeInvalid, match="not defined"):
        recipes.parse(
            {
                "name": "x",
                "description": "d",
                "site": "https://a",
                "steps": [{"id": "a", "when": "nope.ok", "request": "GET /"}],
            }
        )


def test_site_and_host_safety():
    with pytest.raises(RecipeInvalid):
        recipes.parse(
            {"name": "x", "description": "d", "site": "https://u:p@a.com", "steps": [{"id": "a", "request": "GET /"}]}
        )
    assert host_of("https://a\\..\\..\\x").count("\\") == 0
    assert host_of("https://u:p@a.com:8443/x") == "a.com:8443"


def test_redact():
    assert redact("https://a/cb?code=abc&state=ok") == "https://a/cb?code=<redacted>&state=ok"
    assert json.loads(redact('{"access_token": "t", "user": {"name": "n", "password": "p"}}')) == {
        "access_token": "<redacted>",
        "user": {"name": "n", "password": "<redacted>"},
    }
    assert redact("user=a&password=b") == "user=a&password=<redacted>"
