"""Light actions. Runs inside Blender."""

from __future__ import annotations

from blended_backend.actions import common


def ramp(ctx, target, frames, params):
    """Animate a light's energy over the span. The goal's "dim to bright".

    Keyframes live on the light *datablock* (`light.data.energy`), not on the object, so the
    f-curve lookup for the Tier-2 monotonicity probe has to go through the datablock too.
    """
    light = ctx["objects"][target]
    data = light.data
    start_frame, end_frame = frames

    start_energy = float(params["start_energy"])
    end_energy = float(params["end_energy"])

    data.energy = start_energy
    data.keyframe_insert("energy", frame=start_frame)
    data.energy = end_energy
    data.keyframe_insert("energy", frame=end_frame)
    common.apply_easing_to_data(data, params.get("easing", "ease_in_out"), "energy")

    return {
        "start_energy": start_energy,
        "end_energy": end_energy,
        "ratio": round(end_energy / start_energy, 2) if start_energy else None,
    }
