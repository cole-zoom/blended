"""Tier-2 scene probes. Runs inside Blender (ARCHITECTURE §8).

The underrated verification tier. These run **after the scene is built and before anything is
rendered**, querying ground truth directly out of `bpy` — so "the logo drifted out of frame" or
"the light ramps the wrong way" costs seconds to discover instead of an eighty-minute render.

Probes report facts, not verdicts. Whether a fact is a failure depends on intent, and intent
lives in the assertions the host applies (`blended.verify.probes`). Keeping measurement separate
from judgement means a probe can be reused by a stage that cares about it differently.

Each probe returns a JSON-safe dict. Sampling is sparse by design — twenty-odd frames across a
sweep catch every failure seen so far, and cost milliseconds.
"""

from __future__ import annotations

import math
import os

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

#: Frames sampled across the timeline for motion probes. Enough to catch a sweep leaving frame
#: or a ramp reversing, few enough to stay instant.
DEFAULT_SAMPLES = 24


def sample_frames(scene, count=DEFAULT_SAMPLES):
    start, end = scene.frame_start, scene.frame_end
    if end <= start:
        return [start]
    step = max(1, (end - start) // max(1, count - 1))
    frames = list(range(start, end + 1, step))
    if frames[-1] != end:
        frames.append(end)
    return frames


def _subject_objects(scene, names=None):
    """Renderable objects making up the subject, excluding staging.

    `backdrop` is staging: it is deliberately oversized so a camera move cannot slide the
    frame off its edge, so counting it as subject makes the framing probe report that the
    subject overflows the frame on every single frame — technically true of the card, useless
    as a statement about the shot.

    Curves count as subject too. A morphing glyph stays a curve so Blender can re-fill it each
    frame, and it is often the hero of the shot — excluding it would mean framing and coverage
    silently ignore the one object the piece is about.
    """
    excluded_suffixes = ("_outline", "_lineart")
    excluded_names = {"floor", "atmosphere", "backdrop"}
    out = []
    for obj in scene.collection.all_objects:
        if obj.type not in ("MESH", "CURVE"):
            continue
        if obj.name in excluded_names or obj.name.endswith(excluded_suffixes):
            continue
        if names and obj.name not in names:
            continue
        out.append(obj)
    return out


def _world_corners(objs):
    return [obj.matrix_world @ Vector(c) for obj in objs for c in obj.bound_box]


# ------------------------------------------------------------------------------------- geometry


def geometry(scene, ir=None):
    """Static facts about the subject's mesh. No camera, no lights, no time."""
    from blended_backend.ingest import svg as svg_ingest

    objs = _subject_objects(scene)
    report = {"objects": {}}
    for obj in objs:
        stats = svg_ingest.mesh_report(obj)
        report["objects"][obj.name] = stats

    corners = _world_corners(objs)
    if corners:
        lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                     min(c.z for c in corners)))
        hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                     max(c.z for c in corners)))
        size = hi - lo
        report["bounds"] = {"min": list(lo), "max": list(hi), "size": list(size)}
        report["aspect"] = round(size.x / size.z, 4) if size.z else None
        report["depth_ratio"] = round(size.y / size.x, 5) if size.x else None
    report["manifold"] = all(o["is_manifold"] for o in report["objects"].values())
    report["total_holes"] = sum(o["holes"] for o in report["objects"].values())
    return report


# -------------------------------------------------------------------------------------- framing


def framing(scene, ir=None, samples=DEFAULT_SAMPLES):
    """Is the subject actually in shot, and how much of the frame does it fill?

    The single most valuable probe. Projecting the subject's bounding box into camera NDC
    answers "can you see it" without rendering, and a wide subject swinging off-axis is exactly
    the failure a still frame at t=0 will not reveal.
    """
    camera = scene.camera
    objs = _subject_objects(scene)
    if camera is None or not objs:
        return {"error": "no camera or no subject"}

    depsgraph = bpy.context.evaluated_depsgraph_get()
    rows = []
    for frame in sample_frames(scene, samples):
        scene.frame_set(frame)
        corners = _world_corners(objs)
        ndc = [world_to_camera_view(scene, camera, c) for c in corners]

        xs = [p.x for p in ndc]
        ys = [p.y for p in ndc]
        # Two different questions. "Every corner is in front" fails routinely on a deliberate
        # extreme close-up; "the subject's centre is in front" fails only when the camera has
        # genuinely passed through the thing it is filming.
        centre_ndc = world_to_camera_view(scene, camera, sum(corners, Vector()) / len(corners))
        fully_in_front = all(p.z > 0 for p in ndc)
        in_front = centre_ndc.z > 0
        # Fraction of the frame the subject's projected box covers. Clamped to the frame, so a
        # subject partly off-screen reports what is actually visible.
        vis_w = max(0.0, min(1.0, max(xs)) - max(0.0, min(xs)))
        vis_h = max(0.0, min(1.0, max(ys)) - max(0.0, min(ys)))
        rows.append({
            "frame": frame,
            "coverage": round(vis_w * vis_h, 5),
            "width": round(vis_w, 5),
            "in_frame": bool(fully_in_front and min(xs) >= -0.02 and max(xs) <= 1.02
                             and min(ys) >= -0.02 and max(ys) <= 1.02),
            "in_front": bool(in_front),
            "fully_in_front": bool(fully_in_front),
        })
    del depsgraph

    coverages = [r["coverage"] for r in rows]
    return {
        "samples": rows,
        "min_coverage": round(min(coverages), 5),
        "max_coverage": round(max(coverages), 5),
        "min_width": round(min(r["width"] for r in rows), 5),
        "always_in_frame": all(r["in_frame"] for r in rows),
        "always_in_front": all(r["in_front"] for r in rows),
        "frames_off_screen": [r["frame"] for r in rows if not r["in_frame"]],
    }


# --------------------------------------------------------------------------------------- motion


def motion(scene, ir=None, samples=DEFAULT_SAMPLES):
    """Camera path facts: is it orbiting, rising, receding?

    Distance is measured to the subject centre rather than to the orbit pivot, so the probe
    still means something if the camera is animated some other way.
    """
    camera = scene.camera
    objs = _subject_objects(scene)
    if camera is None or not objs:
        return {"error": "no camera or no subject"}

    rows = []
    for frame in sample_frames(scene, samples):
        scene.frame_set(frame)
        corners = _world_corners(objs)
        centre = sum(corners, Vector()) / len(corners)
        pos = camera.matrix_world.translation
        offset = pos - centre
        horizontal = math.hypot(offset.x, offset.y)
        rows.append({
            "frame": frame,
            "distance": round(offset.length, 5),
            "height": round(pos.z, 5),
            "elevation": round(math.degrees(math.atan2(offset.z, horizontal)), 3),
            "azimuth": round(math.degrees(math.atan2(offset.x, -offset.y)), 3),
        })

    distances = [r["distance"] for r in rows]
    azimuths = [r["azimuth"] for r in rows]
    spread = (max(distances) - min(distances)) / max(1e-9, sum(distances) / len(distances))
    return {
        "samples": rows,
        "distance_min": round(min(distances), 5),
        "distance_max": round(max(distances), 5),
        "distance_variation": round(spread, 5),
        "height_start": rows[0]["height"],
        "height_end": rows[-1]["height"],
        "elevation_start": rows[0]["elevation"],
        "elevation_end": rows[-1]["elevation"],
        "azimuth_sweep": round(abs(_unwrap(azimuths)[-1] - _unwrap(azimuths)[0]), 3),
        "azimuth_monotonic": _monotonic(_unwrap(azimuths)),
    }


def _unwrap(degrees):
    """Undo the ±180 wrap so a sweep across the seam reads as continuous."""
    out = [degrees[0]]
    for value in degrees[1:]:
        previous = out[-1]
        while value - previous > 180:
            value -= 360
        while value - previous < -180:
            value += 360
        out.append(value)
    return out


def _monotonic(values, tolerance=1e-6):
    ups = all(b >= a - tolerance for a, b in zip(values, values[1:]))
    downs = all(b <= a + tolerance for a, b in zip(values, values[1:]))
    return bool(ups or downs)


# ---------------------------------------------------------------------------------------- light


def light(scene, ir=None, samples=DEFAULT_SAMPLES):
    """Light energy over time, and whether each light points at the subject.

    A light that exists but aims into the void is the most likely silent failure in a scene, and
    it renders as "everything is dark" rather than as any kind of error.
    """
    objs = _subject_objects(scene)
    lights = [o for o in scene.collection.all_objects if o.type == "LIGHT"]
    if not lights:
        return {"lights": {}, "note": "no lights in scene"}

    centre = None
    if objs:
        corners = _world_corners(objs)
        centre = sum(corners, Vector()) / len(corners)

    frames = sample_frames(scene, samples)
    report = {}
    for obj in lights:
        energies = []
        for frame in frames:
            scene.frame_set(frame)
            energies.append(round(obj.data.energy, 5))

        entry = {
            "type": obj.data.type,
            "energy_start": energies[0],
            "energy_end": energies[-1],
            "energy_min": min(energies),
            "energy_max": max(energies),
            "monotonic": _monotonic(energies),
            "ratio": round(max(energies) / energies[0], 3) if energies[0] > 0 else None,
            "peak_frame": frames[energies.index(max(energies))],
        }
        if centre is not None:
            # A lamp looks down its local -Z. Positive alignment means it faces the subject.
            forward = (obj.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))).normalized()
            to_subject = (centre - obj.matrix_world.translation)
            if to_subject.length > 1e-9:
                entry["aim_alignment"] = round(forward.dot(to_subject.normalized()), 4)
        report[obj.name] = entry
    return {"lights": report, "frames": frames}


# ------------------------------------------------------------------------------------ materials


def materials(scene, ir=None):
    """Material facts: are textures actually loaded, and are colour spaces right?

    A missing image renders as flat magenta with no error, and a normal map left on sRGB
    produces a plausible-looking but subtly plastic surface. Both are silent, so both are
    checked rather than eyeballed.
    """
    report = {"images": {}, "materials": {}, "missing_images": [], "suspect_colorspace": []}
    for image in bpy.data.images:
        if image.source != "FILE":
            continue
        # File existence, not `has_data`. Blender loads pixels lazily, so `has_data` is False
        # for every image until something forces a read — which makes it report "0/5 loaded" on
        # a build-only run where nothing is wrong. What actually causes the magenta failure is
        # a path that does not resolve, and that is what is checked.
        resolved = bpy.path.abspath(image.filepath) if image.filepath else ""
        exists = bool(resolved) and os.path.exists(resolved)
        report["images"][image.name] = {
            "exists": exists,
            "in_memory": bool(image.has_data),
            "size": list(image.size),
            "colorspace": image.colorspace_settings.name,
            "filepath": resolved,
        }
        if not exists:
            report["missing_images"].append(image.name)
        # Data maps must not carry a colour transform.
        role = image.name.lower()
        is_data = any(k in role for k in ("normal", "rough", "disp", "ao", "metal", "bump"))
        if is_data and image.colorspace_settings.name != "Non-Color":
            report["suspect_colorspace"].append(image.name)

    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        kinds = [n.type for n in material.node_tree.nodes]
        report["materials"][material.name] = {
            "nodes": len(kinds),
            "image_textures": kinds.count("TEX_IMAGE"),
            "animated": bool(material.node_tree.animation_data
                             and material.node_tree.animation_data.action),
        }
    return report


PROBES = {
    "geometry": geometry,
    "framing": framing,
    "motion": motion,
    "light": light,
    "materials": materials,
}


def run(scene, names, ir=None):
    """Run the named probes, restoring the timeline afterwards.

    Probes call `frame_set`, which mutates scene state. Leaving the playhead moved would change
    what a subsequent single-frame render produces — a genuinely confusing bug to chase.
    """
    original = scene.frame_current
    out = {}
    try:
        for name in names:
            probe = PROBES.get(name)
            if probe is None:
                out[name] = {"error": f"unknown probe {name!r}"}
                continue
            try:
                out[name] = probe(scene, ir)
            except Exception as exc:  # a probe must never break a build
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        scene.frame_set(original)
    return out
