"""Loading Scene IR and turning it into engine jobs. Host side."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from blended.ir.scene import SceneIR
from blended.errors import BlendedError

QUALITY = {
    "draft": {"resolution": (640, 360), "samples": 8, "step": 4},
    "preview": {"resolution": (1280, 720), "samples": 24, "step": 1},
    "final": {"resolution": (1920, 1080), "samples": 96, "step": 1},
}


class SceneLoadError(BlendedError):
    code = "SCENE_INVALID"


def load(path: Path) -> SceneIR:
    """Parse and structurally validate a scene file.

    Relative asset paths resolve against the scene file's directory, so a project is portable.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SceneLoadError(f"Could not read {path}: {exc}") from exc

    try:
        scene = SceneIR(**data)
    except ValidationError as exc:
        lines = [
            f"  {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()
        ]
        raise SceneLoadError(
            f"{path.name} does not match the Scene IR schema.",
            hint="\n".join(lines),
        ) from exc

    for asset in scene.assets:
        source = Path(asset.source)
        if not source.is_absolute():
            asset.source = str((path.parent / source).resolve())
    return scene


def resolve_textures(ir: dict, cache_root: Path) -> dict:
    """Download any texture sets the scene asks for and inject their local paths.

    Runs on the host, before the job is sent. The backend has no network and no third-party
    packages, so resolution has to happen on this side of the boundary — it only ever receives
    `texture_set: {role: path}`.
    """
    from blended.assets.textures import fetch, fetch_hdri

    world = ir.get("world") or {}
    if world.get("hdri"):
        world["hdri_path"] = fetch_hdri(
            world["hdri"], Path(cache_root),
            resolution=world.get("hdri_resolution", "2k"),
        )

    floor = (ir.get("environment") or {}).get("floor")
    if floor and floor.get("texture"):
        texture_set = fetch(
            floor["texture"],
            Path(cache_root),
            resolution=floor.get("texture_resolution", "2k"),
        )
        floor["texture_set"] = {role: texture_set.path(role) for role in texture_set.maps}
    return ir


def make_stage_job(scene: SceneIR, stage, *, out_dir: Path,
                   cache_root: Path | None = None) -> dict:
    """Build an engine job for one pipeline stage.

    The stage's suppressions are applied to a *copy* of the IR, so what gets rendered is a view
    of the scene rather than a mutation of it. The scene file is never rewritten to render a
    stage — a blocking pass must not be able to clay your materials permanently.
    """
    out_dir = Path(out_dir).resolve()
    ir = stage.apply(scene.model_dump(mode="json"))
    ir = resolve_textures(ir, Path(cache_root or ".cache"))

    width, height = stage.resolution
    suffix = "mp4" if stage.media == "video" else ""
    stem = f"{scene.name}_{stage.name}"
    output = out_dir / (f"{stem}.{suffix}" if suffix else f"{stem}_")

    return {
        "blend_out": str(out_dir / f"{stem}.blend"),
        "scene": {"kind": "scene_ir", "ir": ir},
        "render": {
            "engine": "CYCLES" if stage.engine == "cycles" else "BLENDER_EEVEE",
            "device": "GPU",
            "resolution": [width, height],
            "fps": scene.timeline.fps,
            "frame_start": 1,
            "frame_end": scene.timeline.frames,
            "frame_step": stage.frame_step,
            "samples": stage.samples,
            "media": stage.media,
            "output": str(output),
        },
        "probes": list(stage.probes),
    }


def make_job(scene: SceneIR, *, quality: str, out_dir: Path, media: str = "video",
             cache_root: Path | None = None, engine: str = "eevee") -> dict:
    """Build an engine job from a validated scene."""
    settings = QUALITY[quality]
    out_dir = Path(out_dir).resolve()
    width, height = settings["resolution"]
    suffix = "mp4" if media == "video" else ""
    # The engine belongs in the filename. Without it an EEVEE preview silently overwrites a
    # Cycles render of the same scene and quality — which is how an 83-minute render got
    # clobbered by a 7-minute one.
    stem = f"{scene.name}_{quality}_{engine}"
    output = out_dir / (f"{stem}.{suffix}" if suffix else f"{stem}_")

    ir = scene.model_dump(mode="json")
    ir = resolve_textures(ir, Path(cache_root or ".cache"))

    return {
        "blend_out": str(out_dir / f"{scene.name}.blend"),
        "scene": {"kind": "scene_ir", "ir": ir},
        "render": {
            "engine": "BLENDER_EEVEE",
            "resolution": [width, height],
            "fps": scene.timeline.fps,
            "frame_start": 1,
            "frame_end": scene.timeline.frames,
            "frame_step": settings["step"],
            "samples": settings["samples"],
            "media": media,
            "output": str(output),
        },
    }
