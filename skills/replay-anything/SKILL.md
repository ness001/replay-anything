---
name: replay-anything
description: Build a reusable website recipe from one recorded run, so agents can later do the same operation in one step with run_recipe. Use when the user says "make a recipe for <operation> on <site>", "record this so you can replay it", "/replay-anything", or repeats the same website operation (post a message, file a form, download a report) more than once. v1 covers websites only.
---

# Build a Replay-Anything recipe

A recipe is a small YAML file that replays one named website operation with new inputs. The
`replay-anything` MCP server (`list_recipes`, `run_recipe`) and CLI run it. This skill builds it.

Before building, call `list_recipes` (or `replay-anything list`): a recipe may already exist.

The plugin does not add `replay-anything` to `PATH`. Use `python launch.py cli <command>` from a
source checkout, or install the CLI separately (`uv tool install
'git+https://github.com/ness001/replay-anything.git'`). When using the plugin, replace
`replay-anything <command>` in the examples below with
`python "${CLAUDE_PLUGIN_ROOT}/launch.py" cli <command>`; expand the plugin root to its installed
folder if the shell does not have `CLAUDE_PLUGIN_ROOT` set. `where` prints the recipe and session folders.

## 1. Agree on the operation

Get from the user, in one short question if missing:
- the site URL and the exact operation ("post a message with an optional image to a channel");
- the inputs that change per run (name, type: string/number/integer/boolean/file/object/array, optional?);
- what to return (message id, URL, …) and how to confirm it worked on the site.

Name the recipe in snake_case after the operation (`post_chat`, not `teams_api_call`).

## 2. Log in once (the user does SSO/2FA)

```bash
replay-anything login https://site.example.com [--check /api/me]
```
A real browser window opens with no automation of the login itself. Tell the user to sign in and
close the window. The session is saved in the per-user sessions folder. **Never copy cookies,
tokens or passwords into a recipe, a repo, a log or a chat message.**

**Already signed in with playwright-cli or agent-browser?** Link that login instead of signing in
again. Only the path is stored, and the file is read fresh on every run. Only cookies for the
recipe's site are used.
```bash
playwright-cli state-save ~/.auth/site.json      # or: agent-browser state save ~/.auth/site.json
replay-anything link https://site.example.com --state ~/.auth/site.json
replay-anything link https://site.example.com --profile <dir>   # a --profile folder (SSO sites); close that browser first; use a folder only for this site
replay-anything link https://site.example.com --remove
replay-anything export-state https://site.example.com out.json  # then: playwright-cli state-load out.json / agent-browser --state out.json open <url>
```
Keep these files outside any repo. A later `replay-anything login` replaces the link.

**Exploring first with playwright-cli or agent-browser:** when you write `browser` steps, turn
their element refs into stable selectors (`playwright-cli generate-locator e15`; for
agent-browser, use the role and name shown in `snapshot`). Refs change between page loads, so never put
a ref like `e15` or `@e1` in a recipe. To find which requests to replay, use their request lists
(`playwright-cli requests` / `request <n>`, or `agent-browser network`), or use `record` below.

**Run recipes first:** if a recipe exists for the task, call `run_recipe`. Drive the browser
with playwright-cli or agent-browser only when no recipe fits, or the recipe fails with
`step_failed`.

## 3. Record the operation once

```bash
replay-anything record https://site.example.com --out <new folder outside any repo>
```
Ask the user to do the operation once in the window with *recognisable sample inputs* (for example
text `replay-test-123`), then close the tab. `--out` gets `requests.json`: requests ranked by
likelihood of mattering (non-GET, JSON, same site first), with secret headers, query values and
body fields redacted. The raw HAR (with cookies) stays in the per-user `recordings` folder; its
path is printed as `raw_har`. Don't read it into the chat, and delete it when you're finished.

## 4. Find the essential requests

In `requests.json`, find the request whose body contains the sample input; that is the core step.
Then walk backwards for values it needs that are not inputs or cookies:
- CSRF/XSRF tokens, ids, upload handles → an earlier step that returns them (`extract`);
- values only in HTML → `extract: {csrf: "re:name=\"csrf\" value=\"([^\"]+)\""}`;
- headers set by JS from storage (bearer tokens, signatures) → request replay will fail;
  use a `browser` step or rely on the `fallback`.
Drop analytics, telemetry, prefetches and anything the core request does not depend on.

## 5. Write the recipe

Save it to the recipes folder (`replay-anything where`) as `<name>.yaml`:

```yaml
name: post_chat
description: Post a message with an optional image to a channel.   # first line = list_recipes summary
site: https://chat.example.com
auth: session            # or none
notes: Built from a recording on 2026-11-03; send is replayed as a request.   # optional: how built, limits
# transport: browser   # optional: send request steps with fetch() from a real page (bot-protected sites)
# headed: true         # optional: visible window instead of headless
# pacing: human        # optional: random 0.5-2 s between steps and actions
inputs:
  channel: {type: string}
  text:    {type: string}
  image:   {type: file, optional: true}
steps:
  - id: token
    request: GET /api/session
    extract: {csrf: $.csrfToken}
  - id: upload
    when: inputs.image                  # path | not path | path == value | path != value
    request: POST /api/uploads
    headers: {X-CSRF-Token: "{{token.csrf}}"}
    multipart: {file: "{{inputs.image}}"}
    extract: {file_id: $.id}
  - id: send
    request: POST /api/channels/{{inputs.channel}}/messages
    headers: {X-CSRF-Token: "{{token.csrf}}"}
    json: {text: "{{inputs.text}}", attachments: ["{{upload.file_id}}"]}
    expect: {status: 201}               # also json: {$.ok: true}, contains: "text"
    extract: {message_id: $.id, url: $.permalink}
output: {message_id: "{{send.message_id}}", url: "{{send.url}}"}
fallback:
  browser:
    - goto: /c/{{inputs.channel}}
    - fill:   {selector: "textarea[aria-label='Message']", value: "{{inputs.text}}"}
    - upload: {selector: "input[type=file]", file: "{{inputs.image}}"}   # skipped when empty
    - click:  {selector: "button[aria-label='Send']"}
    - wait_for: {text: "{{inputs.text}}"}
    - extract: {message_id: {selector: "li.msg", attr: id}}
  output: {message_id: "{{browser.message_id}}"}
```

Reference:
- Step types: `request: METHOD /path` (body: one of `json`, `form`, `multipart`, `body`; plus
  `headers`, `query`), `websocket: /path` (`send`, `receive: {match: {$.type: posted}, timeout: 10}`,
  `extract`), `browser: [actions]`. A browser step may set `for_each: "{{inputs.ids}}"` (a list):
  its actions run once per item in the same browser, with `{{item}}` set, and the step's value is a
  list of `{item, ok, ...extracted}` or `{item, ok: false, error}` (a failed item doesn't stop the
  rest; failed items keep their screenshot path in `saved`). Items share one page, so start the
  actions with a `goto` (a failed item can leave a dialog open). `item` is reserved as a step id.
  Use it to read many pages with one sign-in instead of one run per page.
- Browser actions: `goto` (or `{url, wait_response}` to read JSON the page loads itself, e.g. an API
  called with a token kept in the page), `fill`, `paste_html {selector, html | file}` (rich editors keep
  tables/links), `click` (`wait_response: {url, status, extract: {name: $.path}}` waits until the site accepted it and reads ids from its reply), `press
  {selector, key}`, `upload`, `wait_for {text|selector|url}` (`gone: true` waits for a loading screen to show, up to `appear` ms, default 10000, then until no visible match is left (so every run waits up to `appear` ms when it never shows); aim it at the loading element, not text a chat message could contain), `extract {name: selector | {selector,
  attr | html: true|clean, all: true, last: n, has_text}}`. Put inputs in `has_text:` (literal text), never inside a quoted selector. Any mapping action may set `timeout` (ms).
- Sites with corporate SSO keep their sign-in in the persistent browser profile that `login`
  creates; their recipes usually need `browser` steps.
- `extract`: `$.json.path`, `header:Name`, or `re:regex` (first group). Every step also exposes `status`.
- `{{x}}` alone keeps the value's type; a skipped step or empty optional input is `null`, and nulls
  are dropped from lists. Steps run in order; a step may refer only to earlier steps.
- Default success check: HTTP < 400. A 401/403, or a redirect to a login page, is reported as
  `session_expired`.
- Prefer stable selectors: `aria-label`, `role`, `name`, `data-testid`; avoid generated class names.
- `click`, `fill` and `press` use the first *visible* match, so hidden copies of a button (other dialogs,
  collapsed layouts) are skipped. Browser runs use a 1920x1080 window, headless too.
- Mark the action that changes the site with `wait_response` (preferred: it also checks the site accepted
  it) or `submits: true`, so `dry_run` knows where to stop. A `press` of Enter counts as submitting;
  add `submits: false` when it only searches. Without a marked action, `dry_run` refuses to run.
- Never put an input inside quotes in a selector (`:has-text('{{inputs.text}}')`): a quote in the
  value breaks it. Use `wait_for: {text: ...}`, which takes the value literally. `"li >> text=..."`
  is a weaker option: a value containing `>>` or wrapped in quotes still changes it. `validate` warns.
- Write `notes:` saying how the recipe was built (recording or hand-written, date, why a browser
  step) and known limits, so a later reader can tell.
- Add a `fallback` when the UI is stable enough; it runs only if request replay fails.
- If the recording shows bot-defense cookies or scripts (`_abck`, `_px*`, `cf_clearance`, `datadome`),
  or replayed requests get 403 while the browser works, set `transport: browser`; add `headed: true` if
  headless is still refused. Never add stealth tricks (faking `navigator.webdriver`, user agent,
  fingerprints): replay-anything does not hide automation.

Validate: `replay-anything validate <file>`.

## 6. Test with new inputs and confirm on the site

1. `replay-anything run <name> -i text="replay-test-<random>" …` with inputs **different** from the
   recording. Expect `"ok": true, "via": "request"`.
2. Confirm on the site that the result is really there (read it back with a GET, or look in the
   browser). A 2xx alone is not proof.
3. If the recipe has a fallback, also run `--browser` once and confirm.
4. Test one bad input and confirm the error lists the expected inputs.
5. Run once more with awkward text values: quotes, `<b>&`, non-ASCII and a newline, for example
   `-i text="it's \"q\" <b>& 你好"`. Confirm the result shows the text unchanged.
5. Post test data only where the user agreed (their own test channel, self-chat, a draft). Ask
   before anything visible to other people.

## 7. Hand over

Tell the user the recipe id (`host/name`), how agents call it (`run_recipe`), and the CLI line for
scripts. Delete the `raw_har` file and the `--out` folder.

## Auto-save (no recording, no user steps)

When the Replay-Anything hook (Claude Code) or your instructions (Codex/Copilot) say auto-save is on, build
the recipe from the browser work you just did, without asking the user to log in or record:

1. Skip it if the work was exploration, failed, or isn't a task the user would repeat.
2. Write `browser` steps that repeat what you did: stable selectors (`playwright-cli generate-locator <ref>`,
   or role and name from `agent-browser snapshot`), never `e15`/`@e1` refs. Turn the values the user gave
   (text, channel, file) into `inputs`. Use `auth: session` if the site needed a login, and make sure the
   login is available (`replay-anything link <site> --state <file>` with the tool's saved state).
3. `notes:` says it was auto-saved on <date> from a <tool> session.
4. Run `replay-anything validate <file>` and save it to the recipes folder.
5. **Check it with `dry_run`.** `run_recipe(recipe, inputs, dry_run=true)` runs every action up to the
   submitting click and stops. If it fails, the message names the action and a folder with a screenshot.
   **Test only read-only recipes for real.** If the recipe changes something on the site (posts, sends, edits,
   deletes), add `tested: false` and do not run it. Its first real run is the test. `list_recipes` shows
   `tested: false` until then, and a successful run clears it.
6. Tell the user in one line: the recipe id, and that `replay-anything auto off` disables auto-save.

## Errors you will see from run_recipe

| error | meaning | do |
|---|---|---|
| `bad_inputs` | wrong/missing inputs | fix from `expected_inputs` |
| `session_expired` | login gone | ask the user to run `replay-anything login <site>` |
| `step_failed` | names the step | the site may have changed: see "Fix a recipe" below (`fix` has the file and failure folder) |
| `recipe_not_found` | lists close matches | use the full id |

## Fix a recipe after the site changed

When `run_recipe` returns `step_failed` for a recipe that used to work, the site has usually changed.
The result has a `fix` field with the `recipe_file` and, for browser steps, a `failure_folder` holding
`page.png` and `page.txt` of the page where it failed. Repair the recipe instead of rebuilding it:

1. **Find what broke.** The message names the step and, for browser steps, the action number and its
   selector. Look at `page.png` and `page.txt` to see what the page shows now.
2. **Redo only that part.** Open the site with playwright-cli or agent-browser and get to the same point.
   Find the new selector (`playwright-cli generate-locator <ref>`, or role and name from
   `agent-browser snapshot`) or, for a request step, the new request in their request list.
3. **Patch only the broken step** in `recipe_file`. Keep the other steps, the inputs and the checks
   (for example the step that confirms the right chat or page is open). Never weaken a check to make it pass.
4. **Verify:** `replay-anything validate <file>`. If the recipe changes the site, run it with `dry_run=true`;
   its next real run is the test. If it only reads, skip the dry run (it has nothing to stop before) and run it.
5. **Record it** in `notes:`, for example `2026-11-06: Send button renamed to Post; fixed the click selector.`
6. Tell the user in one line what changed.

If several steps broke, or the flow itself changed (new pages, different order), rebuild the recipe from a
fresh run instead (section 3, or auto-save).
