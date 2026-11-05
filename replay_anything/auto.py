"""Auto-save recipes: after an agent drives a site with playwright-cli / agent-browser and no recipe
covers it, ask the agent (once per site per session) to save what it did as a recipe.

On by default. Turn off with `replay-anything auto off`, or for one session with
REPLAY_ANYTHING_AUTO_RECIPE=0 (1 forces it on).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import recipe as recipes
from .hint import _same_site, sites_in
from .paths import home

ENV = "REPLAY_ANYTHING_AUTO_RECIPE"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def _settings_file() -> Path:
    return home() / "settings.json"


def _settings() -> dict:
    try:
        data = json.loads(_settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled() -> bool:
    env = os.environ.get(ENV, "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    return _settings().get("auto_recipe", True) is not False


def set_enabled(on: bool) -> None:
    data = _settings()
    data["auto_recipe"] = on
    tmp = _settings_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_file())


def status() -> dict:
    env = os.environ.get(ENV, "").strip().lower()
    known = env in ("0", "false", "off", "no", "1", "true", "on", "yes")
    return {
        "auto_recipe": "on" if enabled() else "off",
        "source": f"{ENV}={env}" if known else ("settings" if "auto_recipe" in _settings() else "default"),
    }


_cleaned = False


def session_dir(session_id: str) -> Path:
    """One folder per session with a marker file per site: <host>.seen, then <host>.asked.
    Creating a file is atomic, so parallel hook processes can't overwrite each other's updates."""
    global _cleaned
    base = home() / "auto"
    base.mkdir(exist_ok=True)
    if not _cleaned:  # sessions older than a week are finished
        _cleaned = True
        for old in base.iterdir():
            try:
                if old.stat().st_mtime < time.time() - 7 * 86400:
                    shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink()
            except OSError:
                pass
    d = base / _SAFE_ID.sub("_", session_id or "default")[:80]
    d.mkdir(exist_ok=True)
    return d


def _marker(host: str) -> str:
    return _SAFE_ID.sub("_", host)


def _has_recipe(host: str) -> bool:
    catalog, _ = recipes.catalog()
    return any(_same_site(r.host, host) for r in catalog)


def note_command(session_id: str, command: str, failed: bool = False) -> None:
    """PostToolUse: remember sites driven with a browser tool that have no recipe yet."""
    if failed or not enabled():
        return
    hosts = [h for h in sites_in(command) if not _has_recipe(h)]
    if not hosts:
        return
    d = session_dir(session_id)
    for h in hosts:
        (d / f"{_marker(h)}.seen").write_text(h, encoding="utf-8")


def _claim(path: Path) -> bool:
    """True for exactly one caller: creates the file only if it doesn't exist yet."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def stop_message(session_id: str, stop_hook_active: bool = False) -> str | None:
    """Stop: the instruction to save a recipe, or None. Asks at most once per site per session."""
    if stop_hook_active or not enabled():
        return None
    d = home() / "auto" / _SAFE_ID.sub("_", session_id or "default")[:80]
    if not d.is_dir():
        return None
    pending = []
    for seen in sorted(d.glob("*.seen")):
        host = seen.read_text(encoding="utf-8").strip()
        if host and not _has_recipe(host) and _claim(d / f"{seen.stem}.asked"):
            pending.append(host)
    if not pending:
        return None
    return (
        f"Replay-Anything auto-save is on: this session drove {', '.join(pending)} with a browser tool and no "
        "recipe covers it yet. If that browser work did a task the user may repeat and it succeeded, save it now "
        "as a recipe with the replay-anything skill (section 'Auto-save'): steps from what you just did, stable "
        "selectors (no e15/@e1 refs), the user's values as inputs, then `replay-anything validate`. Test-run it "
        "only if it is read-only; if it changes anything on the site, add `tested: false` and do not run it. "
        "Then tell the user in one line the recipe id and that `replay-anything auto off` disables auto-save. "
        "If the browser work was exploration, failed, or is not a repeatable task, save nothing and say nothing."
    )


_TESTED = re.compile(r"^(\s*tested:\s*)(?:false|False|FALSE|no|No|NO|off|Off|OFF)(\s*(?:#.*)?)$", re.M)


def mark_tested(path: Path | None) -> bool:
    """After the first successful run, an untested recipe counts as tested. True if the file changed."""
    if not path:
        return False
    try:
        text = path.read_text(encoding="utf-8")
        new, n = _TESTED.subn(r"\g<1>true\g<2>", text, count=1)
        if not n:
            return False
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
