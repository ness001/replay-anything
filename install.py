"""Register the Replay-Anything MCP server and copy the build skill for Codex and GitHub Copilot.

    python install.py [--codex] [--copilot] [--rules] [--dry-run]   (no target flag = both)

--rules also adds a short "check recipes before driving a browser" block to the agent's global
instructions (~/.codex/AGENTS.md, ~/.copilot/copilot-instructions.md), between marker lines so it is
replaced, not duplicated, on reinstall.

Claude Code users install the plugin instead (this folder is a Claude Code plugin).
Before changing a config file for the first time it is copied to <file>.replay-anything.bak;
other entries are kept. All files are checked before any is written.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "skills" / "replay-anything"
HOME = Path.home()
NAME = "replay-anything"


def server_cmd() -> tuple[str, list[str]]:
    return sys.executable, [str(ROOT / "launch.py"), "mcp"]


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = p.with_name(p.name + ".replay-anything.bak")
    if p.exists() and not bak.exists():
        shutil.copy2(p, bak)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _skill_plan(dest: Path) -> str | None:
    """None if the skill can be copied; a reason if the user changed an installed copy."""
    if dest.is_symlink():
        return f"{dest} is a link; leaving it alone"
    if dest.exists():
        cmp = filecmp.dircmp(SKILL, dest)
        if cmp.diff_files or cmp.right_only:
            return f"{dest} has local edits; leaving it alone (delete it to reinstall)"
    return None


def _copy_skill(dest: Path, dry: bool) -> str:
    reason = _skill_plan(dest)
    if reason:
        return f"skipped skill: {reason}"
    if not dry:
        shutil.copytree(SKILL, dest, dirs_exist_ok=True)
    return f"skill -> {dest}"


def plan_codex() -> tuple[Path, str]:
    cfg = HOME / ".codex" / "config.toml"
    cmd, args = server_cmd()
    q = json.dumps  # JSON strings are valid TOML basic strings
    block = (
        f"[mcp_servers.{NAME}]\ncommand = {q(cmd)}\n"
        f"args = [{', '.join(q(a) for a in args)}]\nstartup_timeout_sec = 300\n"
    )
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    tomllib.loads(text)  # refuse to touch a file we cannot parse
    # our table and its sub-tables, bare or quoted key, up to the next unrelated table
    key = rf"(?:{re.escape(NAME)}|\"{re.escape(NAME)}\")"
    pattern = re.compile(rf"^\[mcp_servers\.{key}(?:\.[^\]]+)?\]\n(?:(?!\[).*\n?)*", re.M)
    if pattern.search(text):
        first = True

        def repl(_):
            nonlocal first
            out, first = (block if first else ""), False
            return out

        new = pattern.sub(repl, text)
    else:
        new = text.rstrip("\n") + ("\n\n" if text else "") + block
    tomllib.loads(new)
    return cfg, new


def plan_copilot() -> tuple[Path, str]:
    cfg = HOME / ".copilot" / "mcp-config.json"
    cmd, args = server_cmd()
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    data.setdefault("mcpServers", {})[NAME] = {"type": "stdio", "command": cmd, "args": args, "tools": ["*"]}
    return cfg, json.dumps(data, indent=2)


RULES_START = "<!-- replay-anything:start -->"
RULES_END = "<!-- replay-anything:end -->"
RULES = f"""{RULES_START}
## Website recipes (Replay-Anything)
- Before driving a browser (playwright-cli, agent-browser, Playwright), call the `list_recipes` MCP tool
  with the site. If a recipe fits the task, use `run_recipe` instead of browser steps.
- Use the browser when no recipe fits or `run_recipe` returns `step_failed`.
- Auto-save: after a successful browser task the user may repeat, on a site with no recipe, save it as a
  recipe with the `replay-anything` skill (section "Auto-save") and tell the user its id in one line.
  Skip this when `replay-anything auto status` says off.
{RULES_END}
"""


def plan_rules(path: Path) -> tuple[Path, str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    starts, ends = text.count(RULES_START), text.count(RULES_END)
    if (starts, ends) not in ((0, 0), (1, 1)) or (starts and text.index(RULES_START) > text.index(RULES_END)):
        raise ValueError(f"{path} has broken replay-anything marker lines; fix or remove them by hand")
    block = re.compile(re.escape(RULES_START) + r".*?" + re.escape(RULES_END) + r"\n?", re.S)
    if block.search(text):
        return path, block.sub(lambda _: RULES, text, count=1)
    return path, text.rstrip("\n") + ("\n\n" if text.strip() else "") + RULES


RULES_FILES = {
    "codex": lambda: HOME / ".codex" / "AGENTS.md",
    "copilot": lambda: HOME / ".copilot" / "copilot-instructions.md",
}

TARGETS = {
    "codex": (plan_codex, lambda: HOME / ".agents" / "skills" / NAME),
    "copilot": (plan_copilot, lambda: HOME / ".copilot" / "skills" / NAME),
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--codex", action="store_true")
    p.add_argument("--copilot", action="store_true")
    p.add_argument("--rules", action="store_true", help="add the recipe-first rule to global agent instructions")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    names = [t for t in TARGETS if getattr(a, t)] or list(TARGETS)
    plans = []
    for t in names:  # check everything first so a bad file never leaves a half install
        try:
            plans.append((t, *TARGETS[t][0]()))
            if a.rules:
                plans.append((t + " rules", *plan_rules(RULES_FILES[t]())))
        except (ValueError, OSError, tomllib.TOMLDecodeError) as e:
            print(f"error: cannot update the {t} config ({e}); nothing was changed", file=sys.stderr)
            return 1
    verb = "would write" if a.dry_run else "wrote"
    for t, cfg, text in plans:
        if not a.dry_run:
            _write(cfg, text)
        if t.endswith(" rules"):
            print(f"{verb} {t} -> {cfg}")
            continue
        print(f"{verb} {t} mcp -> {cfg}")
        print(f"{verb} " + _copy_skill(TARGETS[t][1](), a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
