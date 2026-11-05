# Contributing

Thanks for helping. Bug reports, recipes for new kinds of sites, and fixes are all welcome.

## Set up

Python 3.11 or later.

```bash
git clone https://github.com/ness001/replay-anything
cd replay-anything
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # includes the pinned ruff version
python -m playwright install chromium
```

## Run the checks

```bash
ruff check .
ruff format --check .
pytest -q
```

The tests start a small local chat site (`tests/chat_site.py`) on port 18765, so nothing real is ever
posted. Tests marked `browser` drive a real Chromium; skip them with `pytest -m "not browser"`.
CI runs the tests on Linux, macOS and Windows, and the lint checks on Linux.

## Layout

| Path | What it is |
|---|---|
| `replay_anything/` | the package: recipe format, runtime, browser driver, CLI, MCP server |
| `skills/replay-anything/SKILL.md` | the skill agents follow to build a recipe |
| `examples/` | sample recipes (`local-chat` runs against the test site) |
| `tests/` | tests and the local test site |
| `bench/` | benchmark method, raw results and the script that prints the README tables |
| `.claude-plugin/`, `.mcp.json`, `hooks/hooks.json` | Claude Code plugin |
| `.codex-plugin/` | Codex plugin (manifest, MCP server and hooks) |
| `hooks.json` | GitHub Copilot CLI hooks (Copilot reads it from the repo root) |
| `launch.py`, `install.py` | plugin launcher, and installer for agents without plugin support |

## Pull requests

- Keep each PR to one change, with a test that fails without it.
- If you change behaviour an agent relies on, update `skills/replay-anything/SKILL.md` too.
- If you release, raise `version` in `pyproject.toml`, `replay_anything/__init__.py`,
  `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` together (a test checks they agree), and
  add an entry to `CHANGELOG.md`.
- Never put cookies, tokens, passwords or a real site's private data in a recipe, test or issue.

By contributing you agree that your contribution is licensed under GPL-3.0, like the rest of the project.
