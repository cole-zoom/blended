"""Temporal flicker measurement. Runs inside Blender.

Frame-to-frame difference is the obvious metric and the wrong one: a moving camera changes every
pixel legitimately, so the number is dominated by the shot rather than by any defect.

Flicker is distinguished by being *non-smooth in time*. Real motion has a small second temporal
derivative; popping specular highlights have a large one. So this measures

    |f(n+1) - 2·f(n) + f(n-1)|

which cancels smooth motion and leaves the artefacts.

Built for a real problem on this project: a wet floor shimmered under an orbiting camera, and
the first two diagnoses were wrong precisely because the metric could not separate flicker from
camera movement.
"""

from __future__ import annotations

import glob

import bpy
import numpy as np


def _load(path, downsample=1):
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        if width == 0 or height == 0:
            return None
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)[:, :, :3]
    finally:
        bpy.data.images.remove(image)
    if downsample > 1:
        h = height // downsample * downsample
        w = width // downsample * downsample
        pixels = pixels[:h, :w].reshape(
            h // downsample, downsample, w // downsample, downsample, 3
        ).mean(axis=(1, 3))
    return pixels


def measure(pattern, *, limit=24, downsample=1):
    """Measure flicker across frames matching `pattern`.

    `p99.9` matters more than the mean: flicker lives in a small number of very bright pixels,
    and an average over a mostly-static frame buries it.
    """
    files = sorted(glob.glob(pattern))
    if len(files) < 3:
        return {"error": f"need at least 3 frames, found {len(files)}"}

    if len(files) > limit:
        step = len(files) / limit
        files = [files[int(i * step)] for i in range(limit)]

    frames = [f for f in (_load(p, downsample) for p in files) if f is not None]
    if len(frames) < 3:
        return {"error": "fewer than 3 readable frames"}

    means, tails = [], []
    for i in range(1, len(frames) - 1):
        accel = np.abs(frames[i + 1] - 2.0 * frames[i] + frames[i - 1])
        means.append(float(accel.mean()))
        tails.append(float(np.percentile(accel, 99.9)))

    return {
        "frames": len(frames),
        "mean": round(sum(means) / len(means), 8),
        "p99_9": round(sum(tails) / len(tails), 6),
        "worst_p99_9": round(max(tails), 6),
    }
