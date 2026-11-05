"""Saved logins. A session is a Playwright storage-state file (cookies + localStorage) per site,
kept in the per-user folder and never inside a recipe."""

from __future__ import annotations

import json
import os
import time
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import SessionExpired
from .paths import host_of, profile_dir, session_file


def _link_file(site: str) -> Path:
    return session_file(site).parent / "link.json"


def linked(site: str) -> dict | None:
    """A link to a state file or profile owned by another tool (playwright-cli, agent-browser)."""
    p = _link_file(site)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SessionExpired(site, f"link file {p} is unreadable; run: replay-anything link {site} --remove") from e
    return data if isinstance(data, dict) else None


def _check_state(data: Any, where: str) -> dict:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("cookies", []), list)
        or not isinstance(data.get("origins", []), list)
    ):
        raise ValueError(f"{where} is not a Playwright storage state (cookies/origins)")
    return data


def _for_site(state: dict, site: str) -> dict:
    """Keep only cookies and origins for this site's host, so other sites' logins are never used."""
    host = host_of(site).split(":")[0]

    is_ip = host.replace(".", "").isdigit() or ":" in host

    def match(domain: str) -> bool:
        d = domain.lstrip(".").lower()
        # no parent-domain match for IPs, nor for bare suffixes like "com"
        return host == d or (not is_ip and "." in d and host.endswith("." + d))

    return {
        "cookies": [c for c in state.get("cookies", []) if match(c.get("domain") or host)],
        "origins": [o for o in state.get("origins", []) if match(urlparse(o.get("origin", "")).hostname or "")],
    }


def load(site: str) -> dict | None:
    link = linked(site)
    if link and link.get("profile"):
        return None  # the linked profile holds the login; never fall back to an older state.json
    if link and link.get("state"):
        p = Path(link["state"])
        fix = f"refresh it with playwright-cli/agent-browser, or run: replay-anything link {site} --remove"
        if not p.is_file():
            raise SessionExpired(site, f"linked state file {p} is missing; {fix}")
        try:
            state = _for_site(_check_state(json.loads(p.read_text(encoding="utf-8")), str(p)), site)
        except ValueError as e:
            raise SessionExpired(site, f"linked state file {p} is unreadable; {fix}") from e
        if not state["cookies"] and not state["origins"]:
            raise SessionExpired(site, f"linked state file {p} has no login for this site; {fix}")
        return state
    p = session_file(site)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def exists(site: str) -> bool:
    return load(site) is not None


def profile(site: str) -> str | None:
    """The site's persistent browser profile: a linked one, or the one login created."""
    link = linked(site)
    if link:
        return link.get("profile")  # a linked state file means: no profile
    p = profile_dir(site)
    return str(p) if p.is_dir() else None


def path(site: str) -> Path:
    return session_file(site)


def link(site: str, state: str | None = None, profile: str | None = None) -> dict:
    """Use another tool's saved login for `site`. Only the path is stored; the file is read on each run."""
    if bool(state) == bool(profile):
        raise ValueError("give exactly one of a state file or a profile folder")
    if state:
        p = Path(state).expanduser().resolve()
        found = _for_site(_check_state(json.loads(p.read_text(encoding="utf-8")), str(p)), site)
        data = {"state": str(p), "cookies_for_site": len(found["cookies"])}
    else:
        p = Path(profile).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"profile folder not found: {p}")
        data = {"profile": str(p)}
    target = _link_file(site)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in data.items() if k != "cookies_for_site"}), encoding="utf-8")
    os.replace(tmp, target)
    return data


def unlink(site: str) -> bool:
    p = _link_file(site)
    if p.is_file():
        p.unlink()
        return True
    return False


def export(site: str, out: str) -> Path:
    """Write the site's session as a storage-state file for `playwright-cli state-load` / `agent-browser --state`."""
    state = load(site)
    if state is None:
        raise ValueError(f"no saved session for {site}")
    p = Path(out)
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)  # never readable by others, even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return p


def cookie_jar(state: dict | None, site: str) -> httpx.Cookies:
    """Build an httpx cookie jar from a storage state, dropping cookies that have expired."""
    jar = httpx.Cookies()
    now = time.time()
    for c in (state or {}).get("cookies", []):
        expires = c.get("expires", -1)
        if expires not in (-1, None) and expires < now:
            continue
        domain = c.get("domain") or host_of(site).split(":")[0]
        jar.jar.set_cookie(
            Cookie(
                version=0,
                name=c["name"],
                value=c["value"],
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=c.get("path", "/"),
                path_specified=True,
                secure=bool(c.get("secure")),
                expires=None if expires in (-1, None) else int(expires),
                discard=False,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": ""} if c.get("httpOnly") else {},
            )
        )
    return jar


def has_live_cookies(state: dict | None) -> bool:
    now = time.time()
    return any(c.get("expires", -1) in (-1, None) or c["expires"] > now for c in (state or {}).get("cookies", []))


def login(site: str, check_url: str | None = None, timeout_s: int = 600) -> Path:
    """Open a real browser window, let the user sign in (SSO/2FA), then save the session.

    Saves when the user closes the window, or as soon as `check_url` (relative to the site) returns
    200 inside the browser, whichever comes first.
    """
    from playwright.sync_api import sync_playwright

    from .browser import launch

    target = session_file(site)
    with sync_playwright() as pw:
        browser, ctx = launch(pw, headless=False, profile=str(profile_dir(site)))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(site)
        print(f"Sign in to {site} in the browser window. Close the tab when you are done.", flush=True)
        deadline = time.time() + timeout_s
        state, confirmed = None, False
        while time.time() < deadline:
            try:
                state = ctx.storage_state()  # keep the latest state in case the whole browser is closed
            except Exception:
                break
            if not ctx.pages:
                break
            if check_url:
                try:
                    if ctx.request.get(site + check_url, fail_on_status_code=False).status == 200:
                        confirmed = True
                        state = ctx.storage_state()
                        break
                except Exception:
                    pass
            time.sleep(2)
        try:
            browser.close()
        except Exception:
            pass
    if state is None:
        raise RuntimeError("no session captured; the previous session (if any) was kept")
    if check_url and not confirmed:
        raise RuntimeError(
            f"{check_url} never returned 200, so sign-in was not confirmed; the previous session (if any) was kept"
        )
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    unlink(site)  # a fresh login replaces any linked login from another tool
    return target
