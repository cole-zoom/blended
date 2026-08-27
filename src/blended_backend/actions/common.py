"""Shared helpers for action implementations. Runs inside Blender."""

from __future__ import annotations

from blended_backend import fcurves

#: Named easings mapped onto Blender's interpolation + easing pair.
#:
#: Blender defaults new keyframes to BEZIER with automatic handles, which eases in *and* out of
#: every key. That is wrong for anything meant to run at a constant rate, so every action sets
#: this explicitly rather than inheriting the default.
EASINGS = {
    "linear": ("LINEAR", "AUTO"),
    "ease_in": ("SINE", "EASE_IN"),
    "ease_out": ("SINE", "EASE_OUT"),
    "ease_in_out": ("SINE", "EASE_IN_OUT"),
}


def apply_easing(obj, easing, data_path=None):
    """Set interpolation on an object's keyframes, optionally limited to one data path."""
    interpolation, ease = EASINGS.get(easing, EASINGS["ease_in_out"])
    count = 0
    for fcurve in fcurves.iter_fcurves(obj):
        if data_path is not None and fcurve.data_path != data_path:
            continue
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = interpolation
            keyframe.easing = ease
            count += 1
    return count


def apply_easing_to_data(datablock, easing, data_path=None):
    """Easing for animation living on a datablock (light energy) rather than an object."""
    return apply_easing(datablock, easing, data_path)
