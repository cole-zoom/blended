"""Patches, history, revert, and the render cache.

The founding complaint this project started from was that one change regenerates everything.
These tests guard the answer to it: an edit is a validated, reversible, scoped patch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blended.cache import RenderCache, fingerprint
from blended.patch import (
    PatchError,
    apply,
    apply_to_file,
    dry_run,
    entries,
    invert,
    read,
    revert_last,
    touched_stages,
)


def scene_dict(**overrides) -> dict:
    data = {
        "name": "t",
        "timeline": {"duration": 8.0, "fps": 30},
        "assets": [{"id": "logo", "source": "x.svg", "wetness": 0.5}],
        "lights": [{"id": "key", "type": "spot", "energy": 300.0}],
        "tracks": [
            {"action": "camera.orbit", "target": "camera", "start": 0.0, "duration": 8.0,
             "params": {"start_azimuth": -20.0, "end_azimuth": 20.0}},
            {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 8.0,
             "params": {"start_energy": 10.0, "end_energy": 300.0}},
        ],
    }
    data.update(overrides)
    return data


def write_scene(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(scene_dict(**overrides), indent=2))
    return path


# ---------------------------------------------------------------------------------- pointers


def test_reads_nested_and_indexed_paths() -> None:
    doc = scene_dict()
    assert read(doc, "/timeline/fps") == 30
    assert read(doc, "/tracks/0/action") == "camera.orbit"
    assert read(doc, "/assets/0/wetness") == 0.5


def test_rejects_malformed_and_missing_paths() -> None:
    doc = scene_dict()
    with pytest.raises(PatchError, match="must start with"):
        read(doc, "timeline/fps")
    with pytest.raises(PatchError, match="does not exist"):
        read(doc, "/timeline/nope")


def test_escaped_pointer_tokens() -> None:
    """RFC 6901: ~1 is '/', ~0 is '~'. Unescaping in the wrong order corrupts both."""
    assert read({"a/b": 1}, "/a~1b") == 1
    assert read({"a~b": 2}, "/a~0b") == 2


# ------------------------------------------------------------------------------------- apply


def test_apply_does_not_mutate_the_input() -> None:
    doc = scene_dict()
    before = json.dumps(doc, sort_keys=True)
    apply(doc, [{"op": "replace", "path": "/timeline/fps", "value": 60}])
    assert json.dumps(doc, sort_keys=True) == before


def test_replace_add_remove() -> None:
    doc = scene_dict()
    assert apply(doc, [{"op": "replace", "path": "/timeline/fps",
                        "value": 60}])["timeline"]["fps"] == 60
    added = apply(doc, [{"op": "add", "path": "/timeline/note", "value": "hi"}])
    assert added["timeline"]["note"] == "hi"
    removed = apply(doc, [{"op": "remove", "path": "/assets/0/wetness"}])
    assert "wetness" not in removed["assets"][0]


def test_add_appends_to_a_list() -> None:
    doc = scene_dict()
    out = apply(doc, [{"op": "add", "path": "/tracks/-",
                       "value": {"action": "object.hold", "target": "logo",
                                 "start": 0.0, "duration": 1.0}}])
    assert len(out["tracks"]) == 3


def test_replace_refuses_a_missing_path() -> None:
    """`replace` on something absent is almost always a typo, not an intent to create."""
    with pytest.raises(PatchError, match="cannot replace missing"):
        apply(scene_dict(), [{"op": "replace", "path": "/timeline/nope", "value": 1}])


def test_a_failing_operation_leaves_the_document_untouched() -> None:
    doc = scene_dict()
    with pytest.raises(PatchError, match="operation 1"):
        apply(doc, [
            {"op": "replace", "path": "/timeline/fps", "value": 60},
            {"op": "replace", "path": "/nope", "value": 1},
        ])
    assert doc["timeline"]["fps"] == 30


# ------------------------------------------------------------------------------------ invert


def test_inverse_restores_exactly() -> None:
    doc = scene_dict()
    ops = [{"op": "replace", "path": "/timeline/fps", "value": 60},
           {"op": "remove", "path": "/assets/0/wetness"},
           {"op": "add", "path": "/timeline/note", "value": "x"}]
    changed = apply(doc, ops)
    assert apply(changed, invert(doc, ops)) == doc


def test_inverse_of_a_list_append_removes_it() -> None:
    doc = scene_dict()
    ops = [{"op": "add", "path": "/tracks/-",
            "value": {"action": "object.hold", "target": "logo",
                      "start": 0.0, "duration": 1.0}}]
    assert apply(apply(doc, ops), invert(doc, ops)) == doc


# ------------------------------------------------------------------------------------ scoping


def test_a_patch_reports_which_stages_it_unsettles() -> None:
    """A material tweak that quietly unapproved blocking is the regression this prevents."""
    assert touched_stages([{"op": "replace", "path": "/assets/0/wetness",
                            "value": 0.1}]) == ["materials"]
    assert "blocking" in touched_stages([{"op": "replace", "path": "/tracks/0/duration",
                                          "value": 4.0}])
    assert touched_stages([{"op": "replace", "path": "/world/color",
                            "value": [0, 0, 0, 1]}]) == ["lighting"]


# ------------------------------------------------------------------------------ validation


def test_a_breaking_patch_never_reaches_disk(tmp_path: Path) -> None:
    """Validation happens before the write, so a bad edit cannot leave a broken project."""
    scene = write_scene(tmp_path)
    original = scene.read_text()
    with pytest.raises(PatchError, match="would break the scene"):
        apply_to_file(scene, [{"op": "replace", "path": "/timeline/duration", "value": 1.0}])
    assert scene.read_text() == original
    assert not (tmp_path / "history").exists()


def test_a_patch_producing_invalid_ir_is_rejected(tmp_path: Path) -> None:
    scene = write_scene(tmp_path)
    with pytest.raises(PatchError, match="invalid scene"):
        apply_to_file(scene, [{"op": "replace", "path": "/timeline/fps", "value": -5}])


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    scene = write_scene(tmp_path)
    before = scene.read_text()
    result = dry_run(json.loads(before), [{"op": "replace", "path": "/timeline/fps",
                                           "value": 60}])
    assert result.ok
    assert scene.read_text() == before


# -------------------------------------------------------------------------- history / revert


def test_applying_records_history(tmp_path: Path) -> None:
    scene = write_scene(tmp_path)
    apply_to_file(scene, [{"op": "replace", "path": "/timeline/fps", "value": 60}],
                  note="faster")
    history = entries(scene)
    assert len(history) == 1
    assert history[0]["note"] == "faster"
    assert history[0]["inverse"][0]["value"] == 30


def test_revert_restores_and_clears_its_own_record(tmp_path: Path) -> None:
    """Undo should leave history reading as though the change never happened."""
    scene = write_scene(tmp_path)
    before = json.loads(scene.read_text())
    apply_to_file(scene, [{"op": "replace", "path": "/timeline/fps", "value": 60}])
    revert_last(scene)
    assert json.loads(scene.read_text()) == before
    assert entries(scene) == []


def test_revert_with_no_history_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PatchError, match="no patches"):
        revert_last(write_scene(tmp_path))


def test_reverts_unwind_in_order(tmp_path: Path) -> None:
    scene = write_scene(tmp_path)
    before = json.loads(scene.read_text())
    apply_to_file(scene, [{"op": "replace", "path": "/timeline/fps", "value": 60}])
    # Lengthening is safe; shortening below the tracks would be correctly refused.
    apply_to_file(scene, [{"op": "replace", "path": "/timeline/duration", "value": 12.0}])
    revert_last(scene)
    assert json.loads(scene.read_text())["timeline"]["fps"] == 60
    revert_last(scene)
    assert json.loads(scene.read_text()) == before


# -------------------------------------------------------------------------------------- cache


def _job(**render):
    base = {"engine": "BLENDER_EEVEE", "resolution": [640, 360], "fps": 30,
            "frame_start": 1, "frame_end": 100, "frame_step": 1, "samples": 8,
            "media": "video"}
    base.update(render)
    return {"scene": {"ir": scene_dict()}, "render": base}


def test_fingerprint_tracks_what_changes_the_image() -> None:
    assert fingerprint(_job()) == fingerprint(_job())
    assert fingerprint(_job()) != fingerprint(_job(samples=64))
    assert fingerprint(_job()) != fingerprint(_job(resolution=[1280, 720]))


def test_fingerprint_ignores_what_does_not() -> None:
    """Keying on incidental fields would make the cache miss constantly."""
    a = _job()
    b = _job()
    b["render"]["output"] = "/somewhere/else.mp4"
    b["blend_out"] = "/other.blend"
    assert fingerprint(a) == fingerprint(b)


def test_cache_round_trip(tmp_path: Path) -> None:
    artifact = tmp_path / "out.mp4"
    artifact.write_bytes(b"x")
    cache = RenderCache(tmp_path)
    assert cache.lookup("final", "abc") is None
    cache.store("final", "abc", {"video": str(artifact)})
    assert cache.lookup("final", "abc") is not None


def test_a_deleted_artifact_is_a_miss(tmp_path: Path) -> None:
    artifact = tmp_path / "out.mp4"
    artifact.write_bytes(b"x")
    cache = RenderCache(tmp_path)
    cache.store("final", "abc", {"video": str(artifact)})
    artifact.unlink()
    assert cache.lookup("final", "abc") is None


def test_prefix_artifacts_count_as_present(tmp_path: Path) -> None:
    """Regression: stills stages record a prefix, not a file. Testing that with `exists()`
    made every such stage a permanent cache miss."""
    (tmp_path / "scene_assets_0001.png").write_bytes(b"x")
    cache = RenderCache(tmp_path)
    cache.store("assets", "k", {"stills": str(tmp_path / "scene_assets_")})
    assert cache.lookup("assets", "k") is not None


def test_a_zero_byte_frame_does_not_count(tmp_path: Path) -> None:
    """An interrupted write must not be mistaken for a finished render."""
    (tmp_path / "scene_assets_0001.png").write_bytes(b"")
    cache = RenderCache(tmp_path)
    cache.store("assets", "k", {"stills": str(tmp_path / "scene_assets_")})
    assert cache.lookup("assets", "k") is None
