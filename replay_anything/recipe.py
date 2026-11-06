"""Recipe YAML: loading, validation, and the per-user recipe catalog."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .errors import RecipeInvalid, RecipeNotFound
from .paths import host_of, recipes_dirs
from .template import placeholders

INPUT_TYPES = {"string", "number", "integer", "boolean", "file", "object", "array"}
STEP_KINDS = ("request", "websocket", "browser")
BROWSER_ACTIONS = {"goto", "fill", "paste_html", "click", "press", "upload", "wait_for", "extract"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


@dataclass
class Recipe:
    name: str
    description: str
    site: str
    auth: str
    inputs: dict[str, dict]
    steps: list[dict]
    output: Any
    fallback: dict | None
    path: Path | None = None
    tested: bool = True  # false: auto-saved and not run yet (it changes something on the site)
    transport: str = "http"  # "browser": request steps run as fetch() inside a real browser page
    headed: bool = False  # browser opens a visible window instead of headless
    pacing: str = "none"  # "human": random 0.5-2 s wait between steps and browser actions
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def host(self) -> str:
        return host_of(self.site)

    @property
    def id(self) -> str:
        return f"{self.host}/{self.name}"

    def input_summary(self) -> dict[str, str]:
        return {k: spec["type"] + ("?" if spec.get("optional") else "") for k, spec in self.inputs.items()}

    def expected_inputs(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self.inputs.items()}


def _fail(where: str, msg: str) -> None:
    raise RecipeInvalid(f"{where}: {msg}")


def parse(data: Any, path: Path | None = None) -> Recipe:
    where = str(path) if path else "recipe"
    if not isinstance(data, dict):
        _fail(where, "top level must be a mapping")
    for key in ("name", "description", "site", "steps"):
        if not data.get(key):
            _fail(where, f"missing '{key}'")
    site = str(data["site"]).rstrip("/")
    parsed = urlparse(site)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or "\\" in site:
        _fail(where, f"site must be a plain http(s) URL without credentials, got '{site}'")
    auth = data.get("auth", "none")
    if auth not in ("none", "session"):
        _fail(where, f"auth must be 'none' or 'session', got '{auth}'")
    if "tested" in data and not isinstance(data["tested"], bool):
        _fail(where, "'tested' must be true or false")
    if "notes" in data and not isinstance(data["notes"], str):
        _fail(where, "'notes' must be text (how the recipe was built, known limits)")
    if data.get("transport", "http") not in ("http", "browser"):
        _fail(where, f"transport must be 'http' or 'browser', got '{data['transport']}'")
    if "headed" in data and not isinstance(data["headed"], bool):
        _fail(where, "'headed' must be true or false")
    if data.get("pacing", "none") not in ("none", "human"):
        _fail(where, f"pacing must be 'none' or 'human', got '{data['pacing']}'")

    inputs: dict[str, dict] = {}
    for name, spec in (data.get("inputs") or {}).items():
        spec = {"type": spec} if isinstance(spec, str) else dict(spec or {})
        spec.setdefault("type", "string")
        if spec["type"] not in INPUT_TYPES:
            _fail(where, f"input '{name}' has unknown type '{spec['type']}'")
        inputs[name] = spec

    steps = data["steps"]
    if not isinstance(steps, list):
        _fail(where, "'steps' must be a list")
    known = {"inputs"}
    for i, step in enumerate(steps):
        sw = f"{where}: step {i + 1}"
        if not isinstance(step, dict) or not step.get("id"):
            _fail(sw, "each step needs an 'id'")
        sid = step["id"]
        if sid in known or sid in ("session", "env"):
            _fail(sw, f"duplicate or reserved step id '{sid}'")
        kinds = [k for k in STEP_KINDS if k in step]
        if len(kinds) != 1:
            _fail(sw, f"step '{sid}' needs exactly one of {', '.join(STEP_KINDS)}")
        kind = kinds[0]
        if kind == "request":
            method = str(step["request"]).split(" ", 1)[0].upper()
            if method not in HTTP_METHODS or " " not in str(step["request"]).strip():
                _fail(sw, "request must look like 'METHOD /path'")
            bodies = [b for b in ("json", "form", "multipart", "body") if b in step]
            if len(bodies) > 1:
                _fail(sw, f"use only one body field, got {bodies}")
        elif kind == "browser":
            _check_actions(step["browser"], sw)
        _check_refs(step, known, sw)
        if "when" in step and not isinstance(step["when"], bool):
            bare = re.sub(r"^\s*not\s+", "", str(step["when"]))
            bare = re.split(r"\s*(?:==|!=)\s*", bare)[0].strip()
            _check_refs("{{" + bare.strip("{} ") + "}}", known, sw)
        known.add(sid)

    output = data.get("output")
    _check_refs(output, known, f"{where}: output")

    fallback = data.get("fallback")
    if fallback is not None:
        if not isinstance(fallback, dict) or "browser" not in fallback:
            _fail(where, "fallback must be a mapping with a 'browser' list")
        _check_actions(fallback["browser"], f"{where}: fallback")
        _check_refs(fallback, {"inputs", "browser"}, f"{where}: fallback")

    return Recipe(
        name=str(data["name"]),
        description=str(data["description"]).strip(),
        site=site,
        auth=auth,
        inputs=inputs,
        steps=steps,
        output=output,
        fallback=fallback,
        path=path,
        tested=data.get("tested", True),
        transport=data.get("transport", "http"),
        headed=data.get("headed", False),
        pacing=data.get("pacing", "none"),
        raw=data,
    )


def _check_actions(actions: Any, where: str) -> None:
    if not isinstance(actions, list) or not actions:
        _fail(where, "browser actions must be a non-empty list")
    for a in actions:
        if not isinstance(a, dict) or len(set(a) & BROWSER_ACTIONS) != 1:
            _fail(where, f"each browser action needs one of {sorted(BROWSER_ACTIONS)}, got {a}")
        if "submits" in a and (not ({"click", "press"} & set(a)) or not isinstance(a["submits"], bool)):
            _fail(where, f"'submits' (true/false) goes on a click or press, got {a}")
        w = a.get("wait_for")
        if isinstance(w, dict):
            targets = {"text", "selector", "url"} & set(w)
            extra = set(w) - {"text", "selector", "url", "has_text", "gone", "appear", "timeout"}
            if len(targets) != 1 or extra:
                _fail(
                    where,
                    f"wait_for needs one of text, selector or url (plus has_text, gone, appear, timeout), got {w}",
                )
            if "gone" in w and (not isinstance(w["gone"], bool) or "url" in w):
                _fail(where, f"'gone' (true/false) goes with text or selector, got {w}")
            if "appear" in w and not w.get("gone"):
                _fail(where, f"'appear' goes with gone: true, got {w}")
            if "has_text" in w and "selector" not in w:
                _fail(where, f"'has_text' goes with selector, got {w}")
            for k in ("appear", "timeout"):
                if k in w and (isinstance(w[k], bool) or not isinstance(w[k], int) or w[k] < 0):
                    _fail(where, f"wait_for '{k}' must be a whole number of ms, got {w}")


def _check_refs(value: Any, known: set[str], where: str) -> None:
    for ref in placeholders(value):
        head = ref.split(".", 1)[0]
        if head not in known:
            _fail(where, f"'{{{{{ref}}}}}' refers to '{head}', which is not defined before this point")


def _quoted_input(selector: str) -> bool:
    """True when an {{inputs.x}} placeholder sits inside a '...' or "..." string of the selector."""
    quote = None
    i = 0
    while i < len(selector):
        c = selector[i]
        if selector.startswith("{{", i):
            j = selector.find("}}", i)
            if j < 0:
                return False
            if quote and selector[i + 2 : j].strip().startswith("inputs."):
                return True
            i = j + 2
            continue
        if c == "\\":
            i += 2
            continue
        if quote is None and c in "'\"":
            quote = c
        elif c == quote:
            quote = None
        i += 1
    return False


def _selectors(action: dict) -> list[str]:
    out = []
    for verb, arg in action.items():
        if verb == "when":
            continue
        if verb == "extract" and isinstance(arg, dict):
            specs = [v for k, v in arg.items() if k != "timeout"]
        else:
            specs = [arg]
        for spec in specs:
            sel = spec.get("selector") if isinstance(spec, dict) else spec if verb in ("click", "extract") else None
            if isinstance(sel, str):
                out.append(sel)
    return out


def has_stop_point(recipe: Recipe) -> bool:
    """Whether a dry run has somewhere to stop before the site changes."""
    from .browser import changes_site

    for step in recipe.steps:
        if "websocket" in step or (
            "request" in step and str(step["request"]).split(" ", 1)[0].upper() not in ("GET", "HEAD", "OPTIONS")
        ):
            return True
    groups = [s["browser"] for s in recipe.steps if "browser" in s] + (
        [recipe.fallback["browser"]] if recipe.fallback else []
    )
    return any(
        changes_site(next(k for k in a if k not in ("when", "submits")), a, a.get("click") or a.get("press"))
        for g in groups
        for a in g
    )


def warnings(recipe: Recipe) -> list[str]:
    """Risky but valid patterns. Today: an input placed inside quotes in a selector, which breaks
    when the value contains a quote. Use `has_text:` (literal text) or `wait_for: {text: ...}`."""
    out = []
    actions = [a for s in recipe.steps for a in s.get("browser") or []]
    actions += (recipe.fallback or {}).get("browser") or []
    for a in actions:
        for sel in _selectors(a):
            if _quoted_input(sel):
                out.append(
                    f"selector {sel!r} puts an input inside quotes; a quote in the value "
                    "breaks it (use has_text: or wait_for: {text: ...})"
                )
    return out


def load_file(path: str | os.PathLike) -> Recipe:
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RecipeInvalid(f"{p}: not valid YAML: {e}") from e
    return parse(data, p)


def catalog() -> tuple[list[Recipe], list[str]]:
    """All recipes in the recipe folders, plus load errors (so one bad file never hides the rest)."""
    recipes, errors = [], []
    for d in recipes_dirs():
        for p in sorted(Path(d).rglob("*.y*ml")):
            try:
                recipes.append(load_file(p))
            except RecipeInvalid as e:
                errors.append(str(e))
    return recipes, errors


def find(ref: str, allow_path: bool = False) -> Recipe:
    """Find by id (`host/name`), by bare name when unique, or (CLI only) by a file path."""
    if allow_path and ref.endswith((".yaml", ".yml")) and Path(ref).is_file():
        return load_file(ref)
    recipes, _ = catalog()
    exact = [r for r in recipes if r.id == ref]
    if exact:
        return exact[0]
    named = [r for r in recipes if r.name == ref]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise RecipeNotFound(f"'{ref}' matches several sites; use the full id", [r.id for r in named])
    close = [r.id for r in recipes if ref.lower() in r.id.lower()]
    raise RecipeNotFound(f"no recipe '{ref}'", close or [r.id for r in recipes])


def search(site: str | None = None, query: str | None = None) -> list[Recipe]:
    recipes, _ = catalog()
    if site:
        s = site.lower()
        recipes = [r for r in recipes if s in r.site.lower()]
    if query:
        words = query.lower().split()
        recipes = [r for r in recipes if all(w in f"{r.name} {r.description} {r.site}".lower() for w in words)]
    return recipes
