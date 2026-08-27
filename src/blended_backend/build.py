"""Scene IR → Blender. Runs inside Blender.

The compiler backend: takes a validated Scene IR and produces a scene. It assumes Tier 1 has
already passed, so it fails loudly rather than defensively — anything wrong here is a bug in
the checker, and swallowing it would hide that.
"""

from __future__ import annotations

import bpy

from blended_backend import materials, normalize as norm
from blended_backend import staging, styles
from blended_backend.actions import IMPLEMENTATIONS
from blended_backend.ingest import svg as svg_ingest


def seconds_to_frames(seconds, fps):
    """The single quantization boundary (ARCHITECTURE §3).

    Frame 1 is t=0, matching Blender's 1-based timeline. Every other time in the system is
    integer frames from here on — no float seconds survive past this function.
    """
    return 1 + round(seconds * fps)


def build(ir):
    """Build a scene from Scene IR. Returns a stats dict."""
    scene = bpy.context.scene
    fps = ir["timeline"]["fps"]
    duration = ir["timeline"]["duration"]
    frame_count = round(duration * fps)

    scene.render.fps = fps
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = frame_count

    ctx = {"objects": {}, "asset_objects": {}, "margin": ir.get("camera", {}).get("margin", 1.15)}
    stats = {"assets": {}, "tracks": []}

    subject = []
    for asset in ir["assets"]:
        objects, asset_stats = _build_asset(asset)
        ctx["asset_objects"][asset["id"]] = objects
        for obj in objects:
            ctx["objects"][obj.name] = obj
        subject.extend(objects)
        stats["assets"][asset["id"]] = asset_stats
    ctx["subject_objects"] = subject

    camera_spec = ir.get("camera", {})
    camera, target, distance = staging.add_camera(
        subject,
        lens=camera_spec.get("lens", 50.0),
        margin=camera_spec.get("margin", 1.15),
        clip_start=camera_spec.get("clip_start", 0.01),
        clip_end=camera_spec.get("clip_end", 1000.0),
        name=camera_spec.get("id", "camera"),
    )
    ctx["camera"] = camera
    ctx["objects"][camera_spec.get("id", "camera")] = camera

    dof = camera_spec.get("dof") or {}
    if dof.get("enabled"):
        staging.enable_depth_of_field(
            camera, subject,
            f_stop=dof.get("f_stop", 4.0),
            focus_offset=dof.get("focus_offset", 0.0),
        )
        stats["dof"] = {"f_stop": dof.get("f_stop", 4.0)}
    if camera_spec.get("motion_blur"):
        staging.enable_motion_blur(camera_spec.get("shutter", 0.5))
        stats["motion_blur"] = True

    for light_spec in ir.get("lights", []):
        kind = light_spec.get("type", "area")
        if kind == "spot":
            light = staging.add_spot(
                target,
                energy=light_spec.get("energy", 800.0),
                azimuth=light_spec.get("azimuth", -50.0),
                elevation=light_spec.get("elevation", 38.0),
                distance=light_spec.get("distance", 5.0),
                spot_size=light_spec.get("spot_size", 45.0),
                spot_blend=light_spec.get("spot_blend", 0.25),
                radius=light_spec.get("radius", 0.1),
                color=tuple(light_spec.get("color", [1.0, 1.0, 1.0])),
                name=light_spec["id"],
            )
            ctx["objects"][light_spec["id"]] = light
            continue
        if kind == "sun":
            # Note the absent `distance`/`size`: a directional light has neither. Passing them
            # would imply an effect they cannot have.
            light = staging.add_sun(
                target,
                energy=light_spec.get("energy", 3.0),
                azimuth=light_spec.get("azimuth", -35.0),
                elevation=light_spec.get("elevation", 25.0),
                angle=light_spec.get("angle", staging.SUN_ANGLE_DEGREES),
                color=tuple(light_spec.get("color", [1.0, 1.0, 1.0])),
                name=light_spec["id"],
            )
        else:
            light = staging.add_key_light(
                target,
                energy=light_spec.get("energy", 200.0),
                azimuth=light_spec.get("azimuth", -35.0),
                elevation=light_spec.get("elevation", 35.0),
                distance=light_spec.get("distance", 4.0),
                size=light_spec.get("size", 3.0),
                color=tuple(light_spec.get("color", [1.0, 1.0, 1.0])),
                name=light_spec["id"],
            )
        ctx["objects"][light_spec["id"]] = light

    world = ir.get("world", {})
    if world.get("hdri_path"):
        # Resolved by the host; the backend never fetches. Lighting and reflections come from
        # the HDRI while the camera still sees `color` (staging.set_environment_hdri).
        staging.set_environment_hdri(
            world["hdri_path"],
            strength=world.get("hdri_strength", 1.0),
            visible_to_camera=world.get("hdri_visible", False),
            background=tuple(world.get("color", [0.0, 0.0, 0.0, 1.0])),
            rotation=world.get("hdri_rotation", 0.0),
        )
        stats["hdri"] = world.get("hdri")
    else:
        staging.set_world(tuple(world.get("color", [0.02, 0.02, 0.025, 1.0])),
                          world.get("strength", 1.0))

    environment = ir.get("environment") or {}
    floor_spec = environment.get("floor")
    if floor_spec and floor_spec.get("enabled", True):
        floor = staging.add_floor(
            subject,
            size=floor_spec.get("size", 40.0),
            material=materials.build(
                floor_spec.get("material", "stone"),
                # Resolved by the host before the job was sent; absent means procedural.
                texture_set=floor_spec.get("texture_set"),
                scale=floor_spec.get("scale", 6.0),
                wetness=floor_spec.get("wetness", 0.0),
                ripple_strength=floor_spec.get("ripples", 0.0),
                wet_roughness=floor_spec.get("wet_roughness", 0.12),
                bump=floor_spec.get("bump", 1.0),
                wet_flatten=floor_spec.get("wet_flatten", 0.25),
                ripple_scale=floor_spec.get("ripple_scale", 28.0),
                ripple_speed=floor_spec.get("ripple_speed", 6.0),
                ripple_detail=floor_spec.get("ripple_detail", 6.0),
                frames=(1, frame_count),
            ),
            offset=floor_spec.get("offset", 0.0),
        )
        stats["floor"] = {"z": round(floor.location.z, 5), "size": floor_spec.get("size", 40.0)}

    volumetrics = environment.get("volumetrics") or {}
    if volumetrics.get("enabled"):
        box = staging.add_atmosphere(
            subject,
            density=volumetrics.get("density", 0.12),
            size=volumetrics.get("size"),
            anisotropy=volumetrics.get("anisotropy", 0.4),
            samples=volumetrics.get("samples", 96),
        )
        stats["volumetrics"] = {"density": volumetrics.get("density", 0.12),
                                "box": round(box.dimensions.x, 3)}

    # Outlines go on after everything else: Line Art traces the scene as it finds it.
    for asset in ir["assets"]:
        outline = asset.get("outline", {})
        if outline.get("mode") == "lineart" and outline.get("thickness", 0) > 0:
            styles.add_lineart(
                outline["thickness"] * asset.get("target_size", 1.0),
                tuple(outline.get("color", [0.0, 0.0, 0.0, 1.0])),
                name=f"{asset['id']}_lineart",
                creases=outline.get("creases", False),
            )

    for index, track in enumerate(ir.get("tracks", [])):
        implementation = IMPLEMENTATIONS.get(track["action"])
        if implementation is None:
            raise ValueError(f"track {index}: no implementation for {track['action']!r}")
        frames = (
            seconds_to_frames(track["start"], fps),
            seconds_to_frames(track["start"] + track["duration"], fps),
        )
        result = implementation(ctx, track["target"], frames, track.get("params", {}))
        stats["tracks"].append({"action": track["action"], "target": track["target"],
                                "frames": list(frames), **(result or {})})

    post = ir.get("post") or {}
    if post.get("bloom", 0) > 0:
        staging.add_post_effects(
            bloom=post.get("bloom", 0.0),
            bloom_threshold=post.get("bloom_threshold", 1.0),
        )
        stats["post"] = {"bloom": post.get("bloom", 0.0)}

    stats["frames"] = frame_count
    stats["fps"] = fps
    stats["actual_duration"] = round(frame_count / fps, 6)
    stats["camera_distance"] = round(distance, 4)
    return stats


def _build_asset(asset, frame_count=None):
    """Ingest one asset and apply its style. Tier A (vector) only for now."""
    curves = svg_ingest.import_svg(asset["source"])
    if len(curves) != 1:
        raise ValueError(f"expected 1 curve from {asset['source']!r}, got {len(curves)}")
    curve = curves[0]

    split_x, gap = svg_ingest.find_x_gap(curve)
    names = asset.get("names", [f"{asset['id']}_marks", f"{asset['id']}_text"])
    if split_x is None:
        parts = [curve]
        curve.name = names[0]
    else:
        parts = svg_ingest.split_at_x(curve, split_x, names)
        bpy.data.objects.remove(curve, do_unlink=True)

    from blended_backend.normalize import world_bounds

    lo, hi = world_bounds(parts)
    width = hi.x - lo.x
    for obj in parts:
        svg_ingest.solidify_curve(
            obj,
            depth=asset.get("extrude", 0.05) * width,
            bevel=asset.get("bevel", 0.004) * width,
            resolution=asset.get("resolution", 12),
        )

    topology = {obj.name: svg_ingest.mesh_report(obj) for obj in parts}
    manifest = norm.normalize(parts, target_size=asset.get("target_size", 1.0))

    if asset.get("material") == "worn":
        material = materials.worn(
            f"{asset['id']}_surface",
            base_color=tuple(asset.get("base_color", [0.05, 0.05, 0.055, 1.0])),
            roughness=asset.get("roughness", 0.38),
            metallic=asset.get("metallic", 0.55),
            wear=asset.get("wear", 0.5),
            wetness=asset.get("wetness", 0.0),
            droplet_amount=asset.get("droplets", 0.0),
        )
    else:
        material = materials.surface(
            f"{asset['id']}_surface",
            tuple(asset.get("base_color", [0.92, 0.92, 0.94, 1.0])),
            asset.get("roughness", 0.35),
            asset.get("metallic", 0.0),
        )
    styles.paint(parts, material)
    return parts, {"manifest": manifest, "topology": topology,
                   "split_x": split_x, "split_gap": gap}
