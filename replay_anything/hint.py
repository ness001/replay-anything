"""Hook support: when an agent is about to drive a browser with playwright-cli or agent-browser,
tell it which saved recipes already cover that site, so it can run one in a single step instead."""

from __future__ import annotations

import re

from . import recipe as recipes
from .paths import host_of

TOOLS = re.compile(r"""(?:^|[\s;&|("'])(?:playwright-cli|agent-browser|@playwright/cli)(?:@\S*|\.cmd|\.exe)?(?=\s|$)""")
URL = re.compile(r"""https?://[^\s'"<>]+""")
NAV = re.compile(r"""\b(?:open|goto|navigate|tab-new)\s+['"]?([A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?::\d+)?)(?:[/'"\s]|$)""")
FILE_EXT = re.compile(r"\.(?:html?|json|ya?ml|txt|pdf|png|jpe?g|js|md|xml|csv)$", re.I)


def sites_in(command: str) -> list[str]:
    """Hosts a playwright-cli / agent-browser command opens; empty if it isn't such a command."""
    if not TOOLS.search(command):
        return []
    found = URL.findall(command) + [s for s in NAV.findall(command) if not FILE_EXT.search(s.split(":")[0])]
    hosts: list[str] = []
    for s in found:
        try:
            h = host_of(s)
        except ValueError:
            continue
        if h not in hosts:
            hosts.append(h)
    return hosts


def _same_site(recipe_host: str, host: str) -> bool:
    """Same host and port, or the command opens a subdomain of the recipe's host (www.x.com for x.com)."""
    if ":" in recipe_host or ":" in host:
        return recipe_host == host
    return host == recipe_host or host.endswith("." + recipe_host)


def hint(command: str) -> str | None:
    """A short note naming the recipes for the sites in `command`, or None."""
    hosts = sites_in(command)
    if not hosts:
        return None
    catalog, _ = recipes.catalog()
    found = [r for r in catalog if any(_same_site(r.host, h) for h in hosts)]
    if not found:
        return None
    lines = [
        f"- {r.id}: {(r.description.splitlines() or [''])[0]} (inputs: "
        f"{', '.join(f'{k}: {v}' for k, v in r.input_summary().items()) or 'none'})"
        for r in found[:10]
    ]
    return (
        "Replay-Anything has saved recipes for this site. If one matches the task, the MCP tool "
        "run_recipe does it in one step (CLI: replay-anything run <recipe> -i name=value); the browser "
        "is still available if no recipe fits or run_recipe returns step_failed.\n" + "\n".join(lines)
    )
