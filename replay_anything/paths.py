"""Per-user folders. Recipes and sessions live outside any project so secrets never land in a repo.

REPLAY_ANYTHING_HOME overrides the base folder (tests use it).
REPLAY_ANYTHING_RECIPES adds extra recipe folders (os.pathsep separated).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from platformdirs import user_data_dir


def home() -> Path:
    base = os.environ.get("REPLAY_ANYTHING_HOME") or user_data_dir("replay-anything", appauthor=False)
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def recipes_dir() -> Path:
    p = home() / "recipes"
    p.mkdir(exist_ok=True)
    return p


def recipes_dirs() -> list[Path]:
    extra = [Path(x) for x in os.environ.get("REPLAY_ANYTHING_RECIPES", "").split(os.pathsep) if x]
    return [recipes_dir(), *extra]


def host_of(site: str) -> str:
    """host[:port] only: no credentials, no path characters."""
    u = urlparse(site if "://" in site else f"https://{site}")
    host = re.sub(r"[^A-Za-z0-9.\-]", "_", u.hostname or "")
    if not host.strip("."):
        raise ValueError(f"no host in '{site}'")
    return f"{host}:{u.port}" if u.port else host


def session_file(site: str) -> Path:
    p = home() / "sessions" / host_of(site).replace(":", "_")
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def profile_dir(site: str) -> Path:
    """Persistent browser profile for a site (used by login, record and browser steps)."""
    return session_file(site).parent / "profile"
