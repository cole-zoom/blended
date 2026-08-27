"""Phase 1 ingestion tests, against the real `goal/dark-lancedb-logo.svg`.

Several of these are the first concrete implementations of assertions written in
`goal/acceptance.md`, so they double as the seed of the Tier-2 probe suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blended.assets.resolver import AssetCache, AssetError, AssetRequest
from blended.config import find_blender
from blended.engine.runner import run_job
from blended.errors import BlenderNotFoundError

LOGO = Path(__file__).resolve().parent.parent / "goal" / "dark-lancedb-logo.svg"

# `goal/` is gitignored — it holds machine-local source art. Skip rather than fail on a clone
# that does not have it, so a missing asset reads as "not applicable" and not as a broken build.
requires_logo = pytest.mark.skipif(not LOGO.exists(), reason="goal/ assets not present")


# --------------------------------------------------------------------------- resolver (no Blender)


@requires_logo
def test_vector_source_is_tier_a_and_not_lossy() -> None:
    request = AssetRequest(source=LOGO, name="logo")
    assert request.tier == "A"
    assert request.lossy is False


@requires_logo
def test_raster_source_is_flagged_lossy() -> None:
    """acceptance.md: `manifest.provenance.lossy == false` enforces 'use the SVG, not the PNG'."""
    request = AssetRequest(source=LOGO.with_name("reference_photo.png"), name="logo")
    assert request.tier == "E"
    assert request.lossy is True


def test_unimplemented_tier_fails_loudly(tmp_path: Path) -> None:
    """A tier we haven't built must error, not silently produce nothing."""
    fake = tmp_path / "model.glb"
    fake.write_bytes(b"stub")
    with pytest.raises(AssetError, match="not implemented"):
        AssetRequest(source=fake, name="model").validate()


def test_cache_key_tracks_contents_and_params(tmp_path: Path) -> None:
    src = tmp_path / "a.svg"
    src.write_text("<svg/>")
    base = AssetRequest(source=src, name="a", params={"extrude": 0.05})

    # Same source, same params -> hit.
    assert base.cache_key() == AssetRequest(source=src, name="a",
                                            params={"extrude": 0.05}).cache_key()
    # A geometry parameter changed -> miss.
    assert base.cache_key() != AssetRequest(source=src, name="a",
                                            params={"extrude": 0.06}).cache_key()

    # Editing the file must be a miss. Capture the key first: cache_key() re-reads the source
    # every call, so comparing two post-edit keys would compare the new contents to itself.
    before_edit = base.cache_key()
    src.write_text("<svg><path/></svg>")
    assert base.cache_key() != before_edit


def test_cache_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "a.svg"
    src.write_text("<svg/>")
    request = AssetRequest(source=src, name="a")
    cache = AssetCache(tmp_path / "cache")

    assert cache.lookup(request) is None
    stored = cache.store(request, {"size": [1, 1, 1]})
    assert stored.manifest["provenance"]["tier"] == "A"
    assert stored.manifest["provenance"]["lossy"] is False


# --------------------------------------------------------------------------- ingestion (Blender)

pytestmark_integration = pytest.mark.integration


@pytest.fixture(scope="module")
def ingested():
    if not LOGO.exists():
        pytest.skip("goal/ assets not present")
    try:
        find_blender()
    except BlenderNotFoundError:
        pytest.skip("Blender not installed")
    result = run_job(
        {
            "scene": {"kind": "logo_still", "source": str(LOGO)},
            "render": {"resolution": [64, 36], "frame_start": 1, "frame_end": 1, "output": ""},
        }
    )
    return result.stats


@pytest.fixture(scope="module")
def ingested_hull():
    """The geometry-based outline fallback, which has different invariants to Line Art."""
    if not LOGO.exists():
        pytest.skip("goal/ assets not present")
    try:
        find_blender()
    except BlenderNotFoundError:
        pytest.skip("Blender not installed")
    result = run_job(
        {
            "scene": {"kind": "logo_still", "source": str(LOGO), "outline_mode": "hull",
                      "outline_thickness": 0.015},
            "render": {"resolution": [64, 36], "frame_start": 1, "frame_end": 1, "output": ""},
        }
    )
    return result.stats


@pytest.mark.integration
def test_splits_marks_from_wordmark(ingested) -> None:
    """The SVG is a single <path>; the marks/wordmark gap must be found without a hardcode."""
    assert set(ingested["topology"]) == {"logo_marks", "logo_text"}
    # The real gap is ~8x wider than the largest inter-letter gap, so detection is unambiguous.
    assert ingested["split_gap"] > 0.02
    assert 0.05 < ingested["split_x"] < 0.09


@pytest.mark.integration
def test_evenodd_survived_as_real_holes(ingested) -> None:
    """acceptance.md: counters in a/e/D/B must be holes, not filled blobs.

    `holes = loose_parts - euler_characteristic`. If the fill rule had been lost during
    curve->mesh conversion every part would be simply connected and this would read 0.
    """
    assert ingested["topology"]["logo_text"]["holes"] > 0
    assert ingested["topology"]["logo_marks"]["holes"] > 0


@pytest.mark.integration
def test_geometry_is_manifold(ingested) -> None:
    """Non-manifold geometry makes the outline's inflation produce garbage."""
    for name, report in ingested["topology"].items():
        assert report["is_manifold"], f"{name} has {report['non_manifold_edges']} bad edges"


@pytest.mark.integration
def test_normalization_contract(ingested) -> None:
    asset = ingested["asset"]
    assert asset["up_axis"] == "Z"
    assert asset["front_axis"] == [0.0, -1.0, 0.0]
    assert max(asset["size"]) == pytest.approx(1.0, abs=1e-4)
    # Source viewBox is 857x200 = 4.285:1; the bevel rounds it off slightly.
    assert asset["aspect"] == pytest.approx(4.2, abs=0.2)


@pytest.mark.integration
def test_logo_is_genuinely_3d(ingested) -> None:
    """acceptance.md: real Z extent, not a decal."""
    assert ingested["asset"]["depth_ratio"] > 0.02


@pytest.mark.integration
def test_lineart_outline_produces_strokes(ingested) -> None:
    """Line Art can silently produce nothing — wrong source, no camera, everything filtered —
    and the render just comes back with no outline. Assert strokes actually exist."""
    outline = ingested["outline"]
    assert outline["mode"] == "lineart"
    assert outline["present"], "Line Art generated no strokes"
    assert outline["stroke_points"] > 1000


@pytest.mark.integration
def test_geometry_outline_is_larger_but_shallower(ingested_hull) -> None:
    """For the geometry-based fallback, the outline must protrude in-plane (so it shows) while
    staying inset in depth (so it never occludes the logo's face or z-fights with it).

    The depth gap is also what causes parallax off-axis, so it is kept as small as z-fighting
    allows — see the comment in scene._build_logo_still.
    """
    outline = ingested_hull["outline"]
    assert outline["wider"], "outline does not extend past the logo"
    assert outline["shallower"], "outline is not inset in depth"
    depth = ingested_hull["asset"]["size"][1]
    assert outline["depth_gap"] / depth < 0.08, "depth gap large enough to parallax off-axis"


# ------------------------------------------------------------------------------------ textures


def test_texture_paths_are_absolute(tmp_path: Path) -> None:
    """Regression: relative texture paths render as flat magenta with no error.

    Blender re-resolves relative paths against the saved .blend's directory, not the working
    directory. A path like `.cache/polyhaven/...` therefore breaks the moment a .blend is
    written anywhere else — and a failed image load is silently drawn as magenta.
    """
    from blended.assets.textures import TextureSet

    # Exercise the path-building contract without hitting the network.
    directory = (tmp_path / "rel").resolve()
    texture_set = TextureSet("x", "2k", directory, {"diffuse": "diffuse.jpg"})
    assert Path(texture_set.path("diffuse")).is_absolute()


def test_resolve_textures_is_a_noop_without_a_texture() -> None:
    """A scene with no `texture` must not touch the network at all."""
    from blended.project import resolve_textures

    ir = {"environment": {"floor": {"enabled": True, "material": "stone"}}}
    resolved = resolve_textures(ir, Path("/nonexistent"))
    assert "texture_set" not in resolved["environment"]["floor"]
