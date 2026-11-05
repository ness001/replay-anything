"""Command line: same runtime as the MCP server, for scripts and scheduled tasks."""

from __future__ import annotations

import argparse
import json
import re
import sys

from . import api, record, sessions
from . import recipe as recipes
from .errors import RecipeInvalid, ReplayError
from .paths import home, recipes_dir


def _inputs(pairs: list[str], json_text: str | None) -> dict:
    data = json.loads(json_text) if json_text else {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--input expects name=value, got '{pair}'")
        data[key] = value
    return data


def _failed(resp) -> bool:
    """Whether a shell tool result reports failure. Field names differ between Claude Code, Codex and Copilot."""
    if not isinstance(resp, dict):
        return False
    if resp.get("interrupted") or resp.get("is_error") or resp.get("isError"):
        return True
    if str(resp.get("result_type", resp.get("resultType", ""))).lower() in ("failure", "error", "denied"):
        return True
    m = re.search(r"<exited with exit code (\d+)>\s*$", str(resp.get("text_result_for_llm", "")))  # Copilot
    if m and m.group(1) != "0":
        return True
    return any(resp.get(k) not in (None, 0) for k in ("exit_code", "exitCode"))


def _hook(target: str = "claude") -> int:
    """Agent hook (PreToolUse, PostToolUse, Stop) for Claude Code, Codex and Copilot; event JSON on stdin.
    Prints JSON or nothing.

    PreToolUse notes matching recipes; PostToolUse remembers sites without one; Stop asks the agent to
    auto-save a recipe for them. Never blocks a tool call, and a failure here is silent."""
    try:
        from . import auto
        from .hint import hint

        event = json.loads(sys.stdin.read() or "{}")
        name = event.get("hook_event_name", "PreToolUse")
        command = str((event.get("tool_input") or {}).get("command", ""))
        out = None
        if name == "PreToolUse" and event.get("tool_name") == "Bash":
            text = hint(command)
            if text:
                out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}
        elif name == "PostToolUse" and event.get("tool_name") == "Bash":
            resp = event.get("tool_response", event.get("tool_result"))  # Copilot calls it tool_result
            auto.note_command(str(event.get("session_id", "")), command, failed=_failed(resp))
            if target == "copilot":  # Copilot can't add context before a tool runs: the note comes with the result
                text = hint(command)
                if text:
                    out = {"additionalContext": text}
        elif name == "Stop":
            text = auto.stop_message(str(event.get("session_id", "")), bool(event.get("stop_hook_active")))
            if text:  # documented way to keep the agent going; asked once per site, so it can't loop
                out = {"decision": "block", "reason": text}
    except Exception:  # a broken hook must never get in the way of the agent
        return 0
    if out:
        print(json.dumps(out))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # emoji in outputs must not crash on a cp1252 pipe
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="replay-anything", description="Run and build website recipes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list recipes")
    s.add_argument("--site")
    s.add_argument("--query")

    s = sub.add_parser("run", help="run a recipe; prints output JSON, exit code 1 on failure")
    s.add_argument("recipe", help="recipe id, bare name, or path to a .yaml file")
    s.add_argument("-i", "--input", action="append", default=[], metavar="NAME=VALUE")
    s.add_argument("--json", help="inputs as a JSON object")
    s.add_argument("--browser", action="store_true", help="run only the browser fallback")
    s.add_argument("--no-fallback", action="store_true", help="never use the browser fallback")
    s.add_argument(
        "--dry-run", action="store_true", help="run everything up to the first action that changes the site, then stop"
    )

    s = sub.add_parser("validate", help="check recipe files")
    s.add_argument("files", nargs="+")

    s = sub.add_parser("login", help="sign in once in a real browser and save the session")
    s.add_argument("site")
    s.add_argument("--check", help="path that returns 200 only when signed in; saves as soon as it does")

    s = sub.add_parser("link", help="use a login saved by playwright-cli or agent-browser (stores only the path)")
    s.add_argument("site")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--state", help="storage-state file (playwright-cli state-save / agent-browser state save)")
    g.add_argument("--profile", help="persistent profile folder (--profile of either tool)")
    g.add_argument("--remove", action="store_true", help="stop using the linked login")

    s = sub.add_parser(
        "export-state", help="write a site's session for playwright-cli state-load / agent-browser --state"
    )
    s.add_argument("site")
    s.add_argument("out")

    s = sub.add_parser("record", help="sign in / do the operation once while recording traffic")
    s.add_argument("site")
    s.add_argument("--out", required=True, help="folder for the HAR and the request summary")

    s = sub.add_parser("analyze", help="summarize a recorded HAR: the requests worth replaying")
    s.add_argument("har")
    s.add_argument("--site")

    sub.add_parser("where", help="show the recipe and session folders")
    s = sub.add_parser("hook", help="agent hook: recipe notes before browser commands, auto-save at the end")
    s.add_argument("--target", choices=["claude", "codex", "copilot"], default="claude")
    s = sub.add_parser("auto", help="auto-save recipes after browser work: on, off or status (default on)")
    s.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")

    a = p.parse_args(argv)
    if a.cmd == "hook":
        return _hook(a.target)
    if a.cmd == "auto":
        from . import auto

        if a.mode != "status":
            auto.set_enabled(a.mode == "on")
        print(json.dumps(auto.status(), indent=2))
        return 0
    if a.cmd == "list":
        print(json.dumps(api.list_recipes(a.site, a.query), indent=2, ensure_ascii=False))
    elif a.cmd == "run":
        result = api.run_recipe(
            a.recipe,
            _inputs(a.input, a.json),
            allow_path=True,
            browser_only=a.browser,
            no_fallback=a.no_fallback,
            dry_run=a.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    elif a.cmd == "validate":
        bad = 0
        for f in a.files:
            try:
                r = recipes.load_file(f)
                print(f"ok    {f}  ({r.id})")
                for w in recipes.warnings(r):
                    print(f"warn  {w}")
            except RecipeInvalid as e:
                bad += 1
                print(f"error {e}")
        return 1 if bad else 0
    elif a.cmd == "login":
        print(f"saved session: {sessions.login(a.site, a.check)}")
    elif a.cmd == "link":
        try:
            if a.remove:
                print("link removed" if sessions.unlink(a.site) else "no link")
            else:
                data = sessions.link(a.site, a.state, a.profile)
                print(json.dumps({"linked": data}, indent=2))
                if data.get("cookies_for_site") == 0:
                    print(
                        f"warning: the file has no cookies for {a.site} yet; sign in with that tool first",
                        file=sys.stderr,
                    )
        except (OSError, ValueError, ReplayError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    elif a.cmd == "export-state":
        try:
            print(f"wrote {sessions.export(a.site, a.out)}")
        except (OSError, ValueError, ReplayError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    elif a.cmd == "record":
        out = record.record(a.site, a.out)
        print(json.dumps(out, indent=2))
    elif a.cmd == "analyze":
        print(json.dumps(record.analyze(a.har, a.site), indent=2, ensure_ascii=False))
    elif a.cmd == "where":
        print(json.dumps({"recipes": str(recipes_dir()), "sessions": str(home() / "sessions")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
