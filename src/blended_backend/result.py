"""Backend mirror of the host's result contract.

Runs inside Blender's Python: stdlib only, no host imports. Mirrors
`blended.engine.result` — keep them in sync, `SCHEMA_VERSION` is the tripwire.
"""

from __future__ import annotations

import json
import traceback

SCHEMA_VERSION = 1


def success(job_id, *, blender_version, artifacts, stats=None, diagnostics=None):
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "blender_version": blender_version,
        "artifacts": artifacts,
        "stats": stats or {},
        "diagnostics": diagnostics or [],
        "error": None,
    }


def failure(job_id, exc, *, blender_version=None, hint=None, code="BACKEND_EXCEPTION"):
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "blender_version": blender_version,
        "artifacts": {},
        "stats": {},
        "diagnostics": [],
        "error": {
            "code": code,
            "message": f"{type(exc).__name__}: {exc}",
            "hint": hint,
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        },
    }


def write(path, payload):
    """Write the result atomically — a truncated result file is indistinguishable from a crash."""
    tmp = f"{path}.partial"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
    import os

    os.replace(tmp, path)
