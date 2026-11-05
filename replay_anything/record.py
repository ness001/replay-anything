"""Recording helpers for the build skill: capture traffic while the user does the operation once,
then rank the requests so the agent can pick the essential ones."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from . import sessions

STATIC = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".map", ".webp")
NOISE_HOSTS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "clarity.ms",
    "sentry",
    "datadog",
    "newrelic",
    "hotjar",
    "segment",
    "browser.events.data",
    "aria.microsoft",
    "msecnd",
)
SECRET_HEADERS = {"cookie", "authorization", "x-csrf-token", "x-xsrf-token", "x-api-key", "set-cookie"}


def record(site: str, out: str | Path, timeout_s: int = 1800) -> dict:
    """Open a real browser with the saved session, record a HAR until the tab is closed.

    The raw HAR (it holds cookies) stays in the per-user folder; `out` gets only the redacted summary.
    """
    from playwright.sync_api import sync_playwright

    from .browser import launch
    from .paths import home

    out = Path(out).resolve()
    if any((p / ".git").exists() for p in (out, *out.parents)):
        raise SystemExit(f"--out {out} is inside a git work tree; pick a folder outside any repo")
    out.mkdir(parents=True, exist_ok=True)
    raw = home() / "recordings"
    raw.mkdir(exist_ok=True)
    har = raw / f"{time.strftime('%Y%m%d-%H%M%S')}.har"
    with sync_playwright() as pw:
        browser, ctx = launch(
            pw,
            headless=False,
            profile=sessions.profile(site),
            storage_state=None if sessions.profile(site) else sessions.load(site),
            record_har_path=str(har),
            record_har_content="embed",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(site)
        print("Do the operation once in the browser window, then close the tab.", flush=True)
        deadline = time.time() + timeout_s
        while ctx.pages and time.time() < deadline:
            time.sleep(1)
        ctx.close()  # writes the HAR
        browser.close()
    summary = analyze(har, site)
    (out / "requests.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "raw_har": str(har),
        "summary": str(out / "requests.json"),
        "candidates": len(summary["requests"]),
        "note": "raw_har contains cookies; delete it when the recipe is done",
    }


SECRET_KEYS = re.compile(
    r"(token|secret|password|passwd|pwd|session|sid|code|sig|signature|auth|key|csrf|xsrf|cookie)", re.I
)


def redact(text: str) -> str:
    """Hide values of secret-looking keys in query strings, form bodies and JSON."""
    if not text:
        return text
    try:
        return json.dumps(_redact_json(json.loads(text)), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return re.sub(
        r"([\w.\-]*?)=([^&\s]*)",
        lambda m: f"{m.group(1)}=<redacted>" if SECRET_KEYS.search(m.group(1)) else m.group(0),
        text,
    )


def _redact_json(v):
    if isinstance(v, dict):
        return {
            k: "<redacted>" if SECRET_KEYS.search(k) and not isinstance(v[k], (dict, list)) else _redact_json(v[k])
            for k in v
        }
    if isinstance(v, list):
        return [_redact_json(x) for x in v]
    return v


def analyze(har_path: str | Path, site: str | None = None) -> dict:
    """Keep API-looking requests, drop static files and trackers, redact secrets, show bodies briefly."""
    har = json.loads(Path(har_path).read_text(encoding="utf-8"))
    host = urlparse(site).netloc if site and "://" in site else site
    rows = []
    for i, e in enumerate(har["log"]["entries"]):
        req, resp = e["request"], e["response"]
        u = urlparse(req["url"])
        if u.scheme not in ("http", "https", "ws", "wss"):
            continue
        if u.path.lower().endswith(STATIC) or any(n in u.netloc for n in NOISE_HOSTS):
            continue
        ctype = next((h["value"] for h in resp.get("headers", []) if h["name"].lower() == "content-type"), "")
        if req["method"] == "GET" and "html" in ctype and resp.get("status") == 200:
            kind = "page"
        else:
            kind = "api"
        score = (
            (3 if req["method"] != "GET" else 0)
            + (2 if "json" in ctype else 0)
            + (1 if host and host in u.netloc else 0)
        )
        body = (req.get("postData") or {}).get("text") or ""
        rows.append(
            {
                "index": i,
                "method": req["method"],
                "url": redact(req["url"]),
                "status": resp.get("status"),
                "kind": kind,
                "score": score,
                "request_headers": {
                    h["name"]: ("<redacted>" if h["name"].lower() in SECRET_HEADERS else h["value"])
                    for h in req.get("headers", [])
                    if not h["name"].startswith(":")
                },
                "request_body": redact(body)[:1000],
                "response_type": ctype,
                "response_body": redact((resp.get("content") or {}).get("text") or "")[:1000],
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["index"]))
    return {"site": site, "total_entries": len(har["log"]["entries"]), "requests": rows}
