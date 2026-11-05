# Benchmark method

How the numbers in the README's [Benchmark](../README.md#benchmark) section were measured.
`results.csv` has one line per run; `python bench/summarize.py` prints the README tables from it.

## Task

Post one text message to your own Microsoft Teams "Note to self" chat. Teams was chosen because it is a
real single-sign-on web app with a slow, script-heavy UI, the kind of site where agents struggle.

Each run is a fresh Claude Code subagent (Opus 5) allowed to use only the tool its row names.

## Rows

Each row was run with playwright-cli and with agent-browser, 3 runs per row, one at a time (all runs post
to the same chat).

| Row | What the agent does | What it can reuse |
|---|---|---|
| 1st run | explores Teams and posts | nothing |
| same session, same request | the agent from a 1st run is asked to post again, with no instructions on how | its own conversation |
| record and replay | saves its steps while posting (playwright-cli `recording-start`/`stop`, agent-browser a `batch` list), then posts again by replaying that file | the saved file, same conversation |
| agent-written skill | a fresh agent posts using only a skill an earlier agent wrote after its own 1st run | the skill file |
| recipe (run_recipe) | a fresh agent calls `run_recipe` on a recipe an earlier agent built from its tool's session (replay-anything skill) | the recipe |

- "Same session" and "record and replay" use different agents: once an agent has recorded, its second
  post is no longer a plain repeat.
- For those two rows only the second post is measured.
- Setup posts are not measured: the recording run, and the run that wrote the skill or built the recipe.

Reference row: `replay-anything run <recipe> -i text=...` timed in a terminal, with no agent and so no tokens.

## Rules

- Every run reaches the chat by clicking it in the sidebar, never by its URL.
- Every saved route (script, `batch` list, skill, recipe) checks that the open chat is the expected one
  before typing, and stops if not. No run posted to the wrong chat.
- Each run posts at most once. A failed step is reported, not retried another way.
- replay-anything's auto-save was off, so no row other than "recipe" could get a recipe by accident.
- Each run used its own browser session (playwright-cli `-s=<name>`, agent-browser `--session <name>`) and
  its own copy of a signed-in browser profile.

## Metrics

- **Time:** wall time from the first to the last entry of the run in the subagent's transcript.
- **Tokens:** input + output + cache read + cache write, summed over every API response in the run.
- **Success:** the message appeared in the chat. Medians and ranges use successful runs only.
- **Recipe vs this:** how much less time or tokens the recipe used than that row's median.

`parse_transcript.py` computes time and tokens from a Claude Code transcript (`agent-<id>.jsonl`).
`parse(path, segment=2)` measures only the agent's second task, for the two-post rows.

## Environment

2026-11-05, one Windows 11 machine. playwright-cli 0.1.22, agent-browser 0.27.0, replay-anything 0.2.0,
Claude Code with Opus 5.
