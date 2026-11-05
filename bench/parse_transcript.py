"""Sum token usage and wall time from a Claude Code (sub)agent JSONL transcript.

Usage: python bench/parse_transcript.py <transcript.jsonl> [...]
"""

import json
import sys
from datetime import datetime


def is_prompt(e):
    """A message from the lead agent (the task or a follow-up), not a tool result or system reminder."""
    c = (e.get("message") or {}).get("content")
    if e.get("type") != "user":
        return False
    texts = [c] if isinstance(c, str) else [x.get("text", "") for x in c or [] if isinstance(x, dict)]
    return any(t.lstrip().startswith('<teammate-message teammate_id="team-lead"') for t in texts)


def parse(path, segment=None):
    """segment=N measures only from the Nth prompt (1-based) up to the next one, e.g. a follow-up post."""
    usage, times, prompts = {}, [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if is_prompt(e):
                prompts += 1
            if segment and prompts != segment:
                continue
            if ts := e.get("timestamp"):
                times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            msg = e.get("message") or {}
            u = msg.get("usage")
            # one API response can be split over several lines; keep its last (final) usage
            if u:
                usage[msg.get("id") or len(usage)] = u
    if segment and (prompts < segment or not usage):
        raise ValueError(f"{path}: no prompt #{segment} with API calls (found {prompts} prompts)")
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for u in usage.values():
        tokens["input"] += u.get("input_tokens", 0)
        tokens["output"] += u.get("output_tokens", 0)
        tokens["cache_read"] += u.get("cache_read_input_tokens", 0)
        tokens["cache_write"] += u.get("cache_creation_input_tokens", 0)
    tokens["total"] = sum(tokens.values())
    tokens["seconds"] = round((max(times) - min(times)).total_seconds()) if times else 0
    tokens["api_calls"] = len(usage)
    return tokens


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, json.dumps(parse(p)))
