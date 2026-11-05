# Security policy

Replay-Anything stores website logins on your machine and runs recipes that act on those sites as you,
so security reports are taken seriously.

## Reporting a vulnerability

Please report it privately through
[GitHub's private vulnerability reporting](https://github.com/ness001/replay-anything/security/advisories/new),
not in a public issue. Include the version, your OS, and steps to reproduce. You should get a reply within a
week.

## What the tool protects

- **Logins stay on your machine.** Sessions are saved in a per-user folder (`replay-anything where`),
  never in a recipe, and are never sent to any site other than the one they belong to.
- **Recipes contain no secrets.** A recipe holds steps and inputs only; cookies and tokens come from the
  saved session at run time.
- **Agents run only saved recipes.** Through the MCP server, an agent can run recipes from the recipes
  folder by name, not arbitrary recipe files.
- **A saved session only goes to its own site.** In a recipe that uses a saved session, a request or
  websocket to any other host is refused, so cookies never leave the site they belong to.
- **Recordings are redacted.** `record` writes a summary in which the values of fields named like
  secrets (tokens, codes, passwords and similar, in query strings, forms and JSON) are replaced by
  `<redacted>`; the raw HAR stays in the per-user folder. A secret in a URL path or free text is not
  detected, so check the summary before sharing it.

## Known limits

- `file` inputs can be any local file you can read. An agent could be tricked into uploading a sensitive
  file to a recipe's site. Review `run_recipe` calls that pass file paths.
- Failure screenshots and page text saved under `failures/` can contain private data.
- Anyone who can read your user folder can use your saved sessions, as with any browser profile.
