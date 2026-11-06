"""Playwright helpers shared by browser steps, login and recording."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any


class _Persistent:
    """Gives a persistent context the same close() as a browser, so callers treat both alike."""

    def __init__(self, ctx):
        self.ctx = ctx

    def close(self):
        self.ctx.close()


# browser exited while opening a profile; a locked profile ("already in use") is not retried elsewhere
_CRASH = ("has been closed", "target closed", "exitcode=")
_LOCKED = ("already in use", "processsingleton", "singletonlock")


def launch(
    pw, *, headless: bool = True, storage_state: dict | None = None, profile: str | None = None, **context_args: Any
):
    """Launch Chromium (installed Edge/Chrome first, so no browser download is needed).

    With `profile`, use a persistent browser profile folder: corporate SSO (Entra, device sign-in)
    and tokens kept in IndexedDB survive between runs, which a plain storage state does not keep.
    Returns (closer, context).
    """
    headless = headless and os.environ.get("REPLAY_ANYTHING_HEADED") != "1"
    # a headless default of 1280x720 makes responsive sites hide labels (e.g. "Add" instead of "Add Responsible")
    context_args.setdefault("viewport", VIEWPORT)
    missing: list[str] = []
    crashed: list[tuple[str, Exception]] = []
    for channel in ("msedge", "chrome", None):
        name = channel or "bundled chromium"
        opts = {"headless": headless, **({"channel": channel} if channel else {})}
        try:
            if profile:
                ctx = pw.chromium.launch_persistent_context(profile, **opts, **context_args)
                if crashed:
                    print(
                        f"replay-anything: {crashed[0][0]} could not open profile {profile}; using {name} (it may update the profile, "
                        f"so {crashed[0][0]} may not open it afterwards)",
                        file=sys.stderr,
                    )
                return _Persistent(ctx), ctx
            browser = pw.chromium.launch(**opts)
            break
        except Exception as e:
            text = str(e).lower()
            if "executable doesn't exist" in text or "is not found" in text or "not installed" in text:
                missing.append(name)
            elif profile and any(s in text for s in _CRASH) and not any(s in text for s in _LOCKED):
                # a profile copied from another tool can crash one browser (Edge) but open in the next
                crashed.append((name, e))
            else:  # a real failure (locked profile, bad arguments): report it, don't hide it
                raise
    else:
        if crashed:
            detail = "; ".join(f"{n}: {str(e).splitlines()[0]}" for n, e in crashed)
            raise RuntimeError(f"no browser could open profile {profile} ({detail})") from crashed[0][1]
        raise RuntimeError(
            f"no Chromium browser available ({', '.join(missing)} not installed); run 'playwright install chromium'"
        )
    ctx = browser.new_context(storage_state=storage_state, **context_args)
    return browser, ctx


VIEWPORT = {"width": 1920, "height": 1080}


class LoggedOut(Exception):
    pass


class DryRunStop(Exception):
    """Dry run reached the action that would change the site; nothing after it ran."""

    def __init__(self, where: str):
        super().__init__(where)
        self.where = where


class ActionFailed(Exception):
    """Names the failing action and where its screenshot and page text were saved."""

    def __init__(self, index: int, verb: str, target: str, error: Exception, saved: str | None):
        first = (str(error).strip().splitlines() or [type(error).__name__])[0][:300]
        msg = f"action {index} ({verb}{' ' + target if target else ''}): {type(error).__name__}: {first}"
        super().__init__(msg + (f" [screenshot and page text: {saved}]" if saved else ""))
        self.saved = saved


def changes_site(verb: str, action: dict, arg: Any) -> bool:
    """Where a dry run stops: a click with wait_response or `submits: true`, or a press of Enter
    (sends chat messages, submits forms). `submits: false` marks one as safe, e.g. Enter in a search box.
    A goto with wait_response only reads what the page loads, so it is never a stop."""
    if action.get("submits") is not None:
        return bool(action["submits"])
    if verb == "click":
        return bool(isinstance(arg, dict) and arg.get("wait_response"))
    return verb == "press" and isinstance(arg, dict) and str(arg.get("key", "")).lower() in ("enter", "control+enter")


def _target(arg: Any) -> str:
    if isinstance(arg, dict):
        return str(arg.get("selector") or arg.get("text") or arg.get("url") or "")[:120]
    return str(arg)[:120]


KEEP_FAILURES = 20


def _save_failure(page) -> str | None:
    """Screenshot + visible text of the page at the failure, in the per-user folder.

    They can show private data (messages, tokens in the URL), so the folder is private and only the
    newest KEEP_FAILURES are kept."""
    import shutil
    import tempfile

    try:
        from .paths import home

        base = home() / "failures"
        base.mkdir(mode=0o700, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=base))  # unique, mode 0700
        for old in sorted(base.iterdir(), key=lambda x: (x.stat().st_mtime, x.name))[:-KEEP_FAILURES]:
            if old != d:
                shutil.rmtree(old, ignore_errors=True)
    except Exception:
        return None
    saved = False
    try:
        page.screenshot(path=str(d / "page.png"), full_page=False, timeout=5000)
        os.chmod(d / "page.png", 0o600)
        saved = True
    except Exception:
        pass
    try:
        fd = os.open(d / "page.txt", os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{page.url}\n\n{page.inner_text('body', timeout=5000)}")
        saved = True
    except Exception:
        pass
    if not saved:
        shutil.rmtree(d, ignore_errors=True)
        return None
    return str(d)


def _visible(page, selector: str):
    """First visible match: pages often keep hidden copies of buttons (other dialogs, collapsed layouts)."""
    return page.locator(selector).filter(visible=True).first


_PASTE_JS = """(el, [html, text]) => {
  const dt = new DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', text || el.innerText || '');
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
}"""

# `html: clean`: structure only. Keeps tags, text, href, datetime, data-tid, data-mid, colspan/rowspan;
# drops scripts, styles, svg, images (emoji images become their character), buttons and every other attribute.
_CLEAN_JS = """el => {
  const keep = new Set(['href', 'datetime', 'data-tid', 'data-mid', 'colspan', 'rowspan']);
  const c = el.cloneNode(true);
  // emoji drawn as images keep their character (alt text); other images go
  c.querySelectorAll('img[alt]').forEach(n => { if ([...n.alt].length <= 4 && /\\p{Extended_Pictographic}/u.test(n.alt)) n.replaceWith(n.alt); });
  c.querySelectorAll('script,style,svg,img,button,[aria-hidden=true]').forEach(n => n.remove());
  for (const n of [c, ...c.querySelectorAll('*')])
    for (const a of [...n.attributes]) if (!keep.has(a.name)) n.removeAttribute(a.name);
  return c.innerHTML.replace(/<(span|div)>\\s*<\\/\\1>/g, '').replace(/\\s+/g, ' ');
}"""


def _text(v: Any) -> str | None:
    """has_text must be a string: a whole-value template like {{inputs.id}} keeps its type (an int here)."""
    return None if v is None or v == "" else str(v)


def _await_response(page, wait: Any, act, label: str, timeout_ms: int, out: dict, methods: str | None) -> None:
    """Run `act`, then wait for the first finished request whose URL contains wait["url"] and
    check its status; `extract` ({name: $.jsonpath}) copies values from its JSON body into `out`.
    methods="write" ignores GETs (a click that submits); None takes any method (a page load)."""
    want = wait if isinstance(wait, dict) else {"url": wait}
    seen: dict[str, Any] = {}

    def grab(req):
        # on requestfinished the body is complete; read it now, before any navigation drops it
        if (
            "done" in seen
            or str(want["url"]) not in req.url
            or req.method == "OPTIONS"
            or (methods == "write" and req.method == "GET")
        ):
            return
        r = req.response()
        if r is None or 300 <= r.status < 400:  # a redirect: the real response follows
            return
        seen["status"] = r.status
        if want.get("extract"):
            try:
                seen["body"] = r.json()
            except Exception as e:
                seen["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:120]}"
        seen["done"] = True

    page.on("requestfinished", grab)
    try:
        act()
        deadline = time.monotonic() + timeout_ms / 1000
        while "done" not in seen and time.monotonic() < deadline:
            page.wait_for_timeout(100)
    finally:
        page.remove_listener("requestfinished", grab)
    if "done" not in seen:
        raise RuntimeError(f"{label}: no response from {want['url']} within {timeout_ms} ms")
    status = seen["status"]
    if want.get("status") and status != int(want["status"]):
        raise RuntimeError(f"{label}: expected HTTP {want['status']} from {want['url']}, got {status}")
    if status >= 400:
        raise RuntimeError(f"{label}: {want['url']} returned HTTP {status}")
    if want.get("extract"):  # {name: $.jsonpath} from the response the site sent back
        from jsonpath_ng.ext import parse as jsonpath

        if "body" not in seen:
            raise RuntimeError(f"{label}: could not read the response ({seen.get('error')})")
        for name, path in want["extract"].items():
            found = [m.value for m in jsonpath(str(path)).find(seen["body"])]
            if not found:
                raise RuntimeError(f"{label}: response has no {path}")
            out[name] = found[0]


def run_actions(
    page,
    actions: list[dict],
    base_url: str,
    render,
    default_timeout: int = 15000,
    on_navigate=None,
    dry_run: bool = False,
    pause=None,
) -> dict:
    """Run browser actions. `render` resolves placeholders. Returns values from `extract` actions.

    Any action given as a mapping may set `timeout` (ms). A click with `wait_response` or `submits: true`
    changes the site: with `dry_run` the run stops just before it (DryRunStop). A goto with
    `wait_response` reads a response the page loads itself (read-only).
    `on_navigate()` is checked after each goto; returning True stops with LoggedOut.
    `pause()`, if given, runs between actions (recipe `pacing`).
    Any other failure is raised as ActionFailed naming the action, with a screenshot saved."""
    out: dict[str, Any] = {}
    acted = False
    for index, action in enumerate(actions, 1):
        ((verb, arg),) = ((k, v) for k, v in action.items() if k not in ("when", "submits"))
        if "when" in action and not render(("__when__", action["when"])):
            continue
        if pause and acted:
            pause()
        acted = True
        arg = render(arg)
        if dry_run and changes_site(verb, action, arg):
            raise DryRunStop(f"action {index} ({verb} {_target(arg)})")
        try:
            _one(page, verb, arg, base_url, default_timeout, on_navigate, out)
        except (LoggedOut, DryRunStop):
            raise
        except Exception as e:
            raise ActionFailed(index, verb, _target(arg), e, _save_failure(page)) from e
    return out


def _one(page, verb: str, arg: Any, base_url: str, default_timeout: int, on_navigate, out: dict) -> None:
    timeout_ms = (arg.get("timeout") if isinstance(arg, dict) else None) or default_timeout
    if verb == "goto":
        target = arg["url"] if isinstance(arg, dict) else arg
        url = target if "://" in str(target) else base_url + str(target)
        wait = arg.get("wait_response") if isinstance(arg, dict) else None

        def go():
            page.goto(url, timeout=timeout_ms)
            if on_navigate and on_navigate():
                raise LoggedOut(page.url)

        if wait:  # read data the page loads for itself (e.g. an API call made with a token kept in the page)
            _await_response(page, wait, go, f"goto {target}", timeout_ms, out, methods=None)
        else:
            go()
    elif verb == "fill":
        _visible(page, arg["selector"]).fill("" if arg.get("value") is None else str(arg["value"]), timeout=timeout_ms)
    elif verb == "paste_html":
        # rich editors (Teams, Outlook, Slack) keep tables/links from a paste but not from fill
        html = str(arg.get("html") or "")
        if arg.get("file"):
            with open(arg["file"], encoding="utf-8") as fh:
                html = fh.read()
        if not html.strip():
            raise ValueError("paste_html has nothing to paste (empty html/file)")
        target = _visible(page, arg["selector"])
        target.click(timeout=timeout_ms)
        target.evaluate(_PASTE_JS, [html, str(arg.get("text") or "")])
    elif verb == "click":
        selector = arg["selector"] if isinstance(arg, dict) else arg
        wait = arg.get("wait_response") if isinstance(arg, dict) else None
        if wait:  # {url, status, extract}: don't move on (or close) until the site accepted it
            _await_response(
                page,
                wait,
                lambda: _visible(page, selector).click(timeout=timeout_ms),
                f"click on {selector}",
                timeout_ms,
                out,
                methods="write",
            )
        else:
            _visible(page, selector).click(timeout=timeout_ms)
    elif verb == "press":
        _visible(page, arg["selector"]).press(arg["key"], timeout=timeout_ms)
    elif verb == "upload":
        if arg.get("file"):  # optional file input left empty: skip
            page.set_input_files(arg["selector"], arg["file"], timeout=timeout_ms)
    elif verb == "wait_for":
        if "url" in arg:
            page.wait_for_url(arg["url"], timeout=timeout_ms)
            return
        if "text" in arg:
            loc = page.get_by_text(str(arg["text"]), exact=False)
        else:
            # has_text matches literal text, so an input with quotes can't break the selector
            loc = page.locator(arg["selector"], has_text=_text(arg.get("has_text")))
        if not arg.get("gone"):
            (loc.first if "text" in arg else loc.last).wait_for(timeout=timeout_ms)
            return
        # gone: a loading screen (Teams' "Almost there...") can come up a few seconds after the page looks
        # ready, so give it `appear` ms to show, then wait until no visible match is left
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            loc.filter(visible=True).first.wait_for(timeout=arg.get("appear", 10000))
        except PlaywrightTimeout:
            return  # never showed up: nothing to wait for
        deadline = time.monotonic() + timeout_ms / 1000
        while loc.filter(visible=True).count():
            if time.monotonic() > deadline:
                raise TimeoutError(f"{_target(arg)} still shown {timeout_ms} ms after it appeared")
            page.wait_for_timeout(250)
    elif verb == "extract":
        for name, spec in arg.items():
            if name == "timeout":
                continue
            spec = {"selector": spec} if isinstance(spec, str) else spec
            loc = page.locator(spec["selector"], has_text=_text(spec.get("has_text")))
            loc.last.wait_for(timeout=timeout_ms)

            def read(el, spec=spec):
                if spec.get("attr"):
                    return el.get_attribute(spec["attr"])
                if spec.get("html") == "clean":
                    return el.evaluate(_CLEAN_JS)
                return el.inner_html() if spec.get("html") else el.inner_text()

            if spec.get("all"):  # every match, oldest first; `last: n` keeps the newest n
                items = [read(el) for el in loc.all()]
                n = spec.get("last")
                out[name] = items if n is None else (items[-int(n) :] if int(n) > 0 else [])
            else:
                out[name] = read(loc.last)
