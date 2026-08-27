"""Contact sheets. Runs inside Blender.

A grid of sampled frames in one image. Two uses: a human can take in a whole shot at a glance
instead of scrubbing, and Tier 3 (a vision model, ARCHITECTURE §8) can be handed one image
rather than hundreds.

Built with numpy against Blender's bundled image loader, so no external imaging dependency —
the host has neither Pillow nor ffmpeg available.
"""

from __future__ import annotations

import glob
import math
import os

import bpy
import numpy as np


def _load(path):
    """Read an image as float RGB, oriented top-down.

    Blender stores pixels bottom-up; a contact sheet assembled without flipping reads as an
    upside-down grid, which is confusing rather than wrong.
    """
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        if width == 0 or height == 0:
            return None
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
        return pixels[::-1, :, :3]
    finally:
        bpy.data.images.remove(image)


def build(pattern, output, *, columns=4, max_frames=16, cell_width=320, gap=4,
          background=0.08):
    """Tile frames matching `pattern` into one PNG. Returns a summary dict."""
    files = sorted(glob.glob(pattern))
    if not files:
        return {"error": f"no frames matched {pattern!r}"}

    # Evenly spaced across the shot rather than the first N — a contact sheet of the opening
    # two seconds tells you nothing about the shot.
    if len(files) > max_frames:
        step = len(files) / max_frames
        files = [files[int(i * step)] for i in range(max_frames)]

    tiles = [t for t in (_load(f) for f in files) if t is not None]
    if not tiles:
        return {"error": "no readable frames"}

    source_h, source_w, _ = tiles[0].shape
    cell_h = max(1, round(cell_width * source_h / source_w))
    rows = math.ceil(len(tiles) / columns)

    sheet = np.full(
        (rows * cell_h + (rows + 1) * gap, columns * cell_width + (columns + 1) * gap, 3),
        background, dtype=np.float32,
    )

    for index, tile in enumerate(tiles):
        # Nearest-neighbour downsample. A contact sheet is for reading composition and timing,
        # not for judging detail, and this keeps the module dependency-free.
        ys = (np.arange(cell_h) * source_h // cell_h).clip(0, source_h - 1)
        xs = (np.arange(cell_width) * source_w // cell_width).clip(0, source_w - 1)
        small = tile[ys][:, xs]

        row, col = divmod(index, columns)
        y = gap + row * (cell_h + gap)
        x = gap + col * (cell_width + gap)
        sheet[y:y + cell_h, x:x + cell_width] = small

    height, width, _ = sheet.shape
    out_image = bpy.data.images.new("contact_sheet", width=width, height=height, alpha=False)
    rgba = np.concatenate([sheet[::-1], np.ones((height, width, 1), np.float32)], axis=2)
    out_image.pixels = rgba.ravel()

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    out_image.filepath_raw = output
    out_image.file_format = "PNG"
    out_image.save()
    bpy.data.images.remove(out_image)

    # Deliberately not "frames": the generic render stats set that key from the scene's frame
    # range *after* build stats are merged, so a collision here silently reports the wrong
    # number with no error anywhere.
    return {"output": output, "tiles": len(tiles), "grid": [rows, columns],
            "size": [width, height]}
