# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-05

First public release.

### Added
- Recipes: named website operations with typed inputs, replayed as HTTP requests, websockets, or browser
  steps, with an optional browser fallback.
- `replay-anything` CLI: `login`, `link`, `export-state`, `record`, `analyze`, `run`, `list`, `validate`, `where`, `auto`.
- MCP server with `list_recipes` and `run_recipe`, including `dry_run` to stop before anything changes.
- Skill that guides an agent through building and verifying a recipe.
- Plugins for Claude Code, Codex and GitHub Copilot CLI, with hooks that point agents to saved recipes
  before they drive playwright-cli or agent-browser, and auto-save new recipes.
- Use a login saved by playwright-cli or agent-browser (`link`), or export one to them (`export-state`).
- Browser actions include `wait_for` with `gone: true`, to wait until a loading screen disappears.
- Fixing a recipe after a site changes: `step_failed` results include a `fix` hint (recipe file and failure
  folder), and the skill has a "Fix a recipe" section for patching only the broken step.
- Benchmark against playwright-cli and agent-browser (`bench/`).
