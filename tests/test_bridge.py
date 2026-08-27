"""Integration tests for the Blender bridge. These actually launch Blender."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from blended.config import ENV_VAR, find_blender
from blended.engine.runner import RunOptions, run_job
from blended.errors import BackendError, BlenderNotFoundError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def blender():
    try:
        return find_blender()
    except BlenderNotFoundError:
        pytest.skip("Blender not installed")


def test_discovers_blender(blender) -> None:
    assert blender.path.exists()
    assert blender.version[:2] >= (4, 2)


def test_explicit_override_is_authoritative(monkeypatch) -> None:
    """A bad override must fail, not silently fall back to a different Blender."""
    find_blender.cache_clear()
    monkeypatch.setenv(ENV_VAR, "/definitely/not/blender")
    with pytest.raises(BlenderNotFoundError, match="does not exist"):
        find_blender()
    find_blender.cache_clear()


def test_round_trip_builds_scene(blender) -> None:
    result = run_job(
        {
            "scene": {"kind": "demo_cube", "frame_start": 1, "frame_end": 1},
            "render": {"resolution": [64, 36], "frame_start": 1, "frame_end": 1, "output": ""},
        }
    )
    assert result.ok
    assert result.blender_version.startswith("5.") or result.blender_version.startswith("4.")
    assert result.stats["engine"] == "BLENDER_EEVEE"
    assert result.stats["cube"] == "demo_cube"


def test_backend_failure_is_structured(blender) -> None:
    """A backend exception must arrive as a diagnostic with a traceback, not a crash."""
    with pytest.raises(BackendError) as exc_info:
        run_job({"scene": {"kind": "does_not_exist"}, "render": {"output": ""}})

    err = exc_info.value
    assert "does_not_exist" in err.message
    assert err.traceback_text and "ValueError" in err.traceback_text
    assert err.log_path  # workdir preserved on failure for inspection


def test_renders_video_with_exact_duration(blender, tmp_path: Path) -> None:
    """Frame count and fps must land exactly — the timing guarantee from ARCHITECTURE §3."""
    frames, fps = 30, 30
    out = tmp_path / "spin.mp4"
    result = run_job(
        {
            "blend_out": str(tmp_path / "spin.blend"),
            "scene": {"kind": "demo_cube", "frame_start": 1, "frame_end": frames},
            "render": {
                "resolution": [128, 72],
                "fps": fps,
                "frame_start": 1,
                "frame_end": frames,
                "samples": 1,
                "media": "video",
                "output": str(out),
            },
        },
        RunOptions(timeout_s=300),
    )

    assert result.ok
    assert result.stats["frames"] == frames
    # Exact filename, with no Blender-appended `0001-0030` frame range.
    assert out.exists(), f"expected {out}, found {list(tmp_path.iterdir())}"
    assert (tmp_path / "spin.blend").exists()

    data = out.read_bytes()
    assert b"avcC" in data, "not H.264"
    i = data.find(b"mvhd")
    timescale, duration = struct.unpack(">II", data[i + 16 : i + 24])
    assert abs(duration / timescale - frames / fps) < 0.05
