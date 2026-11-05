"""Sharing logins with playwright-cli and agent-browser: link to their files, export ours."""

import json
import shutil
import subprocess

import httpx
import pytest
from conftest import SITE, messages, save_login

from replay_anything import api, cli, sessions


def _state(sid, extra_domain=None):
    cookies = [
        {
            "name": "sid",
            "value": sid,
            "domain": "127.0.0.1",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": False,
            "sameSite": "Lax",
        }
    ]
    if extra_domain:
        cookies.append(
            {
                "name": "other",
                "value": "secret",
                "domain": extra_domain,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return {"cookies": cookies, "origins": [{"origin": "https://other.example", "localStorage": []}]}


def _login_sid(user="bob"):
    return httpx.post(f"{SITE}/login", data={"user": user, "password": "pw"}).cookies["sid"]


def test_linked_state_file_is_used_and_read_fresh(site, tmp_path):
    f = tmp_path / "pw.json"
    f.write_text(json.dumps(_state("stale")), encoding="utf-8")
    assert cli.main(["link", SITE, "--state", str(f)]) == 0
    assert sessions.linked(SITE) == {"state": str(f.resolve())}
    # the other tool refreshes its file later: no relink needed
    sid = _login_sid()
    f.write_text(json.dumps(_state(sid, extra_domain=".other.example")), encoding="utf-8")
    loaded = sessions.load(SITE)
    assert [c["name"] for c in loaded["cookies"]] == ["sid"] and loaded["origins"] == []
    r = api.run_recipe("post_chat", {"channel": "general", "text": "via link"})
    assert r["ok"], r
    assert messages(sid)[-1]["text"] == "via link"


def test_linked_file_missing_reports_session_expired(site, tmp_path):
    f = tmp_path / "pw.json"
    f.write_text(json.dumps(_state("x")), encoding="utf-8")
    sessions.link(SITE, state=str(f))
    f.unlink()
    assert api.run_recipe("post_chat", {"channel": "general", "text": "x"})["error"] == "session_expired"


def test_link_rejects_bad_input(home, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"cookies": "nope"}', encoding="utf-8")
    assert cli.main(["link", SITE, "--state", str(bad)]) == 1
    assert cli.main(["link", SITE, "--state", str(tmp_path / "missing.json")]) == 1
    assert cli.main(["link", SITE, "--profile", str(tmp_path / "nofolder")]) == 1
    assert sessions.linked(SITE) is None


def test_link_profile_and_remove(home, tmp_path):
    prof = tmp_path / "prof"
    prof.mkdir()
    sessions.link(SITE, profile=str(prof))
    assert sessions.profile(SITE) == str(prof.resolve())
    assert cli.main(["link", SITE, "--remove"]) == 0
    assert sessions.linked(SITE) is None and sessions.profile(SITE) is None


def test_export_state_round_trip(site, tmp_path):
    sid = save_login()
    out = tmp_path / "out.json"
    assert cli.main(["export-state", SITE, str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["cookies"][0]["value"] == sid
    assert cli.main(["export-state", "http://nosession.example", str(tmp_path / "x.json")]) == 1


def _tool_state(tool, tmp_path):
    """Sign in with the real tool on the test site and save its state; skip if it isn't installed."""
    exe = shutil.which(tool)
    if not exe:
        pytest.skip(f"{tool} not installed")
    out = tmp_path / f"{tool}.json"
    sess = "ra-test"
    if tool == "playwright-cli":
        cmds = [
            ["-s=" + sess, "open", f"{SITE}/login"],
            [
                "-s=" + sess,
                "run-code",
                "async page => { await page.fill('[name=user]', 'carol'); await page.fill('[name=password]', 'pw');"
                " await Promise.all([page.waitForNavigation(), page.locator('form').first().evaluate(f => f.submit())]); }",
            ],
            ["-s=" + sess, "state-save", str(out)],
            ["-s=" + sess, "close"],
        ]
    else:
        cmds = [
            ["--session", sess, "open", f"{SITE}/login"],
            ["--session", sess, "fill", "[name=user]", "carol"],
            ["--session", sess, "fill", "[name=password]", "pw"],
            ["--session", sess, "eval", "document.querySelector('form').submit()"],
            ["--session", sess, "wait", "1500"],
            ["--session", sess, "state", "save", str(out)],
            ["--session", sess, "close"],
        ]
    log = tmp_path / f"{tool}.log"
    with open(log, "w") as fh:  # not pipes: the tool's daemon would keep them open and hang the run
        for c in cmds:
            subprocess.run([exe, *c], cwd=tmp_path, timeout=120, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh)
    if not out.is_file():
        pytest.fail(f"{tool} did not save a state file:\n{log.read_text()}")
    return out


@pytest.mark.browser
@pytest.mark.parametrize("tool", ["playwright-cli", "agent-browser"])
def test_real_tool_state_runs_recipe(site, tmp_path, tool):
    f = _tool_state(tool, tmp_path)
    sessions.link(SITE, state=str(f))
    sid = next(c["value"] for c in sessions.load(SITE)["cookies"] if c["name"] == "sid")
    r = api.run_recipe("post_chat", {"channel": "general", "text": f"from {tool}"})
    assert r["ok"], r
    assert messages(sid)[-1]["text"] == f"from {tool}"


def test_profile_link_never_uses_old_state(site, tmp_path):
    save_login()
    prof = tmp_path / "prof"
    prof.mkdir()
    sessions.link(SITE, profile=str(prof))
    assert sessions.load(SITE) is None  # not the stale state.json
    assert cli.main(["export-state", SITE, str(tmp_path / "o.json")]) == 1


def test_linked_file_without_site_login_is_session_expired(site, tmp_path):
    f = tmp_path / "pw.json"
    f.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    assert cli.main(["link", SITE, "--state", str(f)]) == 0  # allowed, with a warning
    r = api.run_recipe("post_chat", {"channel": "general", "text": "x"})
    assert r["error"] == "session_expired" and "no login for this site" in r["message"]


def test_corrupt_linked_file_is_session_expired(site, tmp_path):
    f = tmp_path / "pw.json"
    f.write_text(json.dumps(_state(_login_sid())), encoding="utf-8")
    sessions.link(SITE, state=str(f))
    f.write_text("{half", encoding="utf-8")
    assert api.run_recipe("post_chat", {"channel": "general", "text": "x"})["error"] == "session_expired"
    sessions._link_file(SITE).write_text("{bad", encoding="utf-8")
    assert api.run_recipe("post_chat", {"channel": "general", "text": "x"})["error"] == "session_expired"


def test_ip_host_ignores_suffix_domains():
    state = {
        "cookies": [
            {"name": "a", "value": "1", "domain": "0.0.1"},
            {"name": "b", "value": "2", "domain": "com"},
            {"name": "c", "value": "3", "domain": "127.0.0.1"},
        ],
        "origins": [],
    }
    assert [c["name"] for c in sessions._for_site(state, SITE)["cookies"]] == ["c"]
    assert [c["name"] for c in sessions._for_site(state, "https://x.example.com")["cookies"]] == []
