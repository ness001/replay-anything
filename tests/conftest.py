import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "local-chat"
PORT = 18765
SITE = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def server():
    from chat_site import app

    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            pytest.fail(f"port {PORT} is already in use (another test run?); the example recipes expect this port")
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"test server did not start on port {PORT}")
    yield SITE
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REPLAY_ANYTHING_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REPLAY_ANYTHING_RECIPES", str(EXAMPLES))
    return tmp_path / "home"


@pytest.fixture
def site(server, home):
    httpx.post(f"{SITE}/admin/reset")
    yield SITE
    httpx.post(f"{SITE}/admin/reset")


def save_login(user="alice"):
    """Sign in like a user would, then save the cookies as a Playwright storage state."""
    from replay_anything import sessions

    r = httpx.post(f"{SITE}/login", data={"user": user, "password": "pw"})
    sid = r.cookies["sid"]
    state = {
        "cookies": [
            {
                "name": "sid",
                "value": sid,
                "domain": "127.0.0.1",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    sessions.path(SITE).write_text(json.dumps(state), encoding="utf-8")
    return sid


def messages(sid, channel="general"):
    return httpx.get(f"{SITE}/api/channels/{channel}/messages", cookies={"sid": sid}).json()["messages"]
