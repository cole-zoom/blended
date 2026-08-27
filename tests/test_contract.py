"""The authoring contract: schemas, scaffold, and the docs that describe them.

Phase 4 exists so a second project does not require reading the source. These tests guard the
two ways that promise breaks: a schema that drifts from the code it describes, and a scaffold
that does not actually work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blended.contract import (
    SCHEMAS,
    library_schema,
    scaffold,
    scene_schema,
    stage_schema,
    validate_scaffold,
    write_schemas,
)
from blended.ir.scene import SceneIR
from blended.library import registry
from blended.stages import STAGE_ORDER, STAGES
from blended.verify.static import check

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "authoring.md"


# --------------------------------------------------------------------------------- generation


def test_schemas_are_generated_not_maintained() -> None:
    """Generated from the live models, so the published contract cannot drift from the code."""
    scene = scene_schema()
    assert scene["title"] == "blended Scene IR"
    # Fields the compiler actually reads must be describable.
    for key in ("timeline", "assets", "tracks", "camera", "lights", "world", "environment"):
        assert key in scene["properties"], key


def test_library_schema_lists_every_action_with_its_footprint() -> None:
    """The footprint is what an author is least likely to guess and most needs: it determines
    which tracks can coexist."""
    published = library_schema()["actions"]
    assert set(published) == set(registry.ACTIONS)
    for name, entry in published.items():
        action = registry.ACTIONS[name]
        assert entry["writes"] == list(action.writes)
        assert entry["accepts"] == list(action.accepts)
        assert "properties" in entry["params"] or entry["params"].get("type") == "object"


def test_stage_schema_matches_the_pipeline() -> None:
    published = stage_schema()
    assert published["order"] == list(STAGE_ORDER)
    for name, entry in published["stages"].items():
        assert entry["owns"] == list(STAGES[name].owns)
        assert entry["question"] == STAGES[name].question


def test_write_schemas_emits_valid_json(tmp_path: Path) -> None:
    written = write_schemas(tmp_path)
    assert {p.name for p in written} == set(SCHEMAS)
    for path in written:
        json.loads(path.read_text())  # raises if malformed


# ----------------------------------------------------------------------------------- scaffold


def test_scaffold_validates_against_the_schema() -> None:
    scene = validate_scaffold(scaffold("demo", "logo.svg"))
    assert isinstance(scene, SceneIR)


def test_scaffold_passes_tier_1() -> None:
    """A scaffold that fails its own checker is worse than no scaffold."""
    report = check(validate_scaffold(scaffold("demo", "logo.svg")))
    assert report.ok, [d.message for d in report.errors]


def test_scaffold_is_not_empty() -> None:
    """A blank template makes you look up every field before seeing anything at all. This one
    already moves, so the first blocking render gives you something to react to."""
    scene = validate_scaffold(scaffold("demo", "logo.svg"))
    actions = {t.action for t in scene.tracks}
    assert "camera.orbit" in actions
    assert "light.ramp" in actions
    assert scene.lights, "a scaffold with no lights renders black"


def test_scaffold_quantizes_without_drift() -> None:
    scene = validate_scaffold(scaffold("demo", "logo.svg"))
    assert scene.timeline.drift == 0.0


@pytest.mark.parametrize("duration,fps", [(8.0, 30), (16.0, 24), (12.0, 60), (4.0, 25)])
def test_scaffold_accepts_common_timings(duration: float, fps: int) -> None:
    scene = validate_scaffold(scaffold("d", "l.svg", duration=duration, fps=fps))
    assert check(scene).ok
    assert scene.timeline.drift == 0.0


def test_scaffold_avoids_the_known_traps() -> None:
    """Defaults must not walk into failures the docs warn about."""
    data = scaffold("demo", "logo.svg")
    # post.bloom raises in the Blender 5.2 backend.
    assert data.get("post", {}).get("bloom", 0.0) == 0.0
    # Wetness without an environment to reflect just looks dark; the scaffold ships neither.
    assert data["environment"]["floor"]["enabled"] is False


# --------------------------------------------------------------------------------------- docs


def test_authoring_guide_exists_and_covers_the_traps() -> None:
    """The guide's value is the non-obvious parts; assert they are actually in it."""
    text = DOCS.read_text().lower()
    for topic in ("post.bloom", "channel", "wet_flatten", "lineart", "hdri",
                  "cycles", "resumable", "drift"):
        assert topic in text, f"authoring guide does not mention {topic}"


def test_authoring_guide_documents_every_stage() -> None:
    text = DOCS.read_text()
    for name in STAGE_ORDER:
        assert name in text, f"authoring guide does not mention the {name} stage"


def test_no_intent_layer_was_added() -> None:
    """The roadmap made an L1 semantic IR conditional on Phase 2 showing a need for it, and
    Phase 2 did not. This asserts the decision stayed made rather than drifting back in."""
    assert not (ROOT / "src" / "blended" / "ir" / "intent.py").exists()
    assert not (ROOT / "src" / "blended" / "ir" / "lower.py").exists()
