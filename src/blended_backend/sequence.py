"""Resumable frame-sequence rendering and encoding. Runs inside Blender.

Rendering straight to an mp4 means an interruption loses *everything*: the container needs its
trailing `moov` atom to be playable, so a run killed at 80% leaves an unopenable file. That cost
a full hour of Cycles time on this project.

A PNG sequence has neither problem. Completed frames survive on disk, a restart skips them, and
progress is inspectable while the render is still going. The frames are encoded to video
afterwards through Blender's sequencer, since the host has no ffmpeg binary.
"""

from __future__ import annotations

import glob
import os
import re
import time

import bpy

from blended_backend import render as render_mod

FRAME_RE = re.compile(r"(\d{4,})\.png$")


def frame_path(prefix, frame):
    return f"{prefix}{frame:04d}.png"


def existing_frames(prefix):
    """Frame numbers already on disk for this prefix.

    Zero-byte files count as absent: a render killed mid-write leaves a truncated PNG, and
    resuming onto one would bake the corruption into the final video.
    """
    found = set()
    for path in glob.glob(f"{prefix}*.png"):
        match = FRAME_RE.search(path)
        if match and os.path.getsize(path) > 0:
            found.add(int(match.group(1)))
    return found


def render_sequence(scene, cfg, *, resume=True, progress_every=10):
    """Render frames one at a time, skipping any already present.

    Frame-at-a-time rather than `animation=True` is what makes resume possible: Blender's
    animation render owns the loop and cannot be told to skip frames that already exist.
    """
    prefix = cfg["output"]
    os.makedirs(os.path.dirname(os.path.abspath(prefix)) or ".", exist_ok=True)

    start = int(cfg.get("frame_start", scene.frame_start))
    end = int(cfg.get("frame_end", scene.frame_end))
    step = max(1, int(cfg.get("frame_step", 1)))
    wanted = list(range(start, end + 1, step))

    done = existing_frames(prefix) if resume else set()
    todo = [f for f in wanted if f not in done]

    stats = {"wanted": len(wanted), "skipped": len(wanted) - len(todo), "rendered": 0}
    if not todo:
        stats["frames_path"] = prefix
        return stats

    began = time.perf_counter()
    for index, frame in enumerate(todo, 1):
        scene.frame_set(frame)
        scene.render.filepath = frame_path(prefix, frame)
        bpy.ops.render.render(write_still=True)
        stats["rendered"] += 1

        if index % progress_every == 0 or index == len(todo):
            elapsed = time.perf_counter() - began
            rate = elapsed / index
            # Printed rather than returned, so progress is visible in the log *during* the run
            # instead of only in the result once it finishes.
            print(f"blended: frame {frame} ({index}/{len(todo)}) "
                  f"{rate:.2f}s/frame eta {rate * (len(todo) - index) / 60:.1f}min",
                  flush=True)

    stats["seconds"] = round(time.perf_counter() - began, 2)
    stats["seconds_per_frame"] = round(stats["seconds"] / max(1, stats["rendered"]), 3)
    stats["frames_path"] = prefix
    return stats


def encode(prefix, output, *, fps=30, quality="HIGH"):
    """Encode a PNG sequence to H.264 through Blender's sequencer.

    A fresh scene rather than the render scene: the sequencer needs its own resolution and frame
    range, and mutating the scene that produced the frames would invalidate anything measured
    from it afterwards.
    """
    files = sorted(glob.glob(f"{prefix}*.png"))
    if not files:
        return {"error": f"no frames to encode at {prefix!r}"}

    scene = bpy.data.scenes.new("encode")
    try:
        first = bpy.data.images.load(files[0], check_existing=False)
        width, height = first.size
        bpy.data.images.remove(first)

        # Same even-dimension constraint, applied to whatever the frames actually are.
        scene.render.resolution_x = width // 2 * 2
        scene.render.resolution_y = height // 2 * 2
        scene.render.resolution_percentage = 100
        scene.render.fps = fps
        scene.render.fps_base = 1.0
        scene.frame_start = 1
        scene.frame_end = len(files)

        editor = scene.sequence_editor_create()
        strip = editor.strips.new_image(
            name="frames", filepath=files[0], channel=1, frame_start=1,
        )
        for path in files[1:]:
            strip.elements.append(os.path.basename(path))

        render_mod.apply_settings(scene, {
            "engine": "BLENDER_EEVEE", "resolution": [width, height], "fps": fps,
            "frame_start": 1, "frame_end": len(files), "media": "video", "output": output,
        })
        scene.render.ffmpeg.constant_rate_factor = quality

        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(animation=True)
    finally:
        bpy.data.scenes.remove(scene)

    return {"output": output, "frames": len(files), "size": [width, height]}
