"""Entrypoint executed by Blender: `blender -b --python __main__.py -- --job J --result R`.

Runs inside Blender's bundled Python. stdlib + bpy only — no host imports (CLAUDE.md).

The single most important property of this file: it must ALWAYS write a result file, including
when everything goes wrong. The host treats a missing result as a crash, so an unhandled
exception here degrades a precise diagnostic into "Blender died, good luck".
"""

from __future__ import annotations

import json
import os
import sys
import time

# Blender executes this as a loose script, not as a package member, so `blended_backend` is not
# importable yet. Put its parent (src/) on the path before touching sibling modules.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blended_backend import result as result_mod  # noqa: E402


def parse_args(argv):
    """Blender swallows its own flags; ours come after the `--` separator."""
    args = argv[argv.index("--") + 1 :] if "--" in argv else []
    parsed = {}
    for i in range(0, len(args) - 1, 2):
        if args[i].startswith("--"):
            parsed[args[i][2:]] = args[i + 1]
    return parsed


def run(job, job_id):
    import bpy

    from blended_backend import render as render_mod
    from blended_backend import scene as scene_mod

    stats = {}
    artifacts = {}

    t0 = time.perf_counter()
    scene_mod.reset()
    build_stats = scene_mod.build(job.get("scene") or {})
    stats["build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    stats.update(build_stats)

    scene = bpy.context.scene
    render_cfg = job.get("render") or {}
    render_mod.apply_settings(scene, render_cfg)

    # Save the .blend BEFORE rendering: if the render fails, you still get an inspectable file,
    # which is usually exactly what you need to see why.
    if blend_out := job.get("blend_out"):
        artifacts["blend"] = render_mod.save_blend(blend_out)

    if render_cfg.get("output"):
        t1 = time.perf_counter()
        output = render_mod.render_animation(scene, render_cfg)
        stats["render_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        key = "video" if render_cfg.get("media", "video") == "video" else "stills"
        artifacts[key] = output

    stats["frames"] = scene.frame_end - scene.frame_start + 1
    stats["engine"] = scene.render.engine
    if scene.render.engine == "CYCLES":
        stats["cycles_device"] = scene.cycles.device
        stats["cycles_backend"] = render_cfg.get("_cycles_device")
        stats["cycles_samples"] = scene.cycles.samples
    stats["resolution"] = [scene.render.resolution_x, scene.render.resolution_y]
    stats["fps"] = scene.render.fps
    return artifacts, stats


def main():
    args = parse_args(sys.argv)
    result_path = args.get("result")
    job_path = args.get("job")
    job_id = "unknown"

    if not result_path:
        print("blended_backend: --result is required", file=sys.stderr)
        sys.exit(2)

    blender_version = None
    try:
        import bpy

        blender_version = bpy.app.version_string
    except Exception:  # pragma: no cover - bpy is always present under Blender
        pass

    try:
        with open(job_path) as fh:
            job = json.load(fh)
        job_id = job.get("job_id", "unknown")
        artifacts, stats = run(job, job_id)
    except Exception as exc:
        result_mod.write(
            result_path,
            result_mod.failure(job_id, exc, blender_version=blender_version),
        )
        # Exit non-zero so the failure is visible even if the result file is unreadable.
        # Blender otherwise exits 0 on an uncaught Python exception (CLAUDE.md).
        sys.exit(1)

    result_mod.write(
        result_path,
        result_mod.success(
            job_id,
            blender_version=blender_version,
            artifacts=artifacts,
            stats=stats,
        ),
    )


if __name__ == "__main__":
    main()
