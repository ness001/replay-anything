"""Errors the runtime reports to agents. Each one renders to a plain, actionable message."""

from __future__ import annotations


class ReplayError(Exception):
    kind = "error"

    def to_dict(self) -> dict:
        return {"error": self.kind, "message": str(self)}


class RecipeInvalid(ReplayError):
    kind = "recipe_invalid"


class RecipeNotFound(ReplayError):
    kind = "recipe_not_found"

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []

    def to_dict(self) -> dict:
        return {**super().to_dict(), "candidates": self.candidates}


class BadInputs(ReplayError):
    kind = "bad_inputs"

    def __init__(self, problems: list[str], expected: dict):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.expected = expected

    def to_dict(self) -> dict:
        return {**super().to_dict(), "problems": self.problems, "expected_inputs": self.expected}


class SessionExpired(ReplayError):
    kind = "session_expired"

    def __init__(self, site: str, detail: str):
        super().__init__(
            f"The saved session for {site} is missing or expired ({detail}). "
            f"Log in again with: replay-anything login {site}"
        )
        self.site = site


class StepFailed(ReplayError):
    kind = "step_failed"

    def __init__(
        self,
        step: str,
        message: str,
        fallback_error: str | None = None,
        saved: str | None = None,
        site_may_have_changed: bool = True,
    ):
        text = f"step '{step}' failed: {message}"
        if fallback_error:
            text += f"; browser fallback also failed: {fallback_error}"
        super().__init__(text)
        self.step = step
        self.detail = message
        self.fallback_error = fallback_error
        self.saved = saved  # folder with the failed page's screenshot and text, for browser steps
        # False when the recipe itself can't do what was asked (no fallback, nothing to dry-run before)
        self.site_may_have_changed = site_may_have_changed

    def to_dict(self) -> dict:
        d = {**super().to_dict(), "step": self.step}
        if self.fallback_error:
            d["fallback_error"] = self.fallback_error
        return d
