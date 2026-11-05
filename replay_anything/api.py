"""The two catalog operations, shared by the MCP server and the CLI. Both return plain JSON-able dicts."""

from __future__ import annotations

from . import recipe as recipes
from . import runtime
from .errors import ReplayError, StepFailed


def list_recipes(site: str | None = None, query: str | None = None) -> dict:
    found = recipes.search(site, query)
    _, errors = recipes.catalog()
    result: dict = {
        "recipes": [
            {
                "recipe": r.id,
                "site": r.site,
                "name": r.name,
                "description": r.description.splitlines()[0],
                "inputs": r.input_summary(),
                **({} if r.tested else {"tested": False}),
            }
            for r in found
        ]
    }
    if errors:
        result["invalid_recipe_files"] = errors
    return result


def run_recipe(recipe: str, inputs: dict | None = None, allow_path: bool = False, **options) -> dict:
    r = None
    try:
        r = recipes.find(recipe, allow_path=allow_path)
        result = runtime.run(r, inputs or {}, **options).to_dict()
        if not r.tested and not result.get("dry_run"):
            from .auto import mark_tested

            if mark_tested(r.path):
                result["note"] = "first successful run of an auto-saved recipe; it is now marked tested"
        return result
    except StepFailed as e:
        result = {"ok": False, **e.to_dict()}
        if r is not None and r.path and e.site_may_have_changed:
            result["fix"] = _fix_hint(r, e)
        return result
    except ReplayError as e:
        return {"ok": False, **e.to_dict()}


def _fix_hint(r, e: StepFailed) -> dict:
    """A step that used to work usually fails because the site changed; say how to repair the recipe."""
    hint = {
        "how": 'The site may have changed. Follow "Fix a recipe" in the replay-anything skill: redo only the '
        "failing step in a browser, patch that step in the recipe file, then validate, dry run and run it.",
        "recipe_file": str(r.path),
    }
    if e.saved:
        hint["failure_folder"] = e.saved
    return hint
