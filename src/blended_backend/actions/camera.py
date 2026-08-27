"""Camera actions. Runs inside Blender."""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from blended_backend import staging
from blended_backend.actions import common


def orbit(ctx, target, frames, params):
    """Sweep the camera around the subject on an elevated arc.

    Built as a rotating pivot with the camera parented to it, rather than by keyframing camera
    positions along the arc. Two consequences, both of which matter:

    * Distance to the subject is **constant by construction**. The Tier-2 probe asserting
      `distance_to(subject)` never varies then cannot fail for interpolation reasons — sampled
      positions with Bezier handles genuinely do drift off the circle between keys.
    * The whole sweep is two keyframes on one channel, so easing is exact and the IR stays
      readable.

    A TRACK_TO constraint keeps the camera aimed at the pivot throughout, so "the camera is
    pointed at the logo" is a structural guarantee rather than a per-frame computation.
    """
    scene = bpy.context.scene
    camera = ctx["camera"]
    subject = ctx["subject_objects"]
    start_frame, end_frame = frames

    lo, hi = staging.world_bounds(subject)
    centre = (lo + hi) / 2.0

    distance = params.get("distance")
    if distance is None:
        distance = staging.fit_distance(camera, subject, ctx["margin"])

    pivot = bpy.data.objects.new("orbit_pivot", None)
    pivot.empty_display_type = "PLAIN_AXES"
    pivot.location = centre
    scene.collection.objects.link(pivot)

    start_elevation = params.get("start_elevation", 12.0)
    end_elevation = params.get("end_elevation")
    if end_elevation is None:
        end_elevation = start_elevation
    start_distance = distance * params.get("start_distance_scale", 1.0)
    end_distance = distance * params.get("end_distance_scale", 1.0)

    easing = params.get("easing", "ease_in_out")

    # Camera sits at azimuth 0 in the pivot's local frame; the pivot's Z rotation supplies the
    # sweep. Local offset mirrors staging.spherical() so the two agree on what azimuth means.
    camera.parent = pivot
    camera.rotation_euler = (0.0, 0.0, 0.0)

    track = camera.constraints.new("TRACK_TO")
    track.target = pivot
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    # Azimuth on the pivot.
    start_az = math.radians(params.get("start_azimuth", -30.0))
    end_az = math.radians(params.get("end_azimuth", 30.0))
    pivot.rotation_euler = (0.0, 0.0, start_az)
    pivot.keyframe_insert("rotation_euler", index=2, frame=start_frame)
    pivot.rotation_euler = (0.0, 0.0, end_az)
    pivot.keyframe_insert("rotation_euler", index=2, frame=end_frame)
    common.apply_easing(pivot, easing)

    # Elevation and distance on the camera's local position. Two keyframes, and because the
    # camera is parented the interpolation happens in the pivot's frame — so a rising, receding
    # camera is still exactly circular in plan view.
    camera.location = _local_offset(start_distance, start_elevation)
    camera.keyframe_insert("location", frame=start_frame)
    camera.location = _local_offset(end_distance, end_elevation)
    camera.keyframe_insert("location", frame=end_frame)
    common.apply_easing(camera, easing, "location")

    return {
        "pivot": pivot.name,
        "base_distance": round(distance, 4),
        "distance": [round(start_distance, 4), round(end_distance, 4)],
        "elevation": [start_elevation, end_elevation],
        "sweep_degrees": round(abs(math.degrees(end_az - start_az)), 2),
        "easing": easing,
    }


def _local_offset(distance, elevation_degrees):
    elevation = math.radians(elevation_degrees)
    return Vector(
        (0.0, -distance * math.cos(elevation), distance * math.sin(elevation))
    )
