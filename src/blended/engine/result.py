"""The host↔backend wire contract.

Both sides must agree on this shape, but they cannot share code — the backend runs inside
Blender's Python with no access to the host venv (CLAUDE.md). So this module defines the
contract for the host, and `blended_backend.result` mirrors it in plain stdlib.

Keep the two in sync. `SCHEMA_VERSION` is the tripwire if they drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class JobResult:
    ok: bool
    schema_version: int
    job_id: str
    blender_version: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    #: Set by the runner, not the backend — where stdout/stderr were captured.
    log_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobResult:
        return cls(
            ok=bool(data.get("ok", False)),
            schema_version=int(data.get("schema_version", 0)),
            job_id=str(data.get("job_id", "")),
            blender_version=data.get("blender_version"),
            artifacts=data.get("artifacts") or {},
            stats=data.get("stats") or {},
            diagnostics=data.get("diagnostics") or [],
            error=data.get("error"),
        )

    @property
    def blend(self) -> Path | None:
        p = self.artifacts.get("blend")
        return Path(p) if p else None

    @property
    def video(self) -> Path | None:
        p = self.artifacts.get("video")
        return Path(p) if p else None
