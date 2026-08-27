"""Scene construction. Runs inside Blender.

Phase 0 only knows how to build one hardcoded demo scene. Phase 2 replaces `build()` with a
real Scene IR interpreter driven by the animation library — the dispatch seam is already here.
"""

from __future__ import annotations

import math

import bpy

from blended_backend import fcurves


def reset():
    """Start from a genuinely empty scene.

    `--factory-startup` still ships the default cube/camera/light, and orphaned datablocks
    survive object deletion, so purge them too — otherwise repeated builds leak.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for _ in range(3):
        bpy.ops.outliner.orphans_purge(do_recursive=True)


def build(spec):
    """Dispatch on scene kind. Returns a dict of stats about what was built."""
    kind = spec.get("kind", "demo_cube")
    if kind == "demo_cube":
        return _build_demo_cube(spec)
    if kind == "logo_still":
        return _build_logo_still(spec)
    if kind == "contact_sheet":
        from blended_backend import contact_sheet

        return contact_sheet.build(
            spec["pattern"], spec["output"],
            columns=spec.get("columns", 4),
            max_frames=spec.get("max_frames", 16),
            cell_width=spec.get("cell_width", 320),
        )
    if kind == "scene_ir":
        from blended_backend import build as build_mod

        return build_mod.build(spec["ir"])
    raise ValueError(
        f"Unknown scene kind {kind!r} (known: 'demo_cube', 'logo_still', 'scene_ir')"
    )


def _build_logo_still(spec):
    """Phase 1: an SVG logo as beveled 3D geometry with a black outline, lit and framed.

    Returns a manifest describing what was built — including the topology numbers the
    Tier-2 probes in goal/acceptance.md assert on.
    """
    from blended_backend import normalize as norm
    from blended_backend import staging, styles
    from blended_backend.ingest import svg as svg_ingest

    source = spec["source"]
    names = spec.get("names", ["logo_marks", "logo_text"])

    curves = svg_ingest.import_svg(source)
    if len(curves) > 1:
        raise ValueError(f"expected 1 curve object from {source!r}, got {len(curves)}")
    curve = curves[0]

    split_x, gap = svg_ingest.find_x_gap(curve)
    if split_x is None:
        parts = [curve]
        curve.name = names[0]
    else:
        parts = svg_ingest.split_at_x(curve, split_x, names)
        bpy.data.objects.remove(curve, do_unlink=True)

    # Geometry parameters are expressed as fractions of the asset's width so they are
    # independent of whatever scale the source file happened to import at. The SVG lands at
    # ~0.24 Blender units wide; a GLB might land at 100. The IR should not have to care.
    width = _asset_width(parts)
    extrude = spec.get("extrude", 0.05) * width
    bevel = spec.get("bevel", 0.006) * width
    resolution = spec.get("resolution", 12)
    thickness = spec.get("outline_thickness", 0.004) * width
    # `lineart` is the default and the only technique that holds up under an orbiting camera.
    # `hull` and `offset` build the outline from geometry; they are kept for asset types Line Art
    # handles poorly, but both suffer parallax off-axis. See styles.add_lineart.
    mode = spec.get("outline_mode", "lineart")
    geometry_outline = mode in ("hull", "offset")

    # Geometry outlines branch off a copy of the *curve*, before the logo gets its bevel.
    # `offset` grows the copy in-plane via the curve's own offset; `hull` inflates it in 3D.
    # Either way the source is clean, unbeveled geometry.
    outlines = []
    if thickness > 0 and geometry_outline:
        for obj in parts:
            obj.data.extrude = extrude
            # In offset mode the logo's bevel already flares its silhouette outward by `bevel`,
            # so an offset of that magnitude would be exactly covered by it. Adding bevel makes
            # `outline_thickness` mean the width you actually SEE, as the IR promises.
            grow = (thickness + bevel) if mode == "offset" else 0.0
            outlines.append(styles.outline_curve(obj, grow))

    for obj in parts:
        svg_ingest.solidify_curve(obj, depth=extrude, bevel=bevel, resolution=resolution)

    # Both modes rely on the same trick: keep the outline strictly *shallower* than the logo, so
    # it can only ever show around the silhouette and never z-fights or occludes the face. Curve
    # extrusion is symmetric about the curve plane, so a smaller depth insets it front and back.
    #
    # (Offsetting the object's location instead does not work: stand_upright() rotates about the
    # object origin, so a local-Z nudge survives as a world-Z shift rather than becoming depth.)
    #
    # THE DEPTH GAP IS THE WHOLE BALLGAME, and it wants to be as small as z-fighting allows.
    #
    # The outline is a separate copy sitting behind the logo, so any depth gap between them
    # becomes parallax as soon as the camera leaves head-on: the further copy slides sideways on
    # screen, and a rim that looked perfectly uniform face-on turns into a one-sided drop shadow.
    # The displacement grows as gap * tan(off-axis angle), so with a camera orbiting for 480
    # frames the gap has to be tiny, not merely "smallish".
    #
    # Target: outline back face lands just inside the logo's, by a hair of the total depth.
    # In hull mode the inflation that follows adds `thickness` in depth too, so subtract it here.
    logo_half_depth = extrude + bevel
    epsilon = logo_half_depth * 0.04
    if mode == "hull":
        outline_depth = max(extrude * 0.1, logo_half_depth - thickness - epsilon)
    else:
        outline_depth = logo_half_depth - epsilon
    for obj in outlines:
        svg_ingest.solidify_curve(obj, depth=outline_depth, bevel=0.0, resolution=resolution)

    topology = {obj.name: svg_ingest.mesh_report(obj) for obj in parts}
    # Normalize the whole asset together so the outline keeps its relative placement.
    manifest = norm.normalize(parts + outlines, target_size=spec.get("target_size", 1.0))

    # Inflate AFTER normalization, deliberately. normalize() bakes its scale into vertex data via
    # transform_apply, which does not rescale modifier parameters — a strength set beforehand
    # silently shrinks by the normalization factor (~4x here). Set post-normalize and the asset
    # is exactly `target_size` wide, so thickness is a plain fraction of the logo's width.
    if mode == "hull":
        target_size = spec.get("target_size", 1.0)
        for obj in outlines:
            styles.add_hull_modifier(obj, spec.get("outline_thickness", 0.004) * target_size)
    manifest["objects"] = [o.name for o in parts]
    manifest["outlines"] = [o.name for o in outlines]

    styles.paint(
        parts,
        styles.surface_material(
            "logo_surface",
            tuple(spec.get("base_color", [0.92, 0.92, 0.94, 1.0])),
            spec.get("roughness", 0.35),
            spec.get("metallic", 0.0),
        ),
    )
    if outlines:
        # Repaint after normalize(), which strips materials as part of the asset contract.
        # No backface culling: occlusion comes from keeping the outline shallower than the logo,
        # and culling would discard the very rim we want to see.
        styles.paint(
            outlines,
            styles.unlit_material(
                "logo_outline",
                tuple(spec.get("outline_color", [0.0, 0.0, 0.0, 1.0])),
                backface_culling=False,
            ),
        )

    camera, target, distance = staging.add_camera(
        parts,
        lens=spec.get("lens", 50.0),
        azimuth=spec.get("azimuth", 0.0),
        elevation=spec.get("elevation", 0.0),
        margin=spec.get("margin", 1.15),
    )
    staging.add_key_light(target, energy=spec.get("light_energy", 200.0))
    if spec.get("rim_light", True):
        staging.add_rim_light(target)
    staging.set_world(tuple(spec.get("world_color", [0.02, 0.02, 0.025, 1.0])))

    # Line Art traces whatever geometry is in the scene, so it goes last — after the logo exists
    # and after the camera, which is what it projects against.
    lineart = None
    if thickness > 0 and mode == "lineart":
        lineart = styles.add_lineart(
            spec.get("outline_thickness", 0.004) * spec.get("target_size", 1.0),
            tuple(spec.get("outline_color", [0.0, 0.0, 0.0, 1.0])),
            creases=spec.get("outline_creases", False),
        )
        manifest["outlines"] = [lineart.name]

    return {
        "asset": manifest,
        "topology": topology,
        "split_x": split_x,
        "split_gap": gap,
        "outline": _outline_report(mode, parts, outlines, lineart),
        "camera_distance": round(distance, 4),
        "objects": len(bpy.context.scene.collection.all_objects),
    }


def _outline_report(mode, parts, outlines, lineart):
    """Probe the outline, whichever technique produced it.

    Line Art can silently produce nothing (wrong source, camera outside the scene, all edges
    filtered out) and the render simply comes back without an outline, so 'strokes exist' is the
    check that matters. For the geometry techniques the check is a bounds relationship instead.
    """
    report = {"mode": mode}
    if mode == "lineart":
        if lineart is None:
            return {**report, "present": False}
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = lineart.evaluated_get(depsgraph)
        points = 0
        for layer in evaluated.data.layers:
            frame = layer.current_frame()
            if frame is not None:
                points += len(frame.drawing.attributes["position"].data)
        return {**report, "present": points > 0, "stroke_points": points}
    return {**report, **_outline_bounds_report(parts, outlines)}


def _outline_bounds_report(parts, outlines):
    """Verify the outline geometry relationship that makes the effect work at all.

    The outline must be *wider* than the logo in the logo's plane (X and Z, once upright) so it
    shows around the silhouette, and *shallower* in depth (Y) so it can never occlude the front
    face or z-fight with it. Both halves have been broken during development in ways that were
    only visible by rendering and squinting, so they are measured here instead.

    Bounds come from the evaluated depsgraph: modifiers do not affect `bound_box` otherwise.
    """
    from blended_backend.normalize import world_bounds

    if not outlines:
        return {"wider": None, "shallower": None, "margin": None}

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = [o.evaluated_get(depsgraph) for o in outlines]

    logo_lo, logo_hi = world_bounds(parts)
    out_lo, out_hi = world_bounds(evaluated)

    margin_x = min(logo_lo.x - out_lo.x, out_hi.x - logo_hi.x)
    margin_z = min(logo_lo.z - out_lo.z, out_hi.z - logo_hi.z)
    depth_gap = (logo_hi.y - logo_lo.y) - (out_hi.y - out_lo.y)

    return {
        "wider": margin_x > 0 and margin_z > 0,
        "shallower": depth_gap > 0,
        "margin_x": round(margin_x, 6),
        "margin_z": round(margin_z, 6),
        "depth_gap": round(depth_gap, 6),
    }


def _build_demo_cube(spec):
    """A spinning cube, a key light, and a camera. The Phase 0 smoke test.

    Deliberately exercises every mechanism the real pipeline needs: object creation, material
    assignment, keyframed animation with explicit interpolation, camera targeting, and lighting.
    """
    scene = bpy.context.scene
    turns = float(spec.get("turns", 1.0))
    frame_start = int(spec.get("frame_start", 1))
    frame_end = int(spec.get("frame_end", 60))

    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.active_object
    cube.name = "demo_cube"

    material = bpy.data.materials.new("demo_cube_mat")
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.85, 0.85, 0.88, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.35
    bsdf.inputs["Metallic"].default_value = 0.1
    cube.data.materials.append(material)

    # Keyframe a full-turn spin. Linear interpolation so the rotation rate is constant —
    # Blender's default Bezier easing would ease in and out of the loop.
    cube.rotation_euler = (0.0, 0.0, 0.0)
    cube.keyframe_insert("rotation_euler", frame=frame_start)
    cube.rotation_euler = (0.0, 0.0, turns * 2.0 * math.pi)
    cube.keyframe_insert("rotation_euler", frame=frame_end)
    fcurves.set_interpolation(cube, "LINEAR")

    light_data = bpy.data.lights.new("key_light", type="AREA")
    light_data.energy = 400.0
    light_data.size = 5.0
    light = bpy.data.objects.new("key_light", light_data)
    light.location = (4.0, -4.0, 6.0)
    _aim_at(light, (0.0, 0.0, 0.0))
    scene.collection.objects.link(light)

    cam_data = bpy.data.cameras.new("camera")
    cam_data.lens = 50.0
    camera = bpy.data.objects.new("camera", cam_data)
    camera.location = (6.0, -6.0, 4.0)
    _aim_at(camera, (0.0, 0.0, 0.0))
    scene.collection.objects.link(camera)
    scene.camera = camera

    scene.world = bpy.data.worlds.new("world")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.02, 0.02, 0.03, 1.0)

    return {
        "objects": len(scene.collection.all_objects),
        "cube": cube.name,
        "camera": camera.name,
        "turns": turns,
    }


def _asset_width(objs):
    """Total X extent across objects, in their current (pre-normalization) units."""
    from blended_backend.normalize import world_bounds

    lo, hi = world_bounds(objs)
    return hi.x - lo.x


def _aim_at(obj, target):
    """Point an object's -Z axis at a world-space target.

    Blender lights and cameras both look down -Z, so this works for either.
    """
    from mathutils import Vector

    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
