"""Live reload: the resolved-scene contract between terminal and viewport.

The add-on runs inside Blender with no network and no host packages, so everything it needs has
to be in the file this publishes. These tests guard that contract.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from blended.live import RESOLVED_SUFFIX, publish, resolved_path
from blended.stages import STAGE_ORDER

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon" / "blended_live.py"


def write_scene(tmp_path: Path, **overrides) -> Path:
    data = {
        "name": "t",
        "timeline": {"duration": 4.0, "fps": 30},
        "assets": [{"id": "logo", "source": "logo.svg"}],
        "lights": [{"id": "key", "type": "spot", "energy": 300.0}],
        "tracks": [
            {"action": "camera.orbit", "target": "camera", "start": 0.0, "duration": 4.0,
             "params": {"start_azimuth": -20.0, "end_azimuth": 20.0}},
            {"action": "light.ramp", "target": "key", "start": 0.0, "duration": 4.0,
             "params": {"start_energy": 10.0, "end_energy": 300.0}},
        ],
    }
    data.update(overrides)
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(data))
    return path


# ------------------------------------------------------------------------------------ publish


def test_resolved_sits_beside_the_scene(tmp_path: Path) -> None:
    scene = write_scene(tmp_path)
    assert resolved_path(scene).name == f"scene{RESOLVED_SUFFIX}"
    assert resolved_path(scene).parent == scene.parent


def test_publish_writes_everything_the_addon_needs(tmp_path: Path) -> None:
    """The add-on has no network and no host packages — the file must be self-sufficient."""
    result = publish(write_scene(tmp_path), cache_root=tmp_path / "cache")
    payload = json.loads(result.path.read_text())

    assert payload["ok"] is True
    assert payload["ir"]["timeline"]["fps"] == 30
    # Self-describing: the add-on finds `blended_backend` from this rather than a preference.
    assert Path(payload["src"], "blended_backend").is_dir()


def test_publish_records_validation_failure_instead_of_raising(tmp_path: Path) -> None:
    """A broken edit must not kill the watcher, and the add-on must be told rather than left
    showing a stale scene."""
    scene = write_scene(tmp_path, tracks=[
        {"action": "light.ramp", "target": "ghost", "start": 0.0, "duration": 1.0,
         "params": {"start_energy": 1.0, "end_energy": 2.0}},
    ])
    result = publish(scene, cache_root=tmp_path / "cache")
    assert result.ok is False
    assert result.errors
    assert json.loads(result.path.read_text())["ok"] is False


def test_publish_is_atomic(tmp_path: Path) -> None:
    """The add-on polls this file; a half-written JSON would be read exactly as it changes."""
    scene = write_scene(tmp_path)
    for _ in range(3):
        result = publish(scene, cache_root=tmp_path / "cache")
        json.loads(result.path.read_text())  # never truncated
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_publish_can_apply_a_stage_view(tmp_path: Path, stage: str) -> None:
    """Watching a stage shows what that stage shows — clay at blocking, not the finished look."""
    result = publish(write_scene(tmp_path), stage=stage, cache_root=tmp_path / "cache")
    payload = json.loads(result.path.read_text())
    assert payload["stage"] == stage
    if stage == "blocking":
        assert payload["ir"]["assets"][0]["material"] == "plain"


# -------------------------------------------------------------------------------------- addon


def test_addon_is_importable_python() -> None:
    ast.parse(ADDON.read_text())


def test_addon_declares_blender_metadata() -> None:
    tree = ast.parse(ADDON.read_text())
    info = next(
        (n.value for n in tree.body
         if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "bl_info"),
        None,
    )
    assert info is not None, "an add-on without bl_info cannot be installed"
    keys = {k.value for k in info.keys}
    assert {"name", "blender", "category", "location"} <= keys


def test_addon_imports_nothing_the_gui_lacks() -> None:
    """The add-on runs in Blender's Python, same constraint as the backend: stdlib + bpy.

    `blended_backend` is imported lazily *after* sys.path is extended, so it must not appear as
    a module-level import.
    """
    tree = ast.parse(ADDON.read_text())
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])
    import sys as _sys

    allowed = set(_sys.stdlib_module_names) | {"bpy"}
    assert not (top_level - allowed), f"add-on imports {top_level - allowed} at module level"


def test_addon_uses_a_modal_timer_not_a_thread() -> None:
    """bpy is not thread-safe; touching scene data off the main thread crashes Blender."""
    source = ADDON.read_text()
    assert "modal_handler_add" in source
    assert "event_timer_add" in source
    assert "threading" not in source


def test_addon_clears_without_resetting_the_file() -> None:
    """`reset()` calls read_factory_settings, which would throw away the user's open file."""
    source = ADDON.read_text()
    assert "scene_mod.clear()" in source
    assert "read_factory_settings" not in source
