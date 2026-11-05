"""Start the Replay-Anything MCP server or CLI from a source checkout, creating its venv on first use.

Used by the plugin for Claude Code, Codex and Copilot (MCP server and hooks) and by install.py, so no
manual setup is needed:
    python launch.py mcp          # MCP server on stdio
    python launch.py cli ARGS...  # CLI
The venv lives in <checkout>/.venv. Set PIP_INDEX_URL if your network needs a package mirror.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> None:
    if (
        PY.exists()
        and subprocess.run(
            [str(PY), "-c", "import replay_anything, mcp"], cwd=str(VENV), capture_output=True
        ).returncode
        == 0
    ):
        return
    print("replay-anything: first run, installing dependencies...", file=sys.stderr, flush=True)
    if not PY.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)], stdout=sys.stderr)
    subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "-e", str(ROOT)], stdout=sys.stderr)


def _auto_pending(stdin: str) -> bool:
    """Stop runs every turn: only start the venv if this session drove a site (see replay_anything/auto.py)."""
    import json
    import re

    try:
        sid = str(json.loads(stdin).get("session_id") or "default")
    except ValueError:
        return False
    base = os.environ.get("REPLAY_ANYTHING_HOME") or (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "replay-anything")
        if os.name == "nt"
        else os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "replay-anything")
    )
    d = Path(base) / "auto" / re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80]
    return any(d.glob("*.seen")) if d.is_dir() else False


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("mcp", "cli"):
        print(__doc__, file=sys.stderr)
        return 2
    if sys.argv[2:3] == ["hook"]:  # every Bash call (Claude Code, Codex, Copilot): skip unrelated ones without the venv
        stdin = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if not any(t in stdin for t in ("playwright-cli", "agent-browser", "@playwright/cli")):
            if '"Stop"' not in stdin or not _auto_pending(stdin):
                return 0
        if not PY.exists():  # never install from a hook (short timeout); the MCP server sets the venv up
            return 0
        try:
            subprocess.run(
                [str(PY), "-m", "replay_anything.cli", *sys.argv[2:]], input=stdin, encoding="utf-8", timeout=15
            )
            return 0  # a failing hook must never get in the way
        except Exception:
            return 0
    ensure_venv()
    module = "replay_anything.mcp_server" if sys.argv[1] == "mcp" else "replay_anything.cli"
    return subprocess.call([str(PY), "-m", module, *sys.argv[2:]])


if __name__ == "__main__":
    sys.exit(main())
