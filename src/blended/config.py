"""Blender discovery and version validation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from blended.errors import BlenderNotFoundError, BlenderVersionError

#: Minimum supported Blender. 4.2 is the first EEVEE Next LTS.
MIN_VERSION = (4, 2)

ENV_VAR = "BLENDED_BLENDER"

_CANDIDATES = (
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/Applications/Blender/Blender.app/Contents/MacOS/Blender",
    "/usr/local/bin/blender",
    "/opt/homebrew/bin/blender",
)

_VERSION_RE = re.compile(r"Blender\s+(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class BlenderInfo:
    path: Path
    version: tuple[int, int, int]

    @property
    def version_string(self) -> str:
        return ".".join(str(p) for p in self.version)

    def supports(self, major: int, minor: int) -> bool:
        return self.version[:2] >= (major, minor)


def _candidate_paths() -> list[Path]:
    paths = [Path(c) for c in _CANDIDATES]
    if which := shutil.which("blender"):
        paths.append(Path(which))
    return paths


def _read_version(path: Path) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION_RE.search(out)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


@lru_cache(maxsize=1)
def find_blender() -> BlenderInfo:
    """Locate a usable Blender, or explain what to do about it.

    Order: ``$BLENDED_BLENDER`` → known install locations → ``$PATH``.
    """
    # An explicit override is authoritative. Falling back to auto-discovery when it is wrong
    # would silently run a *different* Blender than the one that was asked for.
    if override := os.environ.get(ENV_VAR):
        path = Path(override)
        if not path.exists():
            raise BlenderNotFoundError(
                f"{ENV_VAR} points at {path}, which does not exist.",
                hint=f"Fix or unset {ENV_VAR} to fall back to auto-discovery.",
            )
        version = _read_version(path)
        if version is None:
            raise BlenderNotFoundError(
                f"{ENV_VAR} points at {path}, which is not a runnable Blender.",
                hint=f"Fix or unset {ENV_VAR} to fall back to auto-discovery.",
            )
        if version[:2] < MIN_VERSION:
            raise BlenderVersionError(
                f"{ENV_VAR} points at Blender {'.'.join(map(str, version))}, but "
                f">= {MIN_VERSION[0]}.{MIN_VERSION[1]} is required.",
            )
        return BlenderInfo(path=path, version=version)

    tried: list[str] = []
    for path in _candidate_paths():
        if not path.exists():
            tried.append(f"{path} (not found)")
            continue
        version = _read_version(path)
        if version is None:
            tried.append(f"{path} (could not read version)")
            continue
        if version[:2] < MIN_VERSION:
            raise BlenderVersionError(
                f"Blender {'.'.join(map(str, version))} at {path} is too old "
                f"(need >= {MIN_VERSION[0]}.{MIN_VERSION[1]})",
                hint=f"Install a newer Blender, or point {ENV_VAR} at one.",
            )
        return BlenderInfo(path=path, version=version)

    listing = "\n  ".join(tried) if tried else "(no candidates)"
    raise BlenderNotFoundError(
        "Could not find a Blender executable.",
        hint=f"Set {ENV_VAR}=/path/to/Blender. Tried:\n  {listing}",
    )


def backend_entrypoint() -> Path:
    """Path to the script Blender runs. Must stay importable without the host venv."""
    return Path(__file__).resolve().parent.parent / "blended_backend" / "__main__.py"
