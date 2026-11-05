"""MCP server: always catalog mode, exactly two tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import api

server = MCPServer(
    name="replay-anything",
    instructions=(
        "Runs saved website recipes. Before driving a browser (playwright-cli, agent-browser, Playwright "
        "or any browser automation), call list_recipes for that site: if a recipe fits, run_recipe does the "
        "whole operation in one step. Use the browser if none fits or run_recipe returns step_failed. "
        "If run_recipe reports session_expired, ask the user to run the login command it names."
    ),
)


@server.tool()
def list_recipes(site: str | None = None, query: str | None = None) -> dict[str, Any]:
    """List saved website recipes: site, name, one-line description and input types ('?' = optional).

    Call this before using playwright-cli, agent-browser or other browser automation on a site:
    a matching recipe replaces many browser steps with one run_recipe call.

    Args:
        site: Only recipes whose site URL contains this text.
        query: Words that must all appear in the recipe name, description or site.
    """
    return api.list_recipes(site, query)


@server.tool()
def run_recipe(recipe: str, inputs: dict[str, Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Run a saved recipe and return its output JSON.

    On failure returns ok=false with error = bad_inputs (plus expected_inputs), session_expired
    (the user must log in again), step_failed (names the failed step), or recipe_not_found.

    Args:
        recipe: Recipe id from list_recipes (site-host/name), or a bare name when unique.
        inputs: Input values by name. File inputs take a local file path.
        dry_run: Run everything up to the first action that changes the site (submit, post, send), then
            stop and report it. Use it to check a recipe on real data without changing anything.
    """
    return api.run_recipe(recipe, inputs, dry_run=dry_run)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
