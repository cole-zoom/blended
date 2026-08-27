"""Stage machinery: field ownership, suppression, approval and drift.

All host-side — no Blender required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blended.approval import Ledger, blocking_drift
from blended.ir.scene import SceneIR
from blended.stages import (
    STAGE_ORDER,
    STAGES,
    changed_paths,
    fingerprint,
    get,
    owned_subset,
    unowned_paths,
    upto,
)


def scene_dict(**overrides) -> dict:
    data = {
        "name": "t",
        "timeline": {"duration": 4.0, "fps": 30},
        "assets": [{"id": "logo", "source": "x.svg", "material": "worn",
                    "base_color": [0.1, 0.1, 0.1, 1.0], "wetness": 0.5}],
        "lights": [{"id": "key", "type": "spot", "energy": 500.0, "azimuth": -30.0}],
        "world": {"color": [0.0, 0.0, 0.0, 1.0]},
        "environment": {"floor": {"enabled": True, "texture": "concrete", "wetness": 0.6},
                        "volumetrics": {"enabled": True, "density": 0.2}},
        "tracks": [
            {"action": "camera.orbit", "target": "camera", "start": 0.0, "duration": 4.0,
             "params": {"start_azimuth": -30.0, "end_azimuth": 30.0}},
            {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 4.0,
             "params": {"start_energy": 10.0, "end_energy": 500.0}},
        ],
    }
    data.update(overrides)
    return json.loads(SceneIR(**data).model_dump_json())


# ------------------------------------------------------------------------------------ ownership


def test_every_stage_is_ordered_and_reachable() -> None:
    assert tuple(STAGES) == STAGE_ORDER
    assert [s.name for s in upto("materials")] == ["assets", "blocking", "materials"]


def test_no_field_is_owned_by_two_stages() -> None:
    """Shared ownership would make 'has this stage changed?' ambiguous."""
    seen: dict[str, str] = {}
    for stage in STAGES.values():
        for path in stage.owns:
            assert path not in seen, f"{path} owned by both {seen[path]} and {stage.name}"
            seen[path] = stage.name


def test_ownership_covers_the_ir() -> None:
    """A field no stage owns can change without ever being flagged."""
    assert unowned_paths(scene_dict()) == []


def test_owned_subset_resolves_list_paths() -> None:
    subset = owned_subset(scene_dict(), get("materials"))
    assert subset["assets[].material"] == ["worn"]
    assert subset["environment.floor.texture"] == "concrete"


def test_missing_paths_resolve_to_none_not_error() -> None:
    """A scene omitting an optional section must still hash, not explode."""
    minimal = {"name": "m", "timeline": {"duration": 1.0, "fps": 30},
               "assets": [{"id": "a", "source": "s.svg"}]}
    ir = json.loads(SceneIR(**minimal).model_dump_json())
    for name in STAGE_ORDER:
        assert isinstance(fingerprint(ir, get(name)), str)


# ---------------------------------------------------------------------------------- suppression


def test_blocking_clays_materials_but_keeps_motion() -> None:
    """Suppression is the point: you cannot judge timing through a finished look."""
    ir = get("blocking").apply(scene_dict())
    assert ir["assets"][0]["material"] == "plain"
    assert ir["assets"][0]["wetness"] == 0.0
    assert ir["assets"][0]["outline"]["mode"] == "none"
    assert ir["environment"]["floor"]["enabled"] is False
    assert ir["environment"]["volumetrics"]["enabled"] is False
    # Motion and lighting timing survive — that is what the stage is for.
    assert any(t["action"] == "camera.orbit" for t in ir["tracks"])
    assert any(t["action"] == "light.ramp" for t in ir["tracks"])


def test_materials_stage_uses_a_neutral_reference_light() -> None:
    """Materials must be judgeable independently of the scene's mood lighting."""
    ir = get("materials").apply(scene_dict())
    assert [lt["id"] for lt in ir["lights"]] == ["ref_key", "ref_fill"]
    assert ir["world"]["color"][0] > 0.1, "reference world is neutral grey, not black"
    # Real materials are preserved — they are the subject of this stage.
    assert ir["assets"][0]["material"] == "worn"
    assert ir["environment"]["floor"]["texture"] == "concrete"


def test_suppressing_lighting_drops_orphaned_light_tracks() -> None:
    """Reference lights replace the scene's, so a light.ramp would target a missing object."""
    ir = get("materials").apply(scene_dict())
    assert not [t for t in ir["tracks"] if t["action"].startswith("light.")]


def test_assets_stage_replaces_the_camera_move_with_a_turntable() -> None:
    ir = get("assets").apply(scene_dict())
    orbits = [t for t in ir["tracks"] if t["action"] == "camera.orbit"]
    assert len(orbits) == 1
    assert orbits[0]["params"]["end_azimuth"] == 360.0


def test_apply_does_not_mutate_the_source_ir() -> None:
    """Rendering a blocking pass must never permanently clay the real materials."""
    ir = scene_dict()
    before = json.dumps(ir, sort_keys=True)
    get("blocking").apply(ir)
    assert json.dumps(ir, sort_keys=True) == before


# ------------------------------------------------------------------------------------- approval


def test_fingerprint_is_stable_and_order_independent() -> None:
    a = scene_dict()
    b = json.loads(json.dumps(a))  # same content, fresh dict ordering
    assert fingerprint(a, get("blocking")) == fingerprint(b, get("blocking"))


def test_fingerprint_only_tracks_owned_fields() -> None:
    """Changing a material must not invalidate blocking approval."""
    a = scene_dict()
    b = scene_dict()
    b["assets"][0]["base_color"] = [0.9, 0.9, 0.9, 1.0]
    assert fingerprint(a, get("blocking")) == fingerprint(b, get("blocking"))
    assert fingerprint(a, get("materials")) != fingerprint(b, get("materials"))


def test_approve_then_drift_names_the_changed_path(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".stages.json")
    ir = scene_dict()
    ledger.approve("t", ir, get("blocking"))
    assert ledger.state("t", ir, get("blocking")).status == "approved"

    moved = scene_dict()
    moved["tracks"][0]["params"]["end_azimuth"] = 90.0
    state = ledger.state("t", moved, get("blocking"))
    assert state.status == "drifted"
    assert "tracks" in state.changed


def test_changed_paths_reports_only_real_differences() -> None:
    a, b = scene_dict(), scene_dict()
    assert changed_paths(a, b, get("blocking")) == []
    b["camera"]["lens"] = 85.0
    assert changed_paths(a, b, get("blocking")) == ["camera"]


def test_ledger_survives_corruption(tmp_path: Path) -> None:
    """A broken ledger must not block work — approvals are recoverable."""
    path = tmp_path / ".stages.json"
    path.write_text("{ not json")
    ledger = Ledger(path)
    assert ledger.approval("t", "blocking") is None
    ledger.approve("t", scene_dict(), get("blocking"))
    assert ledger.approval("t", "blocking") is not None


def test_only_upstream_drift_blocks(tmp_path: Path) -> None:
    """Re-running `lighting` after a material change matters; later stages do not."""
    ledger = Ledger(tmp_path / ".stages.json")
    ir = scene_dict()
    for name in ("blocking", "materials"):
        ledger.approve("t", ir, get(name))

    moved = scene_dict()
    moved["assets"][0]["base_color"] = [1.0, 0.0, 0.0, 1.0]  # materials-owned
    states = ledger.states("t", moved, [get(n) for n in STAGE_ORDER])

    assert [s.stage for s in blocking_drift(states, "lighting")] == ["materials"]
    # Running the materials stage itself is not blocked by its own drift.
    assert blocking_drift(states, "materials") == []


def test_revoke_removes_approval(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".stages.json")
    ir = scene_dict()
    ledger.approve("t", ir, get("blocking"))
    assert ledger.revoke("t", "blocking") is True
    assert ledger.state("t", ir, get("blocking")).status == "pending"
    assert ledger.revoke("t", "blocking") is False


def test_scenes_are_isolated_in_the_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".stages.json")
    ledger.approve("scene_a", scene_dict(), get("blocking"))
    assert ledger.approval("scene_b", "blocking") is None


@pytest.mark.parametrize("name", STAGE_ORDER)
def test_motion_stages_never_skip_frames(name: str) -> None:
    """A stepped video plays back at the wrong speed."""
    stage = get(name)
    if stage.media in ("video", "sequence"):
        assert stage.frame_step == 1


def test_the_long_stage_renders_a_resumable_sequence() -> None:
    """`final` is the only stage where losing an interrupted render actually hurts."""
    assert get("final").media == "sequence"


def test_stages_before_lighting_drop_compositing() -> None:
    """post.bloom raises in the backend, so inheriting it would crash blocking outright."""
    ir = scene_dict()
    ir["post"] = {"bloom": 0.6, "bloom_threshold": 0.8}
    for name in ("assets", "blocking", "materials"):
        assert get(name).apply(ir)["post"]["bloom"] == 0.0, name
    # `lighting` owns it and must keep it.
    assert get("lighting").apply(ir)["post"]["bloom"] == 0.6
