"""Host side of the Blender bridge.

Spawns `blender -b --python blended_backend/__main__.py -- --job J --result R`, then reads the
result *file*. Blender's stdout is far too noisy to parse, and its exit code is unreliable
(it returns 0 even on an uncaught Python exception), so the result file is the only signal
we trust. See CLAUDE.md.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from blended.config import BlenderInfo, backend_entrypoint, find_blender
from blended.engine.result import SCHEMA_VERSION, JobResult
from blended.errors import BackendError

#: Tail of the Blender log surfaced inline when a job fails.
_LOG_TAIL_LINES = 40


@dataclasses.dataclass
class RunOptions:
    #: Keep the work directory even when the job succeeds (job.json / result.json / blender.log).
    keep_workdir: bool = False
    #: Hard limit for a single Blender invocation.
    timeout_s: float = 900.0
    #: Stream Blender's output to the terminal as it runs.
    verbose: bool = False


def run_job(job: dict[str, Any], options: RunOptions | None = None) -> JobResult:
    """Execute one job in Blender and return its structured result.

    Raises `BackendError` if the job failed, Blender crashed, or no readable result was produced.
    """
    options = options or RunOptions()
    blender = find_blender()

    job = dict(job)
    job.setdefault("job_id", uuid.uuid4().hex[:12])
    job["schema_version"] = SCHEMA_VERSION

    workdir = Path(tempfile.mkdtemp(prefix=f"blended-{job['job_id']}-"))
    job_path = workdir / "job.json"
    result_path = workdir / "result.json"
    log_path = workdir / "blender.log"

    job_path.write_text(json.dumps(job, indent=2))

    # On failure the workdir is deliberately preserved — job.json, result.json and blender.log
    # are exactly what you need to debug, and BackendError carries the path.
    completed = _invoke(blender, job_path, result_path, log_path, options)
    result = _read_result(result_path, log_path, completed)
    if not result.ok:
        raise _backend_error(result, log_path)

    result = dataclasses.replace(result, log_path=log_path)
    if not options.keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
        result = dataclasses.replace(result, log_path=None)
    return result


def _invoke(
    blender: BlenderInfo,
    job_path: Path,
    result_path: Path,
    log_path: Path,
    options: RunOptions,
) -> subprocess.CompletedProcess:
    cmd = [
        str(blender.path),
        "--background",
        "--factory-startup",  # ignore user prefs/addons so runs are reproducible
        "--python-exit-code",
        "1",
        "--python",
        str(backend_entrypoint()),
        "--",
        "--job",
        str(job_path),
        "--result",
        str(result_path),
    ]

    with log_path.open("w") as log:
        try:
            return subprocess.run(
                cmd,
                stdout=None if options.verbose else log,
                stderr=subprocess.STDOUT if not options.verbose else None,
                text=True,
                timeout=options.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendError(
                f"Blender exceeded the {options.timeout_s:.0f}s timeout.",
                hint="Reduce frame count or resolution, or raise RunOptions.timeout_s.",
                log_path=str(log_path),
            ) from exc


def _read_result(
    result_path: Path,
    log_path: Path,
    completed: subprocess.CompletedProcess,
) -> JobResult:
    if not result_path.exists():
        raise BackendError(
            f"Blender produced no result file (exit code {completed.returncode}) — it likely "
            "crashed or raised before the backend could write one.",
            hint=_log_tail(log_path),
            log_path=str(log_path),
        )
    try:
        data = json.loads(result_path.read_text())
    except (OSError, ValueError) as exc:
        raise BackendError(
            f"Blender wrote an unreadable result file: {exc}",
            hint=_log_tail(log_path),
            log_path=str(log_path),
        ) from exc

    result = JobResult.from_dict(data)
    if result.schema_version != SCHEMA_VERSION:
        raise BackendError(
            f"Result schema mismatch: host expects v{SCHEMA_VERSION}, "
            f"backend produced v{result.schema_version}.",
            hint="src/blended/engine/result.py and src/blended_backend/result.py have drifted.",
            log_path=str(log_path),
        )
    return result


def _backend_error(result: JobResult, log_path: Path) -> BackendError:
    err = result.error or {}
    return BackendError(
        err.get("message") or "The Blender job failed without reporting a reason.",
        hint=err.get("hint") or _log_tail(log_path),
        traceback_text=err.get("traceback"),
        log_path=str(log_path),
    )


def _log_tail(log_path: Path) -> str:
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return f"(could not read {log_path})"
    tail = lines[-_LOG_TAIL_LINES:]
    return "Blender output (tail):\n  " + "\n  ".join(tail) if tail else "(no Blender output)"
