"""The authoring contract: schemas and scaffolds. Host side.

Phase 4 exists so a *second* project does not require reading the source. Everything here is
generated from the same pydantic models and registry the compiler uses, so the published
contract cannot drift from the thing it describes — a hand-maintained schema would be wrong
within a week.

Deliberately **no L1 "intent" IR.** The roadmap made it conditional on Phase 2 showing a real
need for a semantic layer above Scene IR, and Phase 2 did not: hand-authoring Scene IR was
comfortable, the LanceDB scene is under 1,800 characters, and every edit made during production
was a direct field change. A semantic layer would be indirection solving a problem never hit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blended.ir.scene import SceneIR
from blended.library import registry
from blended.stages import STAGE_ORDER, STAGES


def scene_schema() -> dict[str, Any]:
    """JSON Schema for a scene file, straight from the pydantic model."""
    schema = SceneIR.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "blended Scene IR"
    schema["description"] = (
        "A compiled animation. Authored by hand or by an agent; never contains bpy. "
        "Becomes the source of truth once a build succeeds — edits are patches, not "
        "regenerations."
    )
    return schema


def library_schema() -> dict[str, Any]:
    """Every action, its parameters, and its channel footprint.

    The footprint is the part an authoring agent most needs and is least likely to guess: two
    tracks writing the same channel over overlapping frames is a compile error, so knowing what
    an action writes is knowing what it conflicts with.
    """
    actions = {}
    for name, action in sorted(registry.ACTIONS.items()):
        actions[name] = {
            "params": action.params.model_json_schema(),
            "writes": list(action.writes),
            "timing": action.timing,
            "accepts": list(action.accepts),
            "tags": list(action.tags),
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "blended action library",
        "description": (
            "The closed vocabulary a scene composes from. `writes` lists the channels an "
            "action animates; two tracks writing the same channel of the same target over "
            "overlapping frames is a CHANNEL_CONFLICT."
        ),
        "actions": actions,
    }


def stage_schema() -> dict[str, Any]:
    """What each stage owns, suppresses and renders."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "blended pipeline stages",
        "description": (
            "Five stages, each answering one question and ending at a human gate. `owns` is "
            "the set of IR paths that stage is responsible for — approving a stage freezes "
            "exactly those, and later changes to them warn."
        ),
        "order": list(STAGE_ORDER),
        "stages": {
            name: {
                "question": stage.question,
                "owns": list(stage.owns),
                "suppresses": list(stage.suppress),
                "engine": stage.engine,
                "media": stage.media,
                "resolution": list(stage.resolution),
                "probes": list(stage.probes),
            }
            for name, stage in STAGES.items()
        },
    }


SCHEMAS = {
    "scene.schema.json": scene_schema,
    "library.json": library_schema,
    "stages.json": stage_schema,
}


def write_schemas(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, build in SCHEMAS.items():
        path = out_dir / filename
        path.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


# ------------------------------------------------------------------------------------ scaffold


def scaffold(name: str, asset_source: str, *, duration: float = 8.0, fps: int = 30) -> dict:
    """A minimal scene that renders something sensible on the first try.

    Deliberately not empty. A blank template makes you look up every field before seeing
    anything at all; this one has a camera move and a light ramp already wired, so the first
    `blended stage blocking` produces motion you can react to. Editing beats authoring.

    Defaults are the ones the LanceDB piece converged on after a lot of iteration — an elevated
    partial arc rather than a full turntable, and a light that ramps rather than sits flat.
    """
    return {
        "version": 1,
        "name": name,
        "timeline": {"duration": duration, "fps": fps},
        "assets": [
            {
                "id": "logo",
                "source": asset_source,
                "extrude": 0.05,
                "bevel": 0.004,
                "base_color": [0.9, 0.9, 0.92, 1.0],
                "roughness": 0.35,
                "outline": {"mode": "none", "thickness": 0.0},
            }
        ],
        "camera": {"lens": 50.0, "margin": 1.25},
        "lights": [
            {
                "id": "key",
                "type": "spot",
                "energy": 400.0,
                "azimuth": -40.0,
                "elevation": 35.0,
                "distance": 4.5,
                "spot_size": 50.0,
            }
        ],
        "world": {"color": [0.02, 0.02, 0.025, 1.0]},
        "environment": {"floor": {"enabled": False}, "volumetrics": {"enabled": False}},
        "tracks": [
            {
                "action": "camera.orbit",
                "target": "camera",
                "start": 0.0,
                "duration": duration,
                "params": {
                    "start_azimuth": -35.0,
                    "end_azimuth": 20.0,
                    "start_elevation": 8.0,
                    "end_elevation": 22.0,
                    "easing": "ease_out",
                },
            },
            {
                "action": "light.ramp",
                "target": "key",
                "start": 0.0,
                "duration": duration,
                "params": {"start_energy": 40.0, "end_energy": 400.0,
                           "easing": "ease_in_out"},
            },
        ],
    }


def validate_scaffold(data: dict) -> SceneIR:
    """A scaffold that does not pass its own checker is worse than no scaffold."""
    return SceneIR(**data)
