"""Live reload: publishing a resolved scene for the Blender add-on. Host side.

The add-on runs inside Blender and therefore has no network and no host packages — the same
boundary the backend has always had. So it cannot download a texture or validate pydantic
models. This module does that work and writes a *resolved* scene the add-on can build directly.

The resolved file is the interface between the terminal and the viewport. I edit `scene.json`;
this republishes; the add-on notices and rebuilds in place. Nothing is rendered, nothing is
reopened, and the viewport stays exactly where it was put.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from blended.project import load, resolve_textures
from blended.stages import STAGE_ORDER, get as get_stage
from blended.verify.static import check

#: Sits beside the scene file. Gitignored along with the rest of `projects/`.
RESOLVED_SUFFIX = ".resolved.json"


def resolved_path(scene_file: Path) -> Path:
    scene_file = Path(scene_file)
    return scene_file.with_name(scene_file.stem + RESOLVED_SUFFIX)


@dataclass
class Publication:
    path: Path
    scene_name: str
    ok: bool
    errors: list[str]
    stage: str | None


def publish(scene_file: Path, *, stage: str | None = None,
            cache_root: Path | None = None, src_root: Path | None = None) -> Publication:
    """Resolve a scene and write it where the add-on can find it.

    Writing atomically matters: the add-on polls this file, and a half-written JSON would be
    read as corrupt at exactly the moment it changes.

    `src` is recorded in the file so the add-on can put `blended_backend` on its path without
    being configured. Self-describing beats a preferences panel nobody remembers to set.
    """
    scene_file = Path(scene_file)
    scene = load(scene_file)
    report = check(scene)

    ir = scene.model_dump(mode="json")
    if stage:
        ir = get_stage(stage).apply(ir)
    ir = resolve_textures(ir, Path(cache_root or ".cache"))

    src = Path(src_root) if src_root else Path(__file__).resolve().parent.parent
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scene_file": str(scene_file.resolve()),
        "scene": scene.name,
        "stage": stage,
        "src": str(src),
        "ok": report.ok,
        "errors": [d.message for d in report.errors],
        "ir": ir,
    }

    out = resolved_path(scene_file)
    tmp = out.with_suffix(".partial")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out)

    return Publication(out, scene.name, report.ok, payload["errors"], stage)


def watch(scene_file: Path, *, stage: str | None = None, interval: float = 0.5,
          cache_root: Path | None = None, on_publish=None) -> None:
    """Republish whenever the scene file changes. Blocks until interrupted.

    Polling rather than filesystem events: half a second is imperceptible next to a rebuild,
    and it avoids a dependency plus a pile of platform-specific behaviour for no real gain.

    A publish that fails Tier 1 is still written, carrying its errors — the add-on shows them
    rather than silently continuing to display a stale scene.
    """
    scene_file = Path(scene_file)
    last_seen = None
    while True:
        try:
            stamp = scene_file.stat().st_mtime_ns
        except OSError:
            time.sleep(interval)
            continue

        if stamp != last_seen:
            last_seen = stamp
            try:
                result = publish(scene_file, stage=stage, cache_root=cache_root)
            except Exception as exc:  # a bad edit must not kill the watcher
                if on_publish:
                    on_publish(None, exc)
            else:
                if on_publish:
                    on_publish(result, None)
        time.sleep(interval)


def stage_summary(scene_file: Path) -> list[dict]:
    """Stage states, for the add-on's panel."""
    from blended.approval import Ledger

    scene = load(scene_file)
    ir = scene.model_dump(mode="json")
    ledger = Ledger.for_scene(scene_file)
    return [
        {"stage": name, "status": ledger.state(scene.name, ir, get_stage(name)).status}
        for name in STAGE_ORDER
    ]
