import json
import sys

import pytest
from conftest import EXAMPLES, ROOT, messages, save_login

import install


def test_install_codex_and_copilot(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "HOME", tmp_path)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "o"\n')
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "mcp-config.json").write_text(json.dumps({"mcpServers": {"other": {"command": "o"}}}))
    install.main([])
    install.main([])  # idempotent
    toml = (tmp_path / ".codex" / "config.toml").read_text()
    assert toml.count("[mcp_servers.replay-anything]") == 1 and "[mcp_servers.other]" in toml
    import tomllib

    cfg = tomllib.loads(toml)["mcp_servers"]["replay-anything"]
    assert cfg["args"][-1] == "mcp" and cfg["args"][0].endswith("launch.py")
    cop = json.loads((tmp_path / ".copilot" / "mcp-config.json").read_text())["mcpServers"]
    assert set(cop) == {"other", "replay-anything"}
    assert (tmp_path / ".agents" / "skills" / "replay-anything" / "SKILL.md").is_file()
    assert (tmp_path / ".copilot" / "skills" / "replay-anything" / "SKILL.md").is_file()
    assert (tmp_path / ".codex" / "config.toml.replay-anything.bak").is_file()


def test_plugin_files():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "replay-anything"
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    assert mcp["replay-anything"]["args"] == ["${CLAUDE_PLUGIN_ROOT}/launch.py", "mcp"]
    skill = (ROOT / "skills" / "replay-anything" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: replay-anything\n")


@pytest.mark.anyio
async def test_mcp_over_stdio_via_launcher(site, home):
    """The way agents start it: `python launch.py mcp` over stdio."""
    from mcp.client.client import Client
    from mcp.client.stdio import StdioServerParameters

    sid = save_login()
    import os

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "launch.py"), "mcp"],
        env={**os.environ, "REPLAY_ANYTHING_HOME": str(home), "REPLAY_ANYTHING_RECIPES": str(EXAMPLES)},
    )
    async with Client(params) as client:
        assert sorted(t.name for t in (await client.list_tools()).tools) == ["list_recipes", "run_recipe"]
        res = await client.call_tool("run_recipe", {"recipe": "post_chat", "inputs": {"channel": "x", "text": "stdio"}})
        assert res.structured_content["ok"], res
    assert messages(sid, "x")[0]["text"] == "stdio"


def test_install_keeps_original_backup_and_user_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "HOME", tmp_path)
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[mcp_servers."replay-anything"]\ncommand = "old"\n[mcp_servers."replay-anything".env]\nA = "1"\n')
    install.main(["--codex"])
    install.main(["--codex"])
    import tomllib

    assert "old" in (tmp_path / ".codex" / "config.toml.replay-anything.bak").read_text()
    parsed = tomllib.loads(cfg.read_text())
    assert "env" not in parsed["mcp_servers"]["replay-anything"]
    skill = tmp_path / ".agents" / "skills" / "replay-anything" / "SKILL.md"
    skill.write_text("my edits")
    install.main(["--codex"])
    assert skill.read_text() == "my edits"


def test_install_bad_config_changes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "HOME", tmp_path)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("ok = 1\n")
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "mcp-config.json").write_text("{bad")
    assert install.main([]) == 1
    assert (tmp_path / ".codex" / "config.toml").read_text() == "ok = 1\n"
