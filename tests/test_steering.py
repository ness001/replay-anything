"""Steering agents to recipes: the PreToolUse hook, the MCP wording and install.py --rules."""

import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, SITE

import install
from replay_anything import cli
from replay_anything.hint import hint, sites_in

PW = f"playwright-cli open {SITE}/chat"


@pytest.mark.parametrize(
    "cmd,hosts",
    [
        (PW, ["127.0.0.1:18765"]),
        ("agent-browser open https://teams.microsoft.com/v2/", ["teams.microsoft.com"]),
        ("npx @playwright/cli@latest goto example.com", ["example.com"]),
        ("agent-browser --session s open www.example.com && agent-browser snapshot -i", ["www.example.com"]),
        ("curl https://example.com", []),  # not a browser tool
        ("playwright-cli snapshot", []),  # no site named
        ("echo my-playwright-clinic https://x.com", []),  # look-alike word
    ],
)
def test_sites_in(cmd, hosts):
    assert sites_in(cmd) == hosts


def test_hint_names_recipes_for_the_site_only(home):
    text = hint(PW)
    assert "127.0.0.1_18765/post_chat" in text or "post_chat" in text
    assert "run_recipe" in text and "text: string" in text
    assert hint("playwright-cli open https://unrelated.example.com") is None


def _hook(event, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(event)))
    assert cli.main(["hook"]) == 0
    return capsys.readouterr().out


def test_hook_output_format(home, monkeypatch, capsys):
    out = json.loads(_hook({"tool_name": "Bash", "tool_input": {"command": PW}}, monkeypatch, capsys))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "post_chat" in out["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in out["hookSpecificOutput"]  # never blocks
    assert _hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, monkeypatch, capsys) == ""
    assert _hook({"tool_name": "Read", "tool_input": {"command": PW}}, monkeypatch, capsys) == ""


def test_hook_survives_garbage(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    assert cli.main(["hook"]) == 0 and capsys.readouterr().out == ""


def test_hook_through_launch_and_plugin_config(home):
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    h = cfg["hooks"]["PreToolUse"][0]
    assert h["matcher"] == "Bash"
    args = [a.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)) for a in h["hooks"][0]["args"]]
    env = {**os.environ}

    def run(cmd):
        return subprocess.run(
            [sys.executable, *args],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    r = run(PW)
    assert r.returncode == 0 and "post_chat" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    r = run("git status")
    assert r.returncode == 0 and r.stdout == ""


def test_mcp_wording_mentions_browser_tools():
    from replay_anything import mcp_server

    assert "playwright-cli" in mcp_server.server.instructions
    assert "agent-browser" in mcp_server.list_recipes.__doc__


def test_install_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "HOME", tmp_path)
    agents = tmp_path / ".codex" / "AGENTS.md"
    agents.parent.mkdir()
    agents.write_text("# mine\nkeep me\n", encoding="utf-8")
    assert install.main(["--rules"]) == 0
    assert install.main(["--rules"]) == 0  # idempotent
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# mine\nkeep me\n") and text.count(install.RULES_START) == 1
    assert "list_recipes" in (tmp_path / ".copilot" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert (tmp_path / ".codex" / "AGENTS.md.replay-anything.bak").read_text(encoding="utf-8") == "# mine\nkeep me\n"
    install.main([])  # without --rules the instructions are untouched
    assert agents.read_text(encoding="utf-8") == text


def test_hook_matching_is_not_too_broad(home):
    from replay_anything.hint import _same_site

    assert _same_site("x.com", "www.x.com") and not _same_site("www.x.com", "x.com")
    assert _same_site("microsoft.com", "teams.microsoft.com")  # subdomain of the recipe host
    assert not _same_site("teams.microsoft.com", "microsoft.com")
    assert not _same_site("localhost:8080", "localhost:3000")
    assert sites_in("agent-browser open report.html") == []
    assert hint("playwright-cli open http://127.0.0.1:9999/") is None  # other port


def test_empty_description_does_not_hide_hints(home, tmp_path, monkeypatch):
    import yaml

    d = tmp_path / "r"
    d.mkdir()
    (d / "x.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "blank",
                "description": "",
                "site": SITE,
                "auth": "none",
                "steps": [{"id": "a", "request": "GET /"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REPLAY_ANYTHING_RECIPES", os.pathsep.join([str(d), os.environ["REPLAY_ANYTHING_RECIPES"]]))
    assert "post_chat" in hint(PW)


def test_hook_non_utf8_locale_bytes(home):
    cmd = "git commit -m '" + bytes([0xE4, 0xB8, 0x81, 0xF0, 0x9F, 0x98, 0x81]).decode("utf-8") + "'"
    r = subprocess.run(
        [sys.executable, str(ROOT / "launch.py"), "cli", "hook"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""


def test_install_rules_refuses_broken_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "HOME", tmp_path)
    agents = tmp_path / ".codex" / "AGENTS.md"
    agents.parent.mkdir()
    agents.write_text(f"{install.RULES_START}\nmine\n", encoding="utf-8")
    assert install.main(["--codex", "--rules"]) == 1
    assert agents.read_text(encoding="utf-8") == f"{install.RULES_START}\nmine\n"


def _run_hook(args, event):
    return subprocess.run(
        [sys.executable, *args],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "REPLAY_ANYTHING_AUTO_RECIPE"},
        timeout=120,
    )


def test_codex_plugin_files(home):
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "replay-anything" and manifest["skills"] == "./skills/"
    mcp = json.loads((ROOT / manifest["mcpServers"]).read_text(encoding="utf-8"))["mcpServers"]["replay-anything"]
    assert (
        mcp["args"] == ["./launch.py", "mcp"] and mcp["cwd"] == "."
    )  # Codex doesn't expand ${CLAUDE_PLUGIN_ROOT} here
    hooks = json.loads((ROOT / manifest["hooks"]).read_text(encoding="utf-8"))["hooks"]
    claude = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == set(claude)
    cmd = hooks["PreToolUse"][0]["hooks"][0]["command"]
    assert "args" not in hooks["PreToolUse"][0]["hooks"][0]
    args = cmd.replace("${PLUGIN_ROOT}", str(ROOT)).split('"')[1:]
    r = _run_hook(
        [args[0], *args[1].split()],
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": PW}},
    )
    assert "post_chat" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_copilot_hooks_file(home):
    cfg = json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))
    assert cfg["version"] == 1 and set(cfg["hooks"]) == {"PostToolUse", "Stop"}
    h = cfg["hooks"]["PostToolUse"][0]["hooks"][0]
    args = [a.replace("${PLUGIN_ROOT}", str(ROOT)) for a in h["args"]]
    # Copilot can't add context before a tool runs: the note comes after, as plain additionalContext
    r = _run_hook(
        args,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "cp",
            "tool_name": "Bash",
            "tool_input": {"command": PW},
            "tool_response": {},
        },
    )
    assert "post_chat" in json.loads(r.stdout)["additionalContext"]
    r = _run_hook(
        args,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "cp",
            "tool_name": "Bash",
            "tool_input": {"command": "playwright-cli open https://cp-new.example.com/"},
        },
    )
    assert r.stdout == ""
    out = json.loads(_run_hook(args, {"hook_event_name": "Stop", "session_id": "cp"}).stdout)
    assert out["decision"] == "block" and "cp-new.example.com" in out["reason"]


def test_copilot_failed_command_is_not_recorded(home):
    args = [
        a.replace("${PLUGIN_ROOT}", str(ROOT))
        for a in json.loads((ROOT / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PostToolUse"][0]["hooks"][0][
            "args"
        ]
    ]
    _run_hook(
        args,
        {
            "hook_event_name": "PostToolUse",
            "session_id": "cpf",
            "tool_name": "Bash",
            "tool_input": {"command": "playwright-cli open https://cp-fail.example.com/"},
            "tool_result": {
                "result_type": "success",  # what Copilot really sent for a failed command
                "text_result_for_llm": "Unknown command\n<exited with exit code 1>",
            },
        },
    )
    assert _run_hook(args, {"hook_event_name": "Stop", "session_id": "cpf"}).stdout == ""


def test_versions_agree():
    """Plugins only refresh on update when the version changes: keep all three in step."""
    import re
    import tomllib

    py = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    for f in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert json.loads((ROOT / f).read_text(encoding="utf-8"))["version"] == py, f
    assert re.fullmatch(r"\d+\.\d+\.\d+", py)
    from replay_anything import __version__

    assert __version__ == py
