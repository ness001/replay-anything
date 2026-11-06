"""A small local chat site for testing recipes, so nothing real gets spammed.

- POST /login (form user, password=pw) sets a `sid` cookie; GET /login is an HTML form.
- GET /api/session -> {csrfToken, user}; 401 without a valid session.
- POST /api/uploads (multipart file, X-CSRF-Token) -> {id}
- POST /api/channels/{c}/messages (json text, attachments) -> 201 {id, permalink}
- GET  /api/channels/{c}/messages
- WS   /ws: send {"type":"post","channel","text"} -> {"type":"posted","id",...}
- GET  /c/{channel}: HTML chat page (textarea, file input, send button) for browser steps.
- POST /admin/break_api {broken: bool}: make the JSON API return 500 to force the browser fallback.
- POST /admin/expire_sessions: forget all sessions.
"""

from __future__ import annotations

import html
import itertools
import secrets

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI()
SESSIONS: dict[str, dict] = {}
MESSAGES: dict[str, list[dict]] = {}
UPLOADS: dict[str, dict] = {}
STATE = {"broken": False}
_ids = itertools.count(1)


def _session(request: Request) -> dict:
    s = SESSIONS.get(request.cookies.get("sid", ""))
    if not s:
        raise HTTPException(401, "not signed in")
    return s


def _csrf(request: Request, token: str | None) -> dict:
    s = _session(request)
    if token != s["csrf"]:
        raise HTTPException(403, "bad csrf token")
    return s


def _api_guard():
    if STATE["broken"]:
        raise HTTPException(500, "api broken for testing")


def _post(channel: str, user: str, text: str, attachments: list[str]) -> dict:
    mid = f"m{next(_ids)}"
    msg = {
        "id": mid,
        "channel": channel,
        "user": user,
        "text": text,
        "attachments": [UPLOADS[a]["name"] for a in attachments if a in UPLOADS],
        "permalink": f"/c/{channel}#{mid}",
    }
    MESSAGES.setdefault(channel, []).append(msg)
    return msg


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """<form method=post action=/login><input name=user aria-label=User>
<input name=password type=password aria-label=Password><button>Sign in</button></form>"""


@app.post("/login")
def login(user: str = Form(...), password: str = Form(...)):
    if password != "pw":
        raise HTTPException(401, "wrong password")
    sid = secrets.token_hex(8)
    SESSIONS[sid] = {"user": user, "csrf": secrets.token_hex(8)}
    r = RedirectResponse("/c/general", status_code=303)
    r.set_cookie("sid", sid, httponly=True)
    return r


@app.get("/api/session")
def session(request: Request):
    _api_guard()
    s = _session(request)
    return {"csrfToken": s["csrf"], "user": s["user"]}


@app.post("/api/uploads")
async def upload(request: Request, file: UploadFile = File(...), x_csrf_token: str | None = Header(None)):
    _api_guard()
    _csrf(request, x_csrf_token)
    uid = f"f{next(_ids)}"
    UPLOADS[uid] = {"name": file.filename, "size": len(await file.read())}
    return {"id": uid, "name": file.filename}


@app.post("/api/channels/{channel}/messages", status_code=201)
async def post_message(channel: str, request: Request, x_csrf_token: str | None = Header(None)):
    _api_guard()
    s = _csrf(request, x_csrf_token)
    body = await request.json()
    if not body.get("text"):
        raise HTTPException(400, "text required")
    return _post(channel, s["user"], body["text"], body.get("attachments") or [])


@app.get("/api/channels/{channel}/messages")
def list_messages(channel: str, request: Request):
    _api_guard()
    _session(request)
    return {"messages": MESSAGES.get(channel, [])}


@app.websocket("/ws")
async def ws(socket: WebSocket):
    s = SESSIONS.get(socket.cookies.get("sid", ""))
    if not s:
        await socket.close(code=4401)
        return
    await socket.accept()
    await socket.send_json({"type": "hello", "user": s["user"]})
    while True:
        try:
            data = await socket.receive_json()
        except Exception:
            return
        if data.get("type") == "post":
            msg = _post(data["channel"], s["user"], data["text"], [])
            await socket.send_json({"type": "ack", "ref": data.get("ref")})
            await socket.send_json({"type": "posted", **msg})


@app.get("/c/{channel}", response_class=HTMLResponse)
def channel_page(channel: str, request: Request):
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    items = "".join(
        f'<li id="{m["id"]}" class=msg><span class=text>{html.escape(m["text"])}</span>'
        + "".join(f" <span class=att>{html.escape(a)}</span>" for a in m["attachments"])
        + "</li>"
        for m in MESSAGES.get(channel, [])
    )
    return f"""<html><body><h1>#{html.escape(channel)}</h1><ul id=messages>{items}</ul>
<form method=post action="/c/{channel}/send" enctype="multipart/form-data">
<textarea name=text aria-label="Message"></textarea><input type=file name=file>
<button aria-label="Send">Send</button></form></body></html>"""


@app.post("/c/{channel}/send")
async def channel_send(channel: str, request: Request, text: str = Form(...), file: UploadFile | None = File(None)):
    s = _session(request)
    att = []
    if file is not None and file.filename:
        uid = f"f{next(_ids)}"
        UPLOADS[uid] = {"name": file.filename, "size": len(await file.read())}
        att.append(uid)
    _post(channel, s["user"], text, att)
    return RedirectResponse(f"/c/{channel}", status_code=303)


@app.get("/rich/{channel}", response_class=HTMLResponse)
def rich_page(channel: str, request: Request):
    """A contenteditable composer that keeps pasted HTML, like Teams."""
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    items = "".join(
        f'<div class=rmsg id="{m["id"]}">{m["html"]}</div>' for m in MESSAGES.get(channel, []) if "html" in m
    )
    return f"""<html><body>{items}<div id=ed contenteditable=true aria-label="Type a message"></div>
<button name=send onclick="send()">Send</button><script>
ed.addEventListener('paste', e => {{ e.preventDefault(); ed.innerHTML = e.clipboardData.getData('text/html'); }});
async function send() {{
  await fetch('/rich/{channel}/send', {{method: 'POST', headers: {{'content-type': 'application/json'}},
    body: JSON.stringify({{html: ed.innerHTML}})}});
  setTimeout(() => location.reload(), 300);
}}</script></body></html>"""


@app.get("/tricky/{channel}", response_class=HTMLResponse)
def tricky_page(channel: str, request: Request):
    """Like some enterprise apps: a hidden copy of the Send button comes first,
    and the full label only shows on wide screens."""
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    return f"""<html><head><style>.wide{{display:none}} @media (min-width:1600px){{.wide{{display:inline}}}}</style></head>
<body><div style="display:none"><button name=send>Send</button></div>
<div id=ed contenteditable=true aria-label="Type a message"></div>
<button name=send onclick="send()">Send<span class=wide> message</span></button><script>
ed.addEventListener('paste', e => {{ e.preventDefault(); ed.innerHTML = e.clipboardData.getData('text/html'); }});
async function send() {{
  await fetch('/rich/{channel}/send', {{method: 'POST', headers: {{'content-type': 'application/json'}},
    body: JSON.stringify({{html: ed.innerHTML}})}});
}}</script></body></html>"""


@app.get("/redesigned/{channel}", response_class=HTMLResponse)
def redesigned_page(channel: str, request: Request):
    """The same chat after a redesign: the Send button is now a "Post" button, so old selectors stop matching."""
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    return f"""<html><body><textarea id=box aria-label="Message"></textarea>
<button name=post onclick="post()">Post</button><script>
async function post() {{
  await fetch('/rich/{channel}/send', {{method: 'POST', headers: {{'content-type': 'application/json'}},
    body: JSON.stringify({{html: box.value}})}});
}}</script></body></html>"""


@app.get("/loading/{channel}", response_class=HTMLResponse)
def loading_page(channel: str, request: Request):
    """Like Teams: a while after the chat looks ready, an "Almost there..." screen covers it for a few seconds."""
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    return f"""<html><body>
<div id=ed contenteditable=true aria-label="Type a message"></div>
<button name=send onclick="send()" disabled>Send</button>
<div id=splash style="position:fixed;inset:0;background:#eee;display:none">Almost there...</div><script>
// Send only works once startup finishes, behind the overlay
setTimeout(() => splash.style.display = 'block', 800);
setTimeout(() => {{ splash.remove(); document.querySelector('[name=send]').disabled = false; }}, 3800);
async function send() {{
  await fetch('/rich/{channel}/send', {{method: 'POST', headers: {{'content-type': 'application/json'}},
    body: JSON.stringify({{html: ed.innerHTML}})}});
}}</script></body></html>"""


@app.get("/spa/{channel}", response_class=HTMLResponse)
def spa_page(channel: str, request: Request):
    """Like some enterprise apps: the page loads its data itself with a token only its script knows."""
    if not SESSIONS.get(request.cookies.get("sid", "")):
        return RedirectResponse("/login", status_code=303)
    return f"""<html><body><ul id=out></ul><script>
fetch('/api/channels/{channel}/messages').then(r => r.json()).then(d => out.textContent = d.messages.length);
</script></body></html>"""


@app.post("/rich/{channel}/send")
async def rich_send(channel: str, request: Request):
    s = _session(request)
    body = await request.json()
    msg = _post(channel, s["user"], "", [])
    msg["html"] = body["html"]
    return JSONResponse({"id": msg["id"]}, status_code=201)


@app.get("/guarded", response_class=HTMLResponse)
def guarded_page():
    """Like a bot-defense script: the page's own JavaScript sets a cookie the API then requires."""
    return "<html><body><script>document.cookie = 'js_check=passed; path=/'</script>guarded</body></html>"


@app.post("/guarded/api/echo")
async def guarded_echo(request: Request):
    if request.cookies.get("js_check") != "passed":
        raise HTTPException(403, "bot check: no js_check cookie")
    form = await request.form()
    upload = form.get("file")
    return {
        "user": _session(request)["user"],
        "user_agent": request.headers.get("user-agent", ""),
        "text": form.get("text"),
        "file": (await upload.read()).decode() if upload else None,
    }


@app.post("/guarded/api/json")
async def guarded_json(request: Request):
    return {"content_type": request.headers.get("content-type"), "body": await request.json()}


@app.api_route("/go", methods=["GET", "POST"])
def go(to: str):
    """An open redirect, like many sites have: lets tests check that a session never follows one off-site."""
    return RedirectResponse(to, status_code=302)


@app.get("/whoami")
def whoami(request: Request):
    return {"sid": request.cookies.get("sid")}


@app.post("/admin/break_api")
async def break_api(request: Request):
    STATE["broken"] = bool((await request.json()).get("broken"))
    return STATE


@app.post("/admin/expire_sessions")
def expire_sessions():
    SESSIONS.clear()
    return {"ok": True}


@app.post("/admin/reset")
def reset():
    SESSIONS.clear()
    MESSAGES.clear()
    UPLOADS.clear()
    STATE["broken"] = False
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import sys

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 18765)
