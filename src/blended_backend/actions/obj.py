"""Object actions. Runs inside Blender."""

from __future__ import annotations

import math

from blended_backend.actions import common

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def spin(ctx, target, frames, params):
    """Rotate an asset about one axis."""
    objects = ctx["asset_objects"][target]
    start_frame, end_frame = frames
    axis = _AXIS_INDEX[params.get("axis", "z")]
    radians = params.get("turns", 1.0) * 2.0 * math.pi

    for obj in objects:
        rotation = list(obj.rotation_euler)
        obj.keyframe_insert("rotation_euler", index=axis, frame=start_frame)
        rotation[axis] += radians
        obj.rotation_euler = rotation
        obj.keyframe_insert("rotation_euler", index=axis, frame=end_frame)
        common.apply_easing(obj, params.get("easing", "linear"), "rotation_euler")

    return {"turns": params.get("turns", 1.0), "axis": params.get("axis", "z")}


def hold(ctx, target, frames, params):
    """Occupy a span without animating anything.

    Deliberately a no-op. It exists so that "nothing happens here" is stated in the IR rather
    than inferred from a gap, which keeps the coverage check meaningful.
    """
    return {"frames": [frames[0], frames[1]]}
