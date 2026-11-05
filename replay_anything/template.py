"""`{{step.field}}` rendering and `when` conditions.

A string that is exactly one placeholder keeps the value's type (list, int, None).
A placeholder that points at a skipped step or an omitted optional input resolves to None;
None items are dropped from lists, so `attachments: ["{{upload.file_id}}"]` becomes `[]`.
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}")
_MISSING = object()


class TemplateError(Exception):
    pass


def lookup(path: str, scope: dict) -> Any:
    head, *rest = path.split(".")
    if head not in scope:
        raise TemplateError(f"unknown name '{head}' in '{{{{{path}}}}}'")
    value = scope[head]
    for part in rest:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(part, _MISSING)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            value = _MISSING
        if value is _MISSING:
            raise TemplateError(f"'{path}' has no field '{part}'")
    return value


def render(value: Any, scope: dict) -> Any:
    if isinstance(value, str):
        whole = PLACEHOLDER.fullmatch(value.strip())
        if whole:
            return lookup(whole.group(1), scope)

        def sub(m: re.Match) -> str:
            v = lookup(m.group(1), scope)
            return "" if v is None else str(v)

        return PLACEHOLDER.sub(sub, value)
    if isinstance(value, list):
        return [x for x in (render(v, scope) for v in value) if x is not None]
    if isinstance(value, dict):
        return {k: render(v, scope) for k, v in value.items()}
    return value


def render_url(value: str, scope: dict) -> str:
    """Like render, but percent-encodes each inserted value so it cannot change the path or query."""
    from urllib.parse import quote

    def sub(m: re.Match) -> str:
        v = lookup(m.group(1), scope)
        return "" if v is None else quote(str(v), safe="")

    return PLACEHOLDER.sub(sub, value)


def placeholders(value: Any) -> list[str]:
    """All placeholder paths used anywhere in a value (for validation)."""
    if isinstance(value, str):
        return PLACEHOLDER.findall(value)
    if isinstance(value, list):
        return [p for v in value for p in placeholders(v)]
    if isinstance(value, dict):
        return [p for v in value.values() for p in placeholders(v)]
    return []


_COMPARE = re.compile(r"^(?P<left>[\w.\-]+)\s*(?P<op>==|!=)\s*(?P<right>.+)$")


def condition(expr: Any, scope: dict) -> bool:
    """`when:` supports `path`, `not path`, `path == value` and `path != value`."""
    if isinstance(expr, bool):
        return expr
    text = PLACEHOLDER.sub(lambda m: m.group(1), str(expr)).strip()
    if text.startswith("not "):
        return not condition(text[4:], scope)
    cmp = _COMPARE.match(text)
    if cmp:
        left = lookup(cmp["left"], scope)
        right = cmp["right"].strip().strip("'\"")
        equal = str(left) == right if left is not None else right in ("null", "None", "")
        return equal if cmp["op"] == "==" else not equal
    return bool(lookup(text, scope))
