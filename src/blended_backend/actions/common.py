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
    # SINE is a gentle curve — over a short move it is very close to linear and reads as
    # "no easing at all". These are the same shapes with real acceleration in them.
    # QUART is the workhorse; EXPO is extreme, and settles so slowly it reads as drifting,
    # which is what a soft, dreamy move wants.
    "ease_in_strong": ("QUART", "EASE_IN"),
    "ease_out_strong": ("QUART", "EASE_OUT"),
    "ease_in_out_strong": ("QUART", "EASE_IN_OUT"),
    "drift_in": ("EXPO", "EASE_IN"),
    "drift_out": ("EXPO", "EASE_OUT"),
    "drift_in_out": ("EXPO", "EASE_IN_OUT"),
    # Overshoots the target and settles back. The cheapest way to stop motion reading as
    # machine-generated: nothing with mass stops dead on its mark.
    "overshoot_out": ("BACK", "EASE_OUT"),
    "overshoot_in_out": ("BACK", "EASE_IN_OUT"),
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
