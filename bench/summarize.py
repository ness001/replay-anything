"""Print the README benchmark tables from bench/results.csv: median, range, and how the recipe compares with each
other way of repeating the task (the 1st run is needed once by every approach, so it is shown for reference only).

results.csv has one line per run. Agent runs were measured from Claude Code subagent transcripts with
parse_transcript.py; the "replay-anything CLI (no agent)" runs were timed in a terminal and use 0 tokens.

Usage: python bench/summarize.py [bench/results.csv]
"""

import csv
import statistics
import sys
from pathlib import Path

TOOLS = ["playwright-cli", "agent-browser"]
RECIPE = "recipe (run_recipe)"
NO_AGENT = "replay-anything CLI (no agent)"


def fmt_change(value, base):
    """How much less the recipe (base) takes than this row, e.g. 35 s vs 22 s -> '-37%'."""
    return f"{(base - value) / value * 100:+.0f}%" if value else "n/a"


def fmt(xs, unit):
    """Median with the min-max range, e.g. '57 s (55-59)'; a dash when there were no successful runs."""
    if not xs:
        return "-"
    lo, hi = min(xs), max(xs)
    rng = f" ({lo:.0f}-{hi:.0f})" if round(lo) != round(hi) else ""
    return f"{statistics.median(xs):.0f}{unit}{rng}"


def load(path):
    """Rows in file order: name -> {n, ok, secs, toks (thousands)}. Medians use successful runs only."""
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = rows.setdefault(r["row"], {"n": 0, "ok": 0, "secs": [], "toks": []})
            row["n"] += 1
            if r["status"] == "ok":
                row["ok"] += 1
                row["secs"].append(float(r["seconds"]))
                row["toks"].append(int(r["tokens"]) / 1000)
    return rows


def main(path):
    rows = load(path)
    # one table per tool; the shared no-agent row goes in both
    for tool in TOOLS:
        base = rows[f"{tool} {RECIPE}"]
        bs, bt = statistics.median(base["secs"]), statistics.median(base["toks"])
        print(f"\n{tool}\n")
        print("| Run | Success | Time, median (range) | Recipe vs this | Tokens, median (range) | Recipe vs this |")
        print("|---|---|---|---|---|---|")
        for name, r in rows.items():
            if not (name.startswith(tool + " ") or name == NO_AGENT):
                continue
            if name in (f"{tool} {RECIPE}", f"{tool} 1st run", NO_AGENT) or not r["secs"]:
                ds = dt = "-"
            else:
                ds = fmt_change(statistics.median(r["secs"]), bs)
                dt = fmt_change(statistics.median(r["toks"]), bt)
            print(f"| {name} | {r['ok']}/{r['n']} | {fmt(r['secs'], ' s')} | {ds} | {fmt(r['toks'], 'k')} | {dt} |")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("results.csv"))
