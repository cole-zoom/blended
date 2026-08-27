"""Scene IR, the action library, and Tier 1 static verification.

Most of these need no Blender — which is the point of Tier 1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blended.ir.scene import SceneIR, Timeline
from blended.library import registry
from blended.project import load
from blended.verify.static import check

PROJECT = Path(__file__).resolve().parent.parent / "projects" / "lancedb"

# `projects/` is gitignored — scenes and renders are per-machine work product, not source.
requires_project = pytest.mark.skipif(
    not PROJECT.exists(), reason="projects/ scenes not present"
)


def minimal(**overrides) -> dict:
    data = {
        "name": "t",
        "timeline": {"duration": 4.0, "fps": 30},
        "assets": [{"id": "logo", "source": "x.svg"}],
        "lights": [{"id": "key"}],
        "tracks": [],
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------------------------- timeline


def test_quantization_happens_once_and_reports_drift() -> None:
    assert Timeline(duration=16.0, fps=30).frames == 480
    assert Timeline(duration=16.0, fps=30).drift == 0.0

    # The original 16.58s ask does not divide evenly — the machinery still has to report it.
    awkward = Timeline(duration=16.58, fps=24)
    assert awkward.frames == 398
    assert abs(awkward.drift) > 0


@pytest.mark.parametrize("fps", [24, 25, 30, 60])
def test_sixteen_seconds_is_exact_at_every_common_fps(fps: int) -> None:
    """Why 16.0s was chosen: frame rate stays a free choice."""
    assert Timeline(duration=16.0, fps=fps).drift == 0.0


# ------------------------------------------------------------------------------- library/schema


def test_every_declared_action_has_an_implementation() -> None:
    """The host declares actions and the backend implements them; they cannot share code
    (no bpy on the host), so drift between the two is caught here instead."""
    source = (Path(__file__).resolve().parent.parent
              / "src" / "blended_backend" / "actions" / "__init__.py").read_text()
    for name in registry.names():
        assert f'"{name}"' in source, f"{name} is declared but has no backend implementation"


def test_actions_declare_channel_footprints() -> None:
    orbit = registry.get("camera.orbit")
    assert orbit.channels("camera") == ("camera.transform",)
    assert registry.get("light.ramp").channels("key") == ("key.energy",)
    # hold deliberately writes nothing, so it can never conflict.
    assert registry.get("object.hold").writes == ()


# ---------------------------------------------------------------------------------- tier 1


def test_clean_scene_passes() -> None:
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 4.0,
         "params": {"start_energy": 1.0, "end_energy": 10.0}},
    ]))
    assert check(scene).ok


def test_channel_conflict_is_an_error_naming_both_tracks() -> None:
    """The check no bpy-writing agent can offer."""
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 3.0,
         "params": {"start_energy": 1.0, "end_energy": 5.0}},
        {"action": "light.ramp", "target": "key", "start": 2.0, "duration": 2.0,
         "params": {"start_energy": 5.0, "end_energy": 9.0}},
    ]))
    report = check(scene)
    conflicts = [d for d in report.errors if d.code == "CHANNEL_CONFLICT"]
    assert len(conflicts) == 1
    assert "key.energy" in conflicts[0].message
    assert conflicts[0].suggested_fix is not None


def test_sequential_tracks_on_one_channel_do_not_conflict() -> None:
    """Ramp then hold is the shape the goal needs; abutting spans must be legal."""
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 2.0,
         "params": {"start_energy": 1.0, "end_energy": 5.0}},
        {"action": "object.hold", "target": "key", "start": 2.0, "duration": 2.0},
    ]))
    assert check(scene).ok


def test_track_past_the_clock_is_an_error() -> None:
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "key", "start": 3.0, "duration": 5.0,
         "params": {"start_energy": 1.0, "end_energy": 5.0}},
    ]))
    errors = {d.code for d in check(scene).errors}
    assert "TRACK_EXCEEDS_CLOCK" in errors


def test_unknown_target_and_action_are_errors() -> None:
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "ghost", "start": 0.0, "duration": 1.0,
         "params": {"start_energy": 1.0, "end_energy": 2.0}},
        {"action": "camera.nope", "target": "camera", "start": 0.0, "duration": 1.0},
    ]))
    errors = {d.code for d in check(scene).errors}
    assert {"UNKNOWN_TARGET", "UNKNOWN_ACTION"} <= errors


def test_action_rejects_wrong_target_kind() -> None:
    scene = SceneIR(**minimal(tracks=[
        {"action": "light.ramp", "target": "logo", "start": 0.0, "duration": 1.0,
         "params": {"start_energy": 1.0, "end_energy": 2.0}},
    ]))
    assert "TARGET_KIND_MISMATCH" in {d.code for d in check(scene).errors}


# ------------------------------------------------------------------------------ shipped scenes


@requires_project
def test_shipped_scene_is_valid() -> None:
    scene = load(PROJECT / "scene.json")
    report = check(scene)
    assert report.ok, [d.message for d in report.errors]
    assert scene.timeline.frames == 480
    assert scene.timeline.drift == 0.0


@requires_project
def test_shipped_scene_matches_the_goal() -> None:
    """The brief, checked against the IR rather than against a render."""
    scene = load(PROJECT / "scene.json")

    orbit = next(t for t in scene.tracks if t.action == "camera.orbit")
    sweep = abs(orbit.params["end_azimuth"] - orbit.params["start_azimuth"])
    assert sweep >= 60, "camera must pan around the logo"
    # "start at floor level, orbit up and away, decelerating"
    assert orbit.params["start_elevation"] < 0, "starts below the logo's centre"
    assert orbit.params["end_elevation"] > orbit.params["start_elevation"]
    assert orbit.params["end_distance_scale"] > orbit.params["start_distance_scale"]
    assert orbit.params["easing"] == "ease_out"

    ramp = next(t for t in scene.tracks if t.action == "light.ramp")
    assert ramp.params["start_energy"] > 0, "dim, not off"
    assert ramp.params["end_energy"] / ramp.params["start_energy"] >= 5.0
    assert ramp.end == pytest.approx(10.0), "brightest at 10s, then held"
    assert any(t.action == "object.hold" and t.start == 10.0 for t in scene.tracks)

    # One lamp from the corner, not a sun.
    assert [lt.type for lt in scene.lights] == ["spot"]

    assert scene.world.color[:3] == (0.0, 0.0, 0.0), "black background"
    assert scene.world.hdri, "wet surfaces need an environment to reflect"

    floor = scene.environment.floor
    assert floor is not None and floor.enabled and floor.texture
    assert floor.wetness > 0
    # Settled after measuring flicker: ripples off, relief damped where wet.
    assert floor.ripples == 0.0
    assert floor.wet_flatten < 0.5
