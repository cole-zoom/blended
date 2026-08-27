"""Render cache: don't re-render what hasn't changed. Host side.

Keyed on everything that can alter the pixels — the resolved IR plus the stage's render
settings. Anything else (the note on a patch, the time of day) is excluded, or the cache would
miss constantly and be worse than useless.

Live reload took most of the pressure off this: iterating on look no longer costs a render at
all. What remains is the expensive tail — re-running `final` after changing nothing but a
comment, or re-rendering `lighting` when you only touched a material two stages later.

The cache answers "has this exact thing already been rendered?" It deliberately does **not**
answer "is the output still on disk?" beyond checking existence, because a half-written file
from an interrupted run is the sequence renderer's problem and it already handles it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = ".rendercache.json"

#: Render settings that change the image. `frame_step` counts; a timeout does not.
KEYED_RENDER_FIELDS = (
    "engine", "device", "resolution", "fps", "frame_start", "frame_end",
    "frame_step", "samples", "media",
)


def fingerprint(job: dict) -> str:
    """Stable hash of everything that can change the pixels."""
    render = job.get("render", {})
    payload = {
        "ir": job.get("scene", {}).get("ir"),
        "render": {k: render.get(k) for k in KEYED_RENDER_FIELDS},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _artifact_exists(path: str) -> bool:
    """True if the artifact is on disk, whether it is a file or a frame-sequence prefix."""
    target = Path(path)
    if target.exists():
        return True
    # A prefix: anything matching it counts, but a zero-byte frame does not — an interrupted
    # write must not be mistaken for a finished render.
    matches = list(target.parent.glob(f"{target.name}*")) if target.parent.exists() else []
    return any(m.is_file() and m.stat().st_size > 0 for m in matches)


@dataclass(frozen=True)
class CacheHit:
    key: str
    rendered_at: str
    artifacts: dict[str, str]

    def is_present(self) -> bool:
        """A recorded artifact that no longer exists is a miss, not a hit.

        Stills and sequence stages record a *prefix* (`..._assets_`) rather than a file, since
        they produce many. Testing that with `Path.exists()` is always False, which made every
        such stage a permanent cache miss.
        """
        return bool(self.artifacts) and all(_artifact_exists(p) for p in self.artifacts.values())


class RenderCache:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / MANIFEST
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "entries": {}}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"version": 1, "entries": {}}
        data.setdefault("entries", {})
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".partial")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def lookup(self, scope: str, key: str) -> CacheHit | None:
        entry = self._data["entries"].get(f"{scope}:{key}")
        if not entry:
            return None
        hit = CacheHit(key, entry["rendered_at"], entry.get("artifacts", {}))
        return hit if hit.is_present() else None

    def store(self, scope: str, key: str, artifacts: dict[str, str]) -> None:
        self._data["entries"][f"{scope}:{key}"] = {
            "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "artifacts": {k: str(v) for k, v in artifacts.items()},
        }
        self._save()

    def clear(self, scope: str | None = None) -> int:
        keys = [
            k for k in self._data["entries"]
            if scope is None or k.startswith(f"{scope}:")
        ]
        for key in keys:
            del self._data["entries"][key]
        self._save()
        return len(keys)

    def summary(self) -> list[dict]:
        out = []
        for composite, entry in sorted(self._data["entries"].items()):
            scope, _, key = composite.partition(":")
            out.append({
                "stage": scope,
                "key": key,
                "rendered_at": entry["rendered_at"],
                "artifacts": entry.get("artifacts", {}),
                "present": all(_artifact_exists(p)
                               for p in entry.get("artifacts", {}).values()),
            })
        return out
