"""Browser launch fallback: a profile one browser can't open must not block the others."""

import pytest

from replay_anything import browser


class _FakeChromium:
    def __init__(self, fails: dict[str, str]):
        self.fails, self.tried, self.opts = fails, [], []

    def _start(self, opts):
        channel = opts.get("channel") or "bundled"
        self.tried.append(channel)
        self.opts.append(opts)
        if channel in self.fails:
            raise RuntimeError(self.fails[channel])
        return f"ctx:{channel}"

    def launch_persistent_context(self, profile, **opts):
        return self._start(opts)

    def launch(self, **opts):
        return self._start(opts)


class _FakePw:
    def __init__(self, fails):
        self.chromium = _FakeChromium(fails)


CRASH = "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
MISSING = "Executable doesn't exist at C:/x/msedge.exe"


def test_profile_edge_crashes_falls_back_to_chrome(capsys):
    """Edge fails on a profile copy made by another tool; Chrome opens it fine (seen with playwright-cli profiles)."""
    pw = _FakePw({"msedge": CRASH})
    _, ctx = browser.launch(pw, profile="p")
    assert ctx == "ctx:chrome"
    assert pw.chromium.tried == ["msedge", "chrome"]
    assert pw.chromium.opts[-1]["viewport"] == browser.VIEWPORT
    assert "msedge could not open profile p; using chrome" in capsys.readouterr().err


def test_profile_no_browser_opens_lists_each_error():
    pw = _FakePw({"msedge": CRASH, "chrome": "Target closed", "bundled": "exitCode=21"})
    with pytest.raises(RuntimeError, match="no browser could open profile p") as e:
        browser.launch(pw, profile="p")
    assert "msedge: " in str(e.value) and "bundled chromium: exitCode=21" in str(e.value)
    assert "has been closed" in str(e.value.__cause__)


def test_profile_edge_crashes_others_missing_reports_crash():
    pw = _FakePw({"msedge": CRASH, "chrome": MISSING.replace("msedge", "chrome"), "bundled": MISSING})
    with pytest.raises(RuntimeError, match="no browser could open profile"):
        browser.launch(pw, profile="p")


def test_locked_profile_is_not_retried():
    pw = _FakePw({"msedge": "The user data directory is already in use"})
    with pytest.raises(RuntimeError, match="already in use"):
        browser.launch(pw, profile="p")
    assert pw.chromium.tried == ["msedge"]


def test_without_profile_a_crash_is_raised():
    pw = _FakePw({"msedge": CRASH})
    with pytest.raises(RuntimeError, match="has been closed"):
        browser.launch(pw)
    assert pw.chromium.tried == ["msedge"]


def test_not_installed_is_case_insensitive():
    pw = _FakePw({"msedge": "Chromium distribution 'msedge' IS NOT FOUND"})
    _, ctx = browser.launch(pw, profile="p")
    assert ctx == "ctx:chrome"
