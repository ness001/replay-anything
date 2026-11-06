# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-11-06

### Added
- Recipe options for sites with bot protection: `transport: browser` sends request steps with fetch() from
  a real page on the site, `headed: true` opens a visible window, `pacing: human` waits 0.5-2 s between
  steps and actions. No stealth features: automation is not hidden.
- Browser steps accept `for_each`: run the same actions once per list item in the run's browser page,
  with `{{item}}` set, returning one result per item. Reading 8 IcM incident pages took about 26 s
  instead of about 70 s with one run per page (2026-11-06).

### Changed
- Document how to install the standalone CLI and reload the Claude Code plugin after an update.
- Initialize the plugin environment in the Stop hook test so CI passes on a clean checkout.
- All browser steps in a run share one browser page instead of launching a browser per step, so a step
  starts where the previous one ended (start it with `goto` for a fresh page). `via` is `browser` whenever
  the run opened a browser.

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
