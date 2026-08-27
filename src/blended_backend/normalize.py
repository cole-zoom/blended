"""The asset normalization contract. Runs inside Blender.

Every ingestion tier (ARCHITECTURE §5) converges here, and nothing downstream sees anything
else. Scene IR can say `"target": "logo"` while knowing nothing about SVGs, fonts, or GLBs.

The contract:
  * origin at the bounding-box centre
  * largest dimension scaled to `target_size` (default 1.0)
  * standing upright in the XZ plane, front facing -Y
  * transforms applied (location 0, rotation 0, scale 1)
  * materials stripped — appearance belongs to the style layer

Orientation matters more than it looks. SVGs import lying flat in XY extruded along +Z, which
would leave a camera orbiting the Z axis staring at the edge. Standing the logo up in XZ means
a Z-axis orbit sweeps across its face, which is what "pan around the logo" has to mean.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

#: Front of an upright asset faces -Y, so a camera at -Y sees it face-on at azimuth 0.
FRONT_AXIS = Vector((0.0, -1.0, 0.0))


def world_bounds(objs):
    """Axis-aligned world-space bounds over several objects.

    The `view_layer.update()` is load-bearing, not defensive. A freshly created object's
    `bound_box` is an uninitialised unit cube spanning (-1, -1, -1)..(1, 1, 1) until the
    dependency graph catches up. Reading it early silently returns geometry ~9x too large,
    and since every derived measurement (extrude depth, bevel, outline thickness, camera
    distance) scales off these bounds, the whole asset inflates with no error anywhere.
    """
    bpy.context.view_layer.update()
    corners = [obj.matrix_world @ Vector(c) for obj in objs for c in obj.bound_box]
    if not corners:
        raise ValueError("no geometry to measure")
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def apply_transforms(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def stand_upright(objs):
    """Rotate flat-in-XY geometry to upright-in-XZ, so a Z-axis orbit sweeps its face."""
    for obj in objs:
        obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    apply_transforms(objs)


def normalize(objs, target_size=1.0, upright=True):
    """Apply the full contract to a group of objects, treated as one asset.

    Returns a manifest dict — the thing Scene IR and the Tier-2 probes read.
    """
    objs = [o for o in objs if o.type == "MESH"]
    if not objs:
        raise ValueError("normalize() needs at least one mesh object")

    if upright:
        stand_upright(objs)

    lo, hi = world_bounds(objs)
    size = hi - lo
    largest = max(size.x, size.y, size.z)
    if largest <= 0:
        raise ValueError("asset has zero extent")
    scale = target_size / largest
    centre = (lo + hi) / 2.0

    # Move to origin and scale as a group, keeping relative placement intact.
    for obj in objs:
        obj.location = (obj.location - centre) * scale
        obj.scale = tuple(s * scale for s in obj.scale)
    apply_transforms(objs)

    for obj in objs:
        obj.data.materials.clear()

    lo, hi = world_bounds(objs)
    size = hi - lo
    return {
        "objects": [o.name for o in objs],
        "bounds": {"min": list(lo), "max": list(hi)},
        "size": list(size),
        "aspect": round(size.x / size.z, 4) if size.z else None,
        "depth_ratio": round(size.y / size.x, 5) if size.x else None,
        "target_size": target_size,
        "up_axis": "Z",
        "front_axis": list(FRONT_AXIS),
        "upright": upright,
    }
