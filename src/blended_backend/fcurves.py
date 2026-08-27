"""F-curve access across Blender's Action API split. Runs inside Blender.

Blender 4.4 replaced flat `action.fcurves` with *slotted actions*:

    action.layers[N].strips[M].channelbag(slot).fcurves

`action.fcurves` no longer exists in 5.x — reaching for it raises AttributeError. Every f-curve
read or write goes through here so that fact lives in exactly one place.

Phase 3's Tier-2 probes depend on this too: verifying "light energy is monotonically increasing"
means sampling the energy f-curve.
"""

from __future__ import annotations


def iter_fcurves(obj):
    """Yield every f-curve animating `obj`, on either Action API."""
    anim = getattr(obj, "animation_data", None)
    action = getattr(anim, "action", None)
    if action is None:
        return

    # Legacy flat actions (Blender < 4.4).
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield from legacy
        return

    slot = getattr(anim, "action_slot", None)
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != "KEYFRAME":
                continue
            bags = []
            if slot is not None:
                bags.append(strip.channelbag(slot))
            else:
                bags.extend(strip.channelbags)
            for bag in bags:
                if bag is not None:
                    yield from bag.fcurves


def find_fcurve(obj, data_path, index=None):
    """Return the f-curve for `data_path` (optionally a specific array index), or None."""
    for fcurve in iter_fcurves(obj):
        if fcurve.data_path != data_path:
            continue
        if index is None or fcurve.array_index == index:
            return fcurve
    return None


def set_interpolation(obj, mode="LINEAR", data_path=None):
    """Set keyframe interpolation on some or all of an object's curves.

    Blender defaults new keyframes to BEZIER, which eases in and out. That is wrong for anything
    meant to run at a constant rate — a continuous spin, or a steady camera orbit.
    """
    count = 0
    for fcurve in iter_fcurves(obj):
        if data_path is not None and fcurve.data_path != data_path:
            continue
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = mode
            count += 1
    return count


def sample(obj, data_path, frames, index=0):
    """Evaluate an f-curve at each frame. The basis of Tier-2 probes."""
    fcurve = find_fcurve(obj, data_path, index)
    if fcurve is None:
        return None
    return [fcurve.evaluate(f) for f in frames]
