"""Runs a recipe: request replay first, the recipe's browser fallback only if replay fails."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from jsonpath_ng.ext import parse as jsonpath

from . import sessions
from .errors import BadInputs, ReplayError, SessionExpired, StepFailed
from .recipe import Recipe
from .template import TemplateError, condition, render, render_url

LOGIN_HINT = re.compile(r"/(login|signin|sign-in|auth|oauth2?|sso)\b", re.I)
_PY_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
    "file": str,
}


@dataclass
class Result:
    output: Any
    via: str  # "request" or "browser"
    steps: list[str] = field(default_factory=list)  # ids that ran
    fallback_reason: str | None = None
    stopped_before: str | None = None  # dry run: the action that would have changed the site

    def to_dict(self) -> dict:
        if self.stopped_before is not None:
            return {
                "ok": True,
                "dry_run": True,
                "stopped_before": self.stopped_before,
                "note": "everything before this ran; nothing was submitted",
            }
        d = {"ok": True, "via": self.via, "output": self.output}
        if self.fallback_reason:
            d["fallback_reason"] = self.fallback_reason
        return d


def check_inputs(recipe: Recipe, given: dict | None) -> dict:
    given = dict(given or {})
    problems = []
    for name in given:
        if name not in recipe.inputs:
            problems.append(f"unknown input '{name}'")
    clean: dict[str, Any] = {}
    for name, spec in recipe.inputs.items():
        value = given.get(name, spec.get("default"))
        if value is None or value == "":
            if not spec.get("optional") and "default" not in spec:
                problems.append(f"missing required input '{name}'")
            clean[name] = None
            continue
        t = spec["type"]
        if t in ("number", "integer") and isinstance(value, str):
            try:
                value = int(value) if t == "integer" else float(value)
            except ValueError:
                pass
        if t == "boolean" and isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, _PY_TYPES[t]) or (t != "boolean" and isinstance(value, bool)):
            problems.append(f"input '{name}' should be {t}, got {type(value).__name__}")
        elif t == "file":
            value = os.path.abspath(os.path.expanduser(value))
            if not os.path.isfile(value):
                problems.append(f"input '{name}': file not found: {value}")
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"input '{name}' must be one of {spec['enum']}")
        clean[name] = value
    if problems:
        raise BadInputs(problems, recipe.expected_inputs())
    return clean


def run(
    recipe: Recipe,
    inputs: dict | None = None,
    *,
    browser_only: bool = False,
    no_fallback: bool = False,
    timeout: float = 30.0,
    dry_run: bool = False,
) -> Result:
    """With dry_run, stop before the first action that changes the site: a submitting click, or a
    request/websocket step that isn't a GET. Everything before it runs for real."""
    from .browser import DryRunStop
    from .recipe import has_stop_point

    if dry_run and not has_stop_point(recipe):
        raise StepFailed(
            "dry_run",
            "this recipe marks no action that changes the site (a click with "
            "wait_response or submits: true, Enter, or a non-GET request), so a dry run could "
            "change the site for real; mark the submitting action first",
            site_may_have_changed=False,
        )
    try:
        return _run(recipe, inputs, browser_only, no_fallback, timeout, dry_run)
    except DryRunStop as e:
        return Result(output=None, via="dry_run", stopped_before=e.where)


def _run(
    recipe: Recipe, inputs: dict | None, browser_only: bool, no_fallback: bool, timeout: float, dry_run: bool
) -> Result:
    clean = check_inputs(recipe, inputs)
    state = None
    if recipe.auth == "session":
        state = sessions.load(recipe.site)
        if state is None and sessions.linked(recipe.site) and sessions.profile(recipe.site):
            state = {"cookies": [], "origins": []}  # a linked profile holds the login; browser steps use it
        if state is None:
            raise SessionExpired(recipe.site, "no saved session")
        if state.get("cookies") and not sessions.has_live_cookies(state):
            raise SessionExpired(recipe.site, "all saved cookies have expired")

    if browser_only:
        if not recipe.fallback:
            raise StepFailed("fallback", "this recipe has no browser fallback", site_may_have_changed=False)
        try:
            return _run_fallback(recipe, clean, state, reason=None, dry_run=dry_run)
        except ReplayError:
            raise
        except Exception as fb:
            raise StepFailed("fallback", _short(fb), saved=getattr(fb, "saved", None)) from fb

    try:
        return _run_steps(recipe, clean, state, timeout, dry_run)
    except StepFailed as e:
        if no_fallback or not recipe.fallback:
            raise
        try:
            return _run_fallback(recipe, clean, state, reason=str(e), dry_run=dry_run)
        except ReplayError:
            raise
        except Exception as fb:
            raise StepFailed(
                e.step, e.detail, fallback_error=_short(fb), saved=e.saved or getattr(fb, "saved", None)
            ) from fb


def _short(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:300]


def _run_steps(recipe: Recipe, inputs: dict, state: dict | None, timeout: float, dry_run: bool = False) -> Result:
    from .browser import DryRunStop

    scope: dict[str, Any] = {"inputs": inputs}
    ran: list[str] = []
    file_inputs = {v for k, v in inputs.items() if recipe.inputs[k]["type"] == "file" and v}
    with httpx.Client(
        base_url=recipe.site,
        cookies=sessions.cookie_jar(state, recipe.site),
        follow_redirects=True,
        timeout=timeout,
        event_hooks={"request": [_stay_on_site(recipe)]},
    ) as client:
        for step in recipe.steps:
            sid = step["id"]
            try:
                if "when" in step and not condition(step["when"], scope):
                    scope[sid] = None
                    continue
            except TemplateError as e:
                raise StepFailed(sid, f"bad 'when': {e}") from e
            if dry_run and _changes_site(step):
                raise DryRunStop(
                    f"step {sid} ({str(step.get('request') or 'websocket ' + str(step.get('websocket')))[:80]})"
                )
            try:
                if "request" in step:
                    scope[sid] = _request(step, scope, client, recipe, file_inputs, state is not None)
                elif "websocket" in step:
                    scope[sid] = _websocket(step, scope, client, recipe, timeout)
                else:
                    scope[sid] = _browser_step(step, scope, recipe, state, dry_run)
            except (ReplayError, DryRunStop):
                raise
            except TemplateError as e:
                raise StepFailed(sid, str(e)) from e
            except OffSiteRedirect as e:
                raise StepFailed(sid, str(e)) from e
            except httpx.HTTPError as e:
                raise StepFailed(sid, f"network error: {_short(e) or type(e).__name__}") from e
            except Exception as e:
                from .browser import ActionFailed

                if isinstance(e, ActionFailed):  # already names the action and the saved screenshot
                    raise StepFailed(sid, str(e), saved=e.saved) from e
                raise StepFailed(sid, f"{type(e).__name__}: {_short(e)}") from e
            ran.append(sid)
        try:
            output = render(recipe.output, scope) if recipe.output is not None else {k: scope[k] for k in ran}
        except TemplateError as e:
            raise StepFailed("output", str(e)) from e
    used_browser = any("browser" in s for s in recipe.steps if s["id"] in ran)
    return Result(output=output, via="browser" if used_browser else "request", steps=ran)


def _request(
    step: dict, scope: dict, client: httpx.Client, recipe: Recipe, file_inputs: set[str], authed: bool
) -> dict:
    sid = step["id"]
    method, url = str(step["request"]).strip().split(" ", 1)
    url = _on_site(render_url(url.strip(), scope), recipe, sid)
    kwargs: dict[str, Any] = {}
    if "headers" in step:
        kwargs["headers"] = {k: str(v) for k, v in render(step["headers"], scope).items() if v is not None}
    if "query" in step:
        kwargs["params"] = {k: v for k, v in render(step["query"], scope).items() if v is not None}
    if "json" in step:
        kwargs["json"] = render(step["json"], scope)
    elif "form" in step:
        kwargs["data"] = {k: v for k, v in render(step["form"], scope).items() if v is not None}
    elif "body" in step:
        kwargs["content"] = str(render(step["body"], scope))
    opened = []
    try:
        if "multipart" in step:
            files, data = {}, {}
            for k, v in render(step["multipart"], scope).items():
                if v is None:
                    continue
                if isinstance(v, str) and v in file_inputs:
                    fh = open(v, "rb")
                    opened.append(fh)
                    files[k] = (os.path.basename(v), fh)
                else:
                    data[k] = str(v)
            kwargs["files"], kwargs["data"] = files or None, data or None
        resp = client.request(method.upper(), url, **kwargs)
    finally:
        for fh in opened:
            fh.close()

    if authed and _looks_logged_out(resp, recipe, step):
        raise SessionExpired(recipe.site, f"step '{sid}' got HTTP {resp.status_code} from {resp.url.path}")
    _expect(sid, step.get("expect"), resp.status_code, _body(resp), resp.text)
    return _extract(sid, step.get("extract"), _body(resp), resp.text, resp.headers, resp.status_code)


def _on_site(url: str, recipe: Recipe, sid: str) -> str:
    """Saved-session steps may only talk to the recipe's own host, so cookies never leave it."""
    if "://" in url:
        host = httpx.URL(url).host
        if recipe.auth == "session" and host != httpx.URL(recipe.site).host:
            raise StepFailed(sid, f"URL host '{host}' is not the recipe site; refusing to send the session there")
    return url


class OffSiteRedirect(httpx.HTTPError):
    """A same-site request was redirected to another host; it is stopped before anything is sent there."""


def _stay_on_site(recipe: Recipe):
    """httpx calls this before every request it sends, including each redirect hop."""
    site_host = httpx.URL(recipe.site).host

    def check(request: httpx.Request) -> None:
        if recipe.auth == "session" and request.url.host != site_host:
            raise OffSiteRedirect(
                f"redirected to host '{request.url.host}', which is not the recipe site; "
                "refusing to send the session there"
            )

    return check


def _looks_logged_out(resp: httpx.Response, recipe: Recipe, step: dict) -> bool:
    expected = (step.get("expect") or {}).get("status")
    expected = expected if isinstance(expected, list) else [expected]
    if resp.status_code in (401, 403) and resp.status_code not in expected:
        return True
    if resp.history:  # redirected: off-site or to a login page means the session is gone
        return resp.url.host != httpx.URL(recipe.site).host or bool(LOGIN_HINT.search(resp.url.path))
    return False


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def _expect(sid: str, expect: dict | None, status: int, body: Any, text: str) -> None:
    expect = expect or {}
    want = expect.get("status")
    if want is None:
        if status >= 400:
            raise StepFailed(sid, f"HTTP {status}{_excerpt(text)}")
    elif status not in (want if isinstance(want, list) else [want]):
        raise StepFailed(sid, f"expected HTTP {want}, got {status}{_excerpt(text)}")
    for path, value in (expect.get("json") or {}).items():
        found = [m.value for m in jsonpath(path).find(body)] if body is not None else []
        if value not in found:
            raise StepFailed(sid, f"expected {path} == {value!r}, got {found[:3]!r}")
    if "contains" in expect and str(expect["contains"]) not in text:
        raise StepFailed(sid, f"response does not contain {expect['contains']!r}")


def _extract(sid: str, spec: dict | None, body: Any, text: str, headers=None, status: int | None = None) -> dict:
    """`$.path` = JSONPath, `header:Name` = response header, `re:pattern` = first group in the text."""
    out: dict[str, Any] = {"status": status} if status is not None else {}
    for name, expr in (spec or {}).items():
        expr = str(expr)
        if expr.startswith("header:"):
            value = (headers or {}).get(expr[7:].strip())
        elif expr.startswith("re:"):
            m = re.search(expr[3:], text or "")
            value = (m.group(1) if m.groups() else m.group(0)) if m else None
        else:
            matches = [m.value for m in jsonpath(expr).find(body)] if body is not None else []
            value = matches[0] if len(matches) == 1 else (matches or None)
        if value is None:
            raise StepFailed(sid, f"could not extract '{name}' with {expr}")
        out[name] = value
    return out


def _websocket(step: dict, scope: dict, client: httpx.Client, recipe: Recipe, timeout: float) -> dict:
    """Open a WebSocket with the session cookies, send messages, wait for a matching reply.

    step keys: websocket: /path, send: msg or [msgs], receive: {match: {jsonpath: value}, timeout: s}
    """
    from websockets.sync.client import connect

    sid = step["id"]
    url = render_url(str(step["websocket"]).strip(), scope)
    if "://" not in url:
        url = re.sub(r"^http", "ws", recipe.site) + url
    _on_site(re.sub(r"^ws", "http", url), recipe, sid)
    site_host = httpx.URL(recipe.site).host
    cookie = "; ".join(
        f"{c.name}={c.value}"
        for c in client.cookies.jar
        if site_host == c.domain.lstrip(".") or site_host.endswith("." + c.domain.lstrip("."))
    )
    headers = {k: str(v) for k, v in render(step.get("headers") or {}, scope).items()}
    if cookie:
        headers["Cookie"] = cookie
    receive = step.get("receive") or {}
    match = render(receive.get("match") or {}, scope)
    wait = float(receive.get("timeout", timeout))
    with connect(url, additional_headers=headers, open_timeout=wait) as ws:
        sends = render(step.get("send"), scope)
        for msg in sends if isinstance(sends, list) else ([sends] if sends is not None else []):
            ws.send(msg if isinstance(msg, str) else json.dumps(msg))
        if not step.get("receive") and not step.get("extract"):
            return {}
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise StepFailed(sid, f"no WebSocket message matching {match} within {wait:g}s")
            try:
                raw = ws.recv(timeout=left)
            except TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                msg = None
            if (
                all(value in [m.value for m in jsonpath(p).find(msg)] for p, value in match.items())
                if msg is not None
                else not match
            ):
                return _extract(sid, step.get("extract"), msg, raw if isinstance(raw, str) else "")


def _changes_site(step: dict) -> bool:
    if "websocket" in step:
        return True
    return "request" in step and str(step["request"]).split(" ", 1)[0].upper() not in ("GET", "HEAD", "OPTIONS")


def _browser_step(step: dict, scope: dict, recipe: Recipe, state: dict | None, dry_run: bool = False) -> dict:
    return _with_page(recipe, state, step["browser"], scope, dry_run)


def _with_page(recipe: Recipe, state: dict | None, actions: list[dict], scope: dict, dry_run: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    from .browser import DryRunStop, launch, run_actions

    def r(v):
        if isinstance(v, tuple) and v[0] == "__when__":
            return condition(v[1], scope)
        return render(v, scope)

    def on_login_page(page) -> bool:
        return (
            recipe.auth == "session"
            and bool(LOGIN_HINT.search(page.url))
            and httpx.URL(page.url).path != httpx.URL(recipe.site).path
        )

    with sync_playwright() as pw:
        prof = sessions.profile(recipe.site) if recipe.auth == "session" else None
        browser, ctx = launch(pw, storage_state=None if prof else state, profile=prof)
        try:
            page = ctx.new_page()
            try:
                out = run_actions(
                    page, actions, recipe.site, r, on_navigate=lambda: on_login_page(page), dry_run=dry_run
                )
            except DryRunStop:
                raise
            except Exception as e:
                if on_login_page(page):
                    path = httpx.URL(page.url).path
                    raise SessionExpired(recipe.site, f"browser was sent to a login page ({path})") from e
                raise
            if on_login_page(page):
                raise SessionExpired(recipe.site, f"browser ended on a login page ({httpx.URL(page.url).path})")
            return out
        finally:
            browser.close()


def _run_fallback(
    recipe: Recipe, inputs: dict, state: dict | None, reason: str | None, dry_run: bool = False
) -> Result:
    fb = recipe.fallback or {}
    scope: dict[str, Any] = {"inputs": inputs}
    scope["browser"] = _with_page(recipe, state, fb["browser"], scope, dry_run)
    output = render(fb["output"], scope) if "output" in fb else scope["browser"]
    return Result(output=output, via="browser", steps=["fallback"], fallback_reason=reason)


def _excerpt(text: str) -> str:
    """Response text in errors only with REPLAY_ANYTHING_DEBUG=1: error pages can echo tokens."""
    if os.environ.get("REPLAY_ANYTHING_DEBUG") == "1" and text:
        return ": " + text[:200]
    return ""
