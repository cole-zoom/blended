"""Error types for the host process.

Every error carries a `code` so diagnostics stay machine-readable — see ARCHITECTURE §12,
where CLI diagnostics become the agent's primary interface.
"""

from __future__ import annotations


class BlendedError(Exception):
    """Base for all blended errors."""

    code = "BLENDED_ERROR"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class BlenderNotFoundError(BlendedError):
    code = "BLENDER_NOT_FOUND"


class BlenderVersionError(BlendedError):
    code = "BLENDER_VERSION_UNSUPPORTED"


class BackendError(BlendedError):
    """The Blender-side job failed.

    Note: Blender exits 0 even on an uncaught Python exception, so this is raised based on the
    result file, not the exit code. See CLAUDE.md.
    """

    code = "BACKEND_FAILED"

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        traceback_text: str | None = None,
        log_path: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.traceback_text = traceback_text
        self.log_path = log_path

    def as_dict(self) -> dict:
        d = super().as_dict()
        d["traceback"] = self.traceback_text
        d["log_path"] = self.log_path
        return d
