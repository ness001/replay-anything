"""End-to-end against the local chat site: request replay, websocket, browser fallback, errors."""

import json
from pathlib import Path

import httpx
import pytest
from conftest import SITE, messages, save_login

from replay_anything import api, cli
from replay_anything import recipe as recipes
from replay_anything.errors import RecipeInvalid
from replay_anything.mcp_server import server as mcp_server


def test_post_text_only(site):
    sid = save_login()
    r = api.run_recipe("post_chat", {"channel": "general", "text": "hello"})
    assert r["ok"] and r["via"] == "request", r
    assert r["output"]["url"] == f"/c/general#{r['output']['message_id']}"
    [m] = messages(sid)
    assert m["text"] == "hello" and m["attachments"] == []


def test_post_with_image(site, tmp_path):
    sid = save_login()
    img = tmp_path / "cat.png"
    img.write_bytes(b"\x89PNG fake")
    r = api.run_recipe("post_chat", {"channel": "dev", "text": "pic", "image": str(img)})
    assert r["ok"], r
    [m] = messages(sid, "dev")
    assert m["attachments"] == ["cat.png"]


def test_new_inputs_each_run(site):
    sid = save_login()
    for i in range(3):
        assert api.run_recipe("post_chat", {"channel": "general", "text": f"msg {i}"})["ok"]
    assert [m["text"] for m in messages(sid)] == ["msg 0", "msg 1", "msg 2"]


def test_websocket_recipe(site):
    sid = save_login()
    r = api.run_recipe("post_chat_ws", {"channel": "live", "text": "over ws"})
    assert r["ok"] and r["via"] == "request", r
    assert messages(sid, "live")[0]["id"] == r["output"]["message_id"]


def test_bad_inputs_returns_expected(site):
    save_login()
    r = api.run_recipe("post_chat", {"channel": "general", "colour": "red", "image": "nope.png"})
    assert r["ok"] is False and r["error"] == "bad_inputs"
    assert "missing required input 'text'" in r["problems"]
    assert "unknown input 'colour'" in r["problems"]
    assert any("file not found" in p for p in r["problems"])
    assert r["expected_inputs"]["image"] == {"type": "file", "optional": True}


def test_no_session(site):
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"})
    assert r["error"] == "session_expired" and "replay-anything login" in r["message"]


def test_expired_session_on_server(site):
    save_login()
    httpx.post(f"{SITE}/admin/expire_sessions")
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"})
    assert r["error"] == "session_expired", r
    assert "token" in r["message"]


def test_failed_step_is_named(site):
    save_login()
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"}, no_fallback=True)
    assert r["ok"]
    httpx.post(f"{SITE}/admin/break_api", json={"broken": True})
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"}, no_fallback=True)
    assert r["error"] == "step_failed" and r["step"] == "token" and "500" in r["message"]


def test_unknown_recipe(site):
    r = api.run_recipe("post", {})
    assert r["error"] == "recipe_not_found" and r["candidates"]


@pytest.mark.browser
def test_browser_fallback_when_api_breaks(site, tmp_path):
    sid = save_login()
    httpx.post(f"{SITE}/admin/break_api", json={"broken": True})
    img = tmp_path / "dog.png"
    img.write_bytes(b"\x89PNG fake")
    r = api.run_recipe("post_chat", {"channel": "general", "text": "via browser", "image": str(img)})
    assert r["ok"] and r["via"] == "browser", r
    assert "token" in r["fallback_reason"]
    httpx.post(f"{SITE}/admin/break_api", json={"broken": False})
    [m] = messages(sid)
    assert m["text"] == "via browser" and m["attachments"] == ["dog.png"]
    assert r["output"]["message_id"] == m["id"]


@pytest.mark.browser
def test_browser_only_optional_file_skipped(site):
    sid = save_login()
    r = api.run_recipe("post_chat", {"channel": "general", "text": "no file"}, browser_only=True)
    assert r["ok"] and r["via"] == "browser", r
    assert messages(sid)[0]["attachments"] == []


@pytest.mark.browser
def test_browser_detects_expired_session(site):
    save_login()
    httpx.post(f"{SITE}/admin/expire_sessions")
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"}, browser_only=True)
    assert r["error"] == "session_expired", r


def test_cli_run_and_list(site, capsys):
    save_login()
    assert cli.main(["run", "post_chat", "-i", "channel=general", "-i", "text=from cli"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert cli.main(["run", "post_chat", "-i", "channel=general"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "bad_inputs"
    cli.main(["list", "--query", "websocket"])
    assert [r["name"] for r in json.loads(capsys.readouterr().out)["recipes"]] == ["post_chat_ws"]


@pytest.mark.anyio
async def test_mcp_exactly_two_tools_and_run(site):
    from mcp.client.client import Client

    sid = save_login()
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
        assert sorted(t.name for t in tools.tools) == ["list_recipes", "run_recipe"]
        listed = await client.call_tool("list_recipes", {"site": "127.0.0.1"})
        assert {r["name"] for r in listed.structured_content["recipes"]} == {"post_chat", "post_chat_ws"}
        res = await client.call_tool(
            "run_recipe", {"recipe": "post_chat", "inputs": {"channel": "mcp", "text": "via mcp"}}
        )
        assert res.structured_content["ok"], res
    assert messages(sid, "mcp")[0]["text"] == "via mcp"


@pytest.mark.browser
def test_browser_step_reports_via_browser(site, tmp_path):
    import yaml

    sid = save_login()
    p = tmp_path / "b.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "b",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"text": {"type": "string"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/c/general"},
                            {
                                "fill": {
                                    "selector": "textarea[aria-label='Message']",
                                    "value": "{{inputs.text}}",
                                    "timeout": 5000,
                                }
                            },
                            {"click": {"selector": "button[aria-label='Send']"}},
                            {"extract": {"id": {"selector": "li.msg", "attr": "id"}}},
                        ],
                    }
                ],
                "output": {"id": "{{post.id}}"},
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {"text": "browser step"}, allow_path=True)
    assert r["ok"] and r["via"] == "browser", r
    assert messages(sid)[0]["id"] == r["output"]["id"]


@pytest.mark.browser
def test_paste_html_and_extract_all(site, tmp_path):
    import yaml

    save_login()
    html_file = tmp_path / "msg.html"
    html_file.write_text(
        "<h1>Handoff</h1><table border=1><tr><td><a href='https://x/1'>1</a></td></tr></table>", encoding="utf-8"
    )
    p = tmp_path / "rich.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "rich",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"html_file": {"type": "file"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/rich/ops"},
                            {"paste_html": {"selector": "#ed", "file": "{{inputs.html_file}}"}},
                            {"click": {"selector": "button[name=send]"}},
                            {"wait_for": {"selector": ".rmsg table"}},
                            {"extract": {"msgs": {"selector": ".rmsg", "html": True, "all": True, "last": 5}}},
                        ],
                    }
                ],
                "output": {"msgs": "{{post.msgs}}"},
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {"html_file": str(html_file)}, allow_path=True)
    assert r["ok"], r
    [msg] = r["output"]["msgs"]
    assert "<table" in msg and "<h1>Handoff</h1>" in msg and 'href="https://x/1"' in msg


@pytest.mark.browser
def test_click_waits_for_response(site, tmp_path):
    import yaml

    save_login()

    def recipe(status):
        p = tmp_path / f"w{status}.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "w",
                    "description": "d",
                    "site": SITE,
                    "auth": "session",
                    "inputs": {"h": {"type": "string"}},
                    "steps": [
                        {
                            "id": "post",
                            "browser": [
                                {"goto": "/rich/w"},
                                {"paste_html": {"selector": "#ed", "html": "{{inputs.h}}"}},
                                {
                                    "click": {
                                        "selector": "button[name=send]",
                                        "wait_response": {
                                            "url": "/rich/w/send",
                                            "status": status,
                                            "extract": {"id": "$.id"},
                                        },
                                    }
                                },
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return str(p)

    r = api.run_recipe(recipe(201), {"h": "<b>x</b>"}, allow_path=True)
    assert r["ok"] and r["output"]["post"]["id"].startswith("m"), r  # extracted from the 201 body
    r = api.run_recipe(recipe(200), {"h": "<b>y</b>"}, allow_path=True)
    assert r["error"] == "step_failed" and "expected HTTP 200" in r["message"]


@pytest.mark.browser
def test_has_text_with_quotes_and_last_zero(site, tmp_path):
    import yaml

    save_login()
    p = tmp_path / "q.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "q",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"h": {"type": "string"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/rich/q"},
                            {
                                "paste_html": {
                                    "selector": "#ed",
                                    "html": "<p>{{inputs.h}}</p><table><tr><td>t</td></tr></table>",
                                }
                            },
                            {"click": {"selector": "button[name=send]"}},
                            {"wait_for": {"selector": ".rmsg:has(table)", "has_text": "{{inputs.h}}"}},
                            {
                                "extract": {
                                    "id": {"selector": ".rmsg", "has_text": "{{inputs.h}}", "attr": "id"},
                                    "none": {"selector": ".rmsg", "all": True, "last": 0},
                                }
                            },
                        ],
                    }
                ],
                "output": {"id": "{{post.id}}", "none": "{{post.none}}"},
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {"h": 'it\'s "quoted"'}, allow_path=True)
    assert r["ok"] and r["output"]["id"].startswith("m") and r["output"]["none"] == [], r
    from replay_anything import recipe as recipes

    assert recipes.warnings(recipes.load_file(p)) == []


@pytest.mark.browser
def test_has_text_with_integer_input(site, tmp_path):
    """{{inputs.n}} alone keeps its int type; has_text must still match it (e.g. incident ids)."""
    import yaml

    save_login()
    p = tmp_path / "n.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "n",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"n": {"type": "integer"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/rich/n"},
                            {"paste_html": {"selector": "#ed", "html": "<p>id {{inputs.n}}</p>"}},
                            {"click": {"selector": "button[name=send]"}},
                            {"wait_for": {"selector": ".rmsg", "has_text": "{{inputs.n}}", "timeout": 5000}},
                            {"extract": {"id": {"selector": ".rmsg", "has_text": "{{inputs.n}}", "attr": "id"}}},
                        ],
                    }
                ],
                "output": {"id": "{{post.id}}"},
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {"n": 894575791}, allow_path=True)
    assert r["ok"] and r["output"]["id"].startswith("m"), r


@pytest.mark.browser
def test_goto_reads_page_loaded_response(site, tmp_path):
    """goto + wait_response reads JSON the page fetched itself (e.g. a dashboard that loads its own data)."""
    import yaml

    save_login()
    p = tmp_path / "g.yaml"

    def write(url):
        p.write_text(
            yaml.safe_dump(
                {
                    "name": "g",
                    "description": "d",
                    "site": SITE,
                    "auth": "session",
                    "inputs": {},
                    "steps": [
                        {
                            "id": "load",
                            "browser": [
                                {
                                    "goto": {
                                        "url": "/spa/general",
                                        "timeout": 5000,
                                        "wait_response": {"url": url, "extract": {"msgs": "$.messages"}},
                                    }
                                }
                            ],
                        }
                    ],
                    "output": {"msgs": "{{load.msgs}}"},
                }
            ),
            encoding="utf-8",
        )

    write("/api/channels/general/messages")
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["ok"] and isinstance(r["output"]["msgs"], list), r
    write("/api/never-called")
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["error"] == "step_failed" and "no response from /api/never-called" in r["message"], r


def test_paste_html_rejects_empty():
    from replay_anything.browser import ActionFailed, run_actions

    with pytest.raises(ActionFailed, match=r"action 1 \(paste_html #ed\): ValueError: .*nothing to paste"):
        run_actions(None, [{"paste_html": {"selector": "#ed", "html": ""}}], SITE, lambda v: v)


def _tricky(tmp_path, actions, name="t"):
    import yaml

    p = tmp_path / f"{name}.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"h": {"type": "string"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/tricky/t"},
                            {"paste_html": {"selector": "#ed", "html": "<p>{{inputs.h}}</p>"}},
                            *actions,
                        ],
                    }
                ],
                "output": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.mark.browser
def test_click_skips_hidden_copy(site, tmp_path):
    """Some sites keep a hidden copy of a button earlier in the page; a strict click on both fails or hits the copy."""
    save_login()
    p = _tricky(
        tmp_path,
        [{"click": {"selector": "button[name=send]", "wait_response": {"url": "/rich/t/send", "status": 201}}}],
    )
    r = api.run_recipe(str(p), {"h": "visible-click"}, allow_path=True)
    assert r["ok"], r


@pytest.mark.browser
def test_desktop_window_shows_wide_labels(site, tmp_path):
    """A label shown only on wide screens (e.g. "Add Responsible" vs "Add") must be there headless too."""
    save_login()
    p = _tricky(
        tmp_path, [{"wait_for": {"selector": ".wide:visible", "has_text": "message", "timeout": 3000}}], name="w"
    )
    r = api.run_recipe(str(p), {"h": "x"}, allow_path=True)
    assert r["ok"], r


@pytest.mark.browser
def test_dry_run_stops_before_submit(site, tmp_path):
    sid = save_login()
    p = _tricky(tmp_path, [{"click": {"selector": "button[name=send]"}, "submits": True}])
    r = api.run_recipe(str(p), {"h": "dry"}, allow_path=True, dry_run=True)
    assert r == {
        "ok": True,
        "dry_run": True,
        "stopped_before": "action 3 (click button[name=send])",
        "note": "everything before this ran; nothing was submitted",
    }, r
    assert not any("dry" in m.get("html", "") for m in messages(sid, "t"))
    r = api.run_recipe("post_chat", {"channel": "general", "text": "dry2"}, dry_run=True)  # request step
    assert (
        r["dry_run"]
        and r["stopped_before"].startswith("step send (POST")
        and messages(sid)[-1:] == []
        or all(m["text"] != "dry2" for m in messages(sid))
    )


@pytest.mark.browser
def test_failure_names_action_and_saves_screenshot(site, tmp_path, home):
    save_login()
    p = _tricky(tmp_path, [{"click": {"selector": "button[name=nope]", "timeout": 1500}}])
    r = api.run_recipe(str(p), {"h": "x"}, allow_path=True)
    assert r["error"] == "step_failed" and "action 3 (click button[name=nope])" in r["message"], r
    saved = sorted((home / "failures").iterdir())
    assert saved and (saved[-1] / "page.png").stat().st_size > 0
    assert "/tricky/t" in (saved[-1] / "page.txt").read_text(encoding="utf-8")


@pytest.mark.browser
def test_site_change_fails_with_fix_hint_and_a_patched_recipe_runs(site, tmp_path, home):
    """The site renamed its Send button. The failure must say how to fix the recipe; patching only the
    broken step (as the skill's "Fix a recipe" section says) must make it work again."""
    import yaml

    sid = save_login()
    old = [
        {"goto": "/redesigned/t"},
        {"fill": {"selector": "#box", "value": "{{inputs.h}}"}},
        {"click": {"selector": "button[name=send]", "timeout": 1500, "wait_response": {"url": "/rich/t/send"}}},
    ]
    body = {
        "name": "post_r",
        "description": "d",
        "site": SITE,
        "auth": "session",
        "inputs": {"h": {"type": "string"}},
        "steps": [{"id": "post", "browser": old}],
        "output": {"ok": True},
    }
    p = tmp_path / "post_r.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")

    r = api.run_recipe(str(p), {"h": "first"}, allow_path=True)
    assert r["error"] == "step_failed" and "action 3 (click button[name=send])" in r["message"], r
    assert r["fix"]["recipe_file"] == str(p), r
    assert "Fix a recipe" in r["fix"]["how"] and (Path(r["fix"]["failure_folder"]) / "page.txt").is_file(), r

    body["steps"][0]["browser"][2]["click"]["selector"] = "button[name=post]"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    assert api.run_recipe(str(p), {"h": "second"}, allow_path=True)["ok"]
    assert messages(sid, "t")[-1]["html"] == "second"


def test_fix_hint_only_when_the_site_may_have_changed(site, tmp_path):
    """No hint for wrong inputs, or when the recipe itself can't do what was asked."""
    import yaml

    save_login()
    r = api.run_recipe("post_chat", {"channel": "x"})
    assert r["error"] == "bad_inputs" and "fix" not in r, r
    r = api.run_recipe("post_chat_ws", {"channel": "x", "text": "y"}, browser_only=True)
    assert r["error"] == "step_failed" and "no browser fallback" in r["message"] and "fix" not in r, r
    p = tmp_path / "read.yaml"
    body = {
        "name": "read",
        "description": "d",
        "site": SITE,
        "auth": "session",
        "steps": [{"id": "a", "browser": [{"goto": "/c/x"}]}],
    }
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    r = api.run_recipe(str(p), {}, allow_path=True, dry_run=True)
    assert r["error"] == "step_failed" and "dry run" in r["message"] and "fix" not in r, r


@pytest.mark.browser
def test_failed_fallback_keeps_its_screenshot(site, tmp_path):
    """The request step fails and so does the browser fallback: the hint points at the fallback's screenshot."""
    import yaml

    save_login()
    body = {
        "name": "fb",
        "description": "d",
        "site": SITE,
        "auth": "session",
        "steps": [{"id": "a", "request": "GET /api/nope", "expect": {"status": 200}}],
        "fallback": {"browser": [{"goto": "/c/x"}, {"click": {"selector": "button[name=gone]", "timeout": 1000}}]},
    }
    p = tmp_path / "fb.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    r = api.run_recipe(str(p), {}, allow_path=True)
    assert r["error"] == "step_failed" and r.get("fallback_error"), r
    assert (Path(r["fix"]["failure_folder"]) / "page.png").is_file(), r
    r = api.run_recipe(str(p), {}, allow_path=True, browser_only=True)
    assert r["error"] == "step_failed" and (Path(r["fix"]["failure_folder"]) / "page.png").is_file(), r


def test_submits_must_be_on_a_click(home, tmp_path):
    import yaml

    from replay_anything import recipe as recipes
    from replay_anything.errors import RecipeInvalid

    p = tmp_path / "s.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "s",
                "description": "d",
                "site": SITE,
                "steps": [{"id": "a", "browser": [{"goto": "/", "submits": True}]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RecipeInvalid, match="submits"):
        recipes.load_file(p)


@pytest.mark.browser
def test_fill_with_integer_input(site, tmp_path):
    """{{inputs.n}} alone stays an int; fill must still type it (e.g. a search box)."""
    import yaml

    sid = save_login()
    p = tmp_path / "f.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "f",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"n": {"type": "integer"}},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/c/general"},
                            {"fill": {"selector": "textarea[aria-label='Message']", "value": "{{inputs.n}}"}},
                            {"click": {"selector": "button[aria-label='Send']"}},
                            {"wait_for": {"text": "424242"}},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = api.run_recipe(str(p), {"n": 424242}, allow_path=True)
    assert r["ok"] and messages(sid)[-1]["text"] == "424242", r


@pytest.mark.browser
def test_dry_run_stops_before_enter(site, tmp_path):
    """Pressing Enter sends in many chats; an unmarked press must still stop a dry run."""
    save_login()
    p = _tricky(
        tmp_path,
        [{"press": {"selector": "#ed", "key": "Enter"}}, {"click": {"selector": "button[name=send]"}, "submits": True}],
        name="e",
    )
    r = api.run_recipe(str(p), {"h": "x"}, allow_path=True, dry_run=True)
    assert r["dry_run"] and r["stopped_before"].startswith("action 3 (press"), r


def test_dry_run_refuses_unmarked_recipe(home, tmp_path):
    """No marked submit: a dry run could change the site for real, so it must not start."""
    p = _tricky(tmp_path, [{"click": {"selector": "button[name=send]"}}], name="u")
    r = api.run_recipe(str(p), {"h": "x"}, allow_path=True, dry_run=True)
    assert r["error"] == "step_failed" and "marks no action" in r["message"], r


@pytest.mark.browser
def test_failure_folders_are_unique_private_and_capped(site, tmp_path, home, monkeypatch):
    from replay_anything import browser

    save_login()
    monkeypatch.setattr(browser, "KEEP_FAILURES", 3)
    monkeypatch.setattr(
        browser.time, "strftime", lambda fmt, *a: "20260101-000000" + ("-" if fmt.endswith("-") else "")
    )
    p = _tricky(tmp_path, [{"click": {"selector": "button[name=nope]", "timeout": 500}}], name="c")
    for _ in range(4):  # several failures within the same second
        api.run_recipe(str(p), {"h": "x"}, allow_path=True)
    saved = sorted((home / "failures").iterdir())
    assert len(saved) == 3 and len({s.name for s in saved}) == 3
    assert all((s / "page.png").exists() and (s / "page.txt").exists() for s in saved)


def _loading(tmp_path, actions, name="l"):
    import yaml

    p = tmp_path / f"{name}.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {},
                "steps": [
                    {
                        "id": "post",
                        "browser": [
                            {"goto": "/loading/t"},
                            *actions,
                            {
                                "click": {
                                    "selector": "button[name=send]",
                                    "timeout": 1000,
                                    "wait_response": {"url": "/rich/t/send", "status": 201},
                                }
                            },
                        ],
                    }
                ],
                "output": {"ok": True},
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.mark.browser
def test_wait_for_gone_waits_out_a_late_loading_screen(site, tmp_path):
    """Teams shows "Almost there..." a moment after the chat looks ready; clicking Send under it times out."""
    save_login()
    late = [{"wait_for": {"selector": "button[name=send]"}}, {"wait_for": {"text": "Almost there", "timeout": 3000}}]
    r = api.run_recipe(str(_loading(tmp_path, late, name="early")), {}, allow_path=True)
    assert r["error"] == "step_failed" and "Timeout" in r["message"], r
    # wait for the chat first, as the Teams recipe does: the overlay is not up yet when "gone" starts
    gone = [
        {"wait_for": {"selector": "button[name=send]"}},
        {"wait_for": {"text": "Almost there", "gone": True, "timeout": 10000}},
    ]
    r = api.run_recipe(str(_loading(tmp_path, gone, name="gone")), {}, allow_path=True)
    assert r["ok"], r


@pytest.mark.browser
def test_wait_for_gone_times_out_when_it_stays(site, tmp_path):
    save_login()
    stays = [{"wait_for": {"text": "Send", "gone": True, "timeout": 500}}]
    r = api.run_recipe(str(_loading(tmp_path, stays, name="stays")), {}, allow_path=True)
    assert r["error"] == "step_failed" and "Timeout" in r["message"], r


@pytest.mark.browser
def test_wait_for_gone_skips_a_screen_that_never_shows(site, tmp_path):
    """Nothing matching shows up within `appear` ms: don't wait out the full timeout."""
    import time

    from playwright.sync_api import sync_playwright

    from replay_anything.browser import run_actions

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content("<p>ready</p>")
        t = time.monotonic()
        run_actions(
            page,
            [{"wait_for": {"text": "Almost there", "gone": True, "appear": 300, "timeout": 20000}}],
            SITE,
            lambda v: v,
        )
        assert time.monotonic() - t < 5
        b.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"text": "x", "gone": "false"},
        {"url": "/x", "gone": True},
        {"text": "x", "gon": True},
        {"text": "x", "selector": "y"},
        {"text": "x", "gone": True, "appear": "5s"},
        {"text": "x", "appear": 100},
        {"text": "x", "has_text": "y"},
    ],
)
def test_wait_for_keys_are_checked(tmp_path, bad):
    p = _loading(tmp_path, [{"wait_for": bad}], name="bad")
    with pytest.raises(RecipeInvalid, match="wait_for|gone|appear|has_text"):
        recipes.load_file(p)


@pytest.mark.browser
def test_wait_for_gone_reports_a_bad_selector(site, tmp_path):
    """A broken selector must fail, not count as "the loading screen never showed up"."""
    from playwright.sync_api import sync_playwright

    from replay_anything.browser import ActionFailed, run_actions

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()
        page.set_content("<p>ready</p>")
        with pytest.raises(ActionFailed):
            run_actions(page, [{"wait_for": {"selector": "p[[[", "gone": True, "appear": 300}}], SITE, lambda v: v)
        b.close()


@pytest.mark.browser
def test_for_each_reads_several_pages_in_one_browser(site, tmp_path):
    """for_each repeats a browser step's actions per item in one browser; a failed item is recorded, the rest go on."""
    import yaml

    save_login()
    p = tmp_path / "fe.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "fe",
                "description": "d",
                "site": SITE,
                "auth": "session",
                "inputs": {"channels": {"type": "array"}},
                "steps": [
                    {
                        "id": "load",
                        "for_each": "{{inputs.channels}}",
                        "browser": [
                            {
                                "goto": {
                                    "url": "/spa/{{item}}",
                                    "timeout": 5000,
                                    "wait_response": {
                                        "url": "/api/channels/{{item}}/messages",
                                        "extract": {"msgs": "$.messages"},
                                    },
                                }
                            }
                        ],
                    }
                ],
                "output": {"results": "{{load}}"},
            }
        ),
        encoding="utf-8",
    )
    assert recipes.load_file(p).id  # valid
    r = api.run_recipe(str(p), {"channels": ["general", "random", "no such/../x"]}, allow_path=True)
    assert r["ok"], r
    res = r["output"]["results"]
    assert [x["item"] for x in res] == ["general", "random", "no such/../x"]
    assert res[0]["ok"] and isinstance(res[0]["msgs"], list)
    assert res[1]["ok"] and isinstance(res[1]["msgs"], list)
    assert res[2]["ok"] is False and res[2]["error"] and res[2].get("saved")  # screenshot path kept per item
