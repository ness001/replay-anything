"""Auto-save: hooks track browser use without a recipe and ask once at Stop; on/off switch; tested mark."""

import io
import json
import os
import subprocess
import sys

import yaml
from conftest import ROOT, SITE, messages, save_login

from replay_anything import api, auto, cli

NEW = "playwright-cli open https://new-site.example.com/page"


def _hook(event, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    assert cli.main(["hook"]) == 0
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def _post(cmd, sid="s1", **resp):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": sid,
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": False, **resp},
    }


def _stop(sid="s1", active=False):
    return {"hook_event_name": "Stop", "session_id": sid, "stop_hook_active": active}


def test_stop_asks_once_per_site(home, monkeypatch, capsys):
    monkeypatch.delenv(auto.ENV, raising=False)
    assert _hook(_post(NEW), monkeypatch, capsys) is None  # PostToolUse prints nothing
    out = _hook(_stop(), monkeypatch, capsys)
    assert out["decision"] == "block" and "new-site.example.com" in out["reason"]
    assert _hook(_stop(), monkeypatch, capsys) is None  # never asks twice
    assert _hook(_stop("other"), monkeypatch, capsys) is None  # other session saw nothing


def test_no_ask_when_recipe_exists_or_unrelated(home, monkeypatch, capsys):
    monkeypatch.delenv(auto.ENV, raising=False)
    _hook(_post(f"playwright-cli open {SITE}/chat"), monkeypatch, capsys)  # post_chat covers it
    _hook(_post("git status"), monkeypatch, capsys)
    _hook(_post(NEW, interrupted=True), monkeypatch, capsys)  # interrupted command
    _hook(_post(NEW, is_error=True), monkeypatch, capsys)  # command that failed
    assert _hook(_stop(), monkeypatch, capsys) is None


def test_stop_hook_active_does_not_loop(home, monkeypatch, capsys):
    monkeypatch.delenv(auto.ENV, raising=False)
    _hook(_post(NEW), monkeypatch, capsys)
    assert _hook(_stop(active=True), monkeypatch, capsys) is None


def test_switch_off_on_and_env(home, monkeypatch, capsys):
    monkeypatch.delenv(auto.ENV, raising=False)
    assert auto.enabled()  # on by default
    assert cli.main(["auto", "off"]) == 0 and not auto.enabled()
    _hook(_post(NEW), monkeypatch, capsys)
    assert _hook(_stop(), monkeypatch, capsys) is None
    monkeypatch.setenv(auto.ENV, "1")  # env wins over settings
    assert auto.enabled()
    monkeypatch.setenv(auto.ENV, "0")
    assert cli.main(["auto", "on"]) == 0 and not auto.enabled()
    monkeypatch.delenv(auto.ENV)
    assert auto.enabled() and auto.status() == {"auto_recipe": "on", "source": "settings"}


def test_untested_recipe_listed_then_marked_after_success(site, tmp_path, monkeypatch):
    data = yaml.safe_load((ROOT / "examples" / "local-chat" / "post_chat.yaml").read_text(encoding="utf-8"))
    data.update(name="auto_post", tested=False)
    d = tmp_path / "r"
    d.mkdir()
    f = d / "auto_post.yaml"
    f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("REPLAY_ANYTHING_RECIPES", os.pathsep.join([str(d), os.environ["REPLAY_ANYTHING_RECIPES"]]))
    listed = {r["name"]: r for r in api.list_recipes()["recipes"]}
    assert listed["auto_post"]["tested"] is False and "tested" not in listed["post_chat"]
    sid = save_login()
    r = api.run_recipe("auto_post", {"channel": "general", "text": "first"})
    assert r["ok"] and "marked tested" in r["note"] and messages(sid)[-1]["text"] == "first"
    assert yaml.safe_load(f.read_text(encoding="utf-8"))["tested"] is True
    assert "note" not in api.run_recipe("auto_post", {"channel": "general", "text": "second"})


def test_bad_tested_value_rejected(home, tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text(
        yaml.safe_dump(
            {"name": "x", "description": "d", "site": SITE, "tested": "no", "steps": [{"id": "a", "request": "GET /"}]}
        ),
        encoding="utf-8",
    )
    assert cli.main(["validate", str(f)]) == 1


def test_stop_through_plugin_config(home):
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert {"PreToolUse", "PostToolUse", "Stop"} <= set(cfg)
    args = [a.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)) for a in cfg["Stop"][0]["hooks"][0]["args"]]
    env = {k: v for k, v in os.environ.items() if k != auto.ENV}

    # The MCP server creates the venv before a plugin hook needs it.
    boot = subprocess.run(
        [sys.executable, str(ROOT / "launch.py"), "cli", "auto", "status"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert boot.returncode == 0, boot.stderr

    def run(ev):
        return subprocess.run(
            [sys.executable, *args], input=json.dumps(ev), capture_output=True, text=True, env=env, timeout=120
        )

    assert run(_post(NEW, sid="e2e")).stdout == ""
    out = json.loads(run(_stop("e2e")).stdout)
    assert "new-site.example.com" in out["reason"]
    assert run(_stop("no-browser-work")).stdout == ""  # filtered in launch.py, no venv start


def test_parallel_hooks_keep_every_site_and_ask_once(home, monkeypatch):
    monkeypatch.delenv(auto.ENV, raising=False)
    from concurrent.futures import ThreadPoolExecutor

    hosts = [f"s{i}.example.com" for i in range(12)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda h: auto.note_command("par", f"agent-browser open https://{h}/"), hosts))
    with ThreadPoolExecutor(4) as ex:
        msgs = [m for m in ex.map(lambda _: auto.stop_message("par"), range(4)) if m]
    assert all(sum(h + " " in m or h + "," in m for m in msgs) == 1 for h in hosts)


def test_mark_tested_handles_yaml_forms(tmp_path):
    f = tmp_path / "r.yaml"
    for line in ("tested: false  # auto-saved", "tested: False", "tested: no"):
        f.write_text(f"name: x\n{line}\nsteps: []\n", encoding="utf-8")
        assert auto.mark_tested(f)
        assert yaml.safe_load(f.read_text(encoding="utf-8"))["tested"] is True
        assert not auto.mark_tested(f)  # nothing to change the second time


def test_status_ignores_unknown_env(home, monkeypatch):
    monkeypatch.setenv(auto.ENV, "maybe")
    assert auto.status() == {"auto_recipe": "on", "source": "default"}
