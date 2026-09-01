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


def _targets(ctx, target):
    """Resolve a track target to objects: an asset id, or one named sub-object of one."""
    if target in ctx["asset_objects"]:
        return ctx["asset_objects"][target]
    if target in ctx["objects"]:
        return [ctx["objects"][target]]
    raise ValueError(
        f"unknown target {target!r} (assets: {sorted(ctx['asset_objects'])}, "
        f"objects: {sorted(ctx['objects'])})"
    )


def move(ctx, target, frames, params):
    """Translate a target between two offsets, in units of the asset's normalised width.

    Offsets rather than absolute positions: the asset has already been normalised and centred,
    so its resting place is 0. Writing `end_x: -0.404` says "move left by 40% of the wordmark's
    width", which stays correct if the artwork is re-traced at a different size.
    """
    objects = _targets(ctx, target)
    start_frame, end_frame = frames
    width = ctx.get("asset_width", 1.0)

    start = (params.get("start_x", 0.0) * width, params.get("start_z", 0.0) * width)
    end = (params.get("end_x", 0.0) * width, params.get("end_z", 0.0) * width)

    for obj in objects:
        base = obj.get("_rest_location")
        if base is None:
            base = list(obj.location)
            obj["_rest_location"] = base
        obj.location = (base[0] + start[0], base[1], base[2] + start[1])
        obj.keyframe_insert("location", frame=start_frame)
        obj.location = (base[0] + end[0], base[1], base[2] + end[1])
        obj.keyframe_insert("location", frame=end_frame)
        common.apply_easing(obj, params.get("easing", "ease_in_out"), "location")

    return {"start": start, "end": end, "objects": len(objects)}


def reveal(ctx, target, frames, params):
    """Sweep the wipe threshold across a target, revealing or hiding it as the edge passes.

    Drives the `wipe` value on the material built by `materials.unlit`. Everything left of the
    threshold is visible; the threshold is in the same normalised width units as `move`, so a
    reveal and a slide can be written against the same numbers.
    """
    objects = _targets(ctx, target)
    start_frame, end_frame = frames
    width = ctx.get("asset_width", 1.0)
    origin = ctx.get("asset_centre_x", 0.0)

    start = origin + params.get("start_x", -0.5) * width
    end = origin + params.get("end_x", 0.5) * width

    seen = set()
    for obj in objects:
        for slot in obj.data.materials:
            if slot is None or slot.name in seen:
                continue
            seen.add(slot.name)
            node = slot.node_tree.nodes.get("wipe_threshold")
            if node is None:
                raise ValueError(
                    f"material {slot.name!r} has no wipe threshold — object.reveal needs "
                    "material 'flat' with wipe enabled"
                )
            node.outputs[0].default_value = start
            node.outputs[0].keyframe_insert("default_value", frame=start_frame)
            node.outputs[0].default_value = end
            node.outputs[0].keyframe_insert("default_value", frame=end_frame)
            common.apply_easing(slot.node_tree, params.get("easing", "ease_in_out"))

    return {"start_x": round(start, 5), "end_x": round(end, 5), "materials": len(seen)}


def morph(ctx, target, frames, params):
    """Drive a shape key from one value to another.

    The shape itself was built at asset time (`blended_backend.morph`); this only animates how
    far along it is, so the expensive, deterministic part never runs per-frame.
    """
    objects = _targets(ctx, target)
    start_frame, end_frame = frames
    key_name = params.get("key", "morph")
    start = params.get("start", 0.0)
    end = params.get("end", 1.0)

    animated = 0
    for obj in objects:
        keys = obj.data.shape_keys
        if keys is None or key_name not in keys.key_blocks:
            raise ValueError(
                f"{obj.name}: no shape key {key_name!r} — set `morph_target` on the asset"
            )
        block = keys.key_blocks[key_name]
        block.value = start
        block.keyframe_insert("value", frame=start_frame)
        block.value = end
        block.keyframe_insert("value", frame=end_frame)
        common.apply_easing(keys, params.get("easing", "ease_in_out"))
        animated += 1

    return {"key": key_name, "start": start, "end": end, "objects": animated}


def tint(ctx, target, frames, params):
    """Fade a flat material's colour from one RGBA to another."""
    objects = _targets(ctx, target)
    start_frame, end_frame = frames
    start = tuple(params.get("start_color", [1.0, 1.0, 1.0, 1.0]))
    end = tuple(params.get("end_color", [1.0, 1.0, 1.0, 1.0]))

    seen = set()
    for obj in objects:
        for slot in obj.data.materials:
            if slot is None or slot.name in seen:
                continue
            seen.add(slot.name)
            node = slot.node_tree.nodes.get("flat_color")
            if node is None:
                raise ValueError(
                    f"material {slot.name!r} is not a flat material — object.tint needs one"
                )
            socket = node.inputs["Color"]
            socket.default_value = start
            socket.keyframe_insert("default_value", frame=start_frame)
            socket.default_value = end
            socket.keyframe_insert("default_value", frame=end_frame)
            common.apply_easing(slot.node_tree, params.get("easing", "ease_in_out"))

    return {"start": start, "end": end, "materials": len(seen)}


def fade(ctx, target, frames, params):
    """Animate a flat material's opacity.

    Distinct from `object.reveal`, which moves a hard edge across the object. A fade changes
    how *present* the whole thing is, which is what reads as materialising out of the air
    rather than being uncovered by a passing wipe.
    """
    objects = _targets(ctx, target)
    start_frame, end_frame = frames
    start = params.get("start", 0.0)
    end = params.get("end", 1.0)

    seen = set()
    for obj in objects:
        for slot in obj.data.materials:
            if slot is None or slot.name in seen:
                continue
            seen.add(slot.name)
            node = slot.node_tree.nodes.get("opacity")
            if node is None:
                raise ValueError(
                    f"material {slot.name!r} has no opacity — object.fade needs material "
                    "'flat' on an asset something fades"
                )
            node.outputs[0].default_value = start
            node.outputs[0].keyframe_insert("default_value", frame=start_frame)
            node.outputs[0].default_value = end
            node.outputs[0].keyframe_insert("default_value", frame=end_frame)
            common.apply_easing(slot.node_tree, params.get("easing", "ease_out_strong"))

    return {"start": start, "end": end, "materials": len(seen)}
