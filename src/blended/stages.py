"""The staged pipeline (ARCHITECTURE §11a).

Five stages, each answering one question and ending at a human gate. Two ideas do the work:

**Render fidelity matches the decision.** Judging camera timing does not need materials, and
paying eighty minutes to discover the camera is wrong is the failure this exists to prevent.

**Each stage suppresses what is not yet being decided.** `blocking` forces clay materials *even
when real ones exist*, because a finished-looking frame makes you judge the look and miss that the
timing is wrong. `materials` renders under a fixed neutral light so "is that concrete, or just the
orange HDRI?" cannot arise. Suppression is a feature.

Every IR field belongs to exactly one stage. That mapping is what makes "has this stage changed?"
answerable, and therefore what makes approval gates meaningful.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: Ordered. Later stages depend on earlier ones being settled.
STAGE_ORDER = ("assets", "blocking", "materials", "lighting", "final")


# --------------------------------------------------------------------------------- field paths


def _resolve(ir: dict, path: str) -> Any:
    """Read one ownership path out of an IR dict.

    Supported forms:
        "timeline"            whole subtree
        "camera.lens"         nested key
        "assets[].source"     that field of every list element
        "environment.floor.texture"
    """
    if "[]" in path:
        head, _, tail = path.partition("[]")
        items = _resolve(ir, head.rstrip(".")) or []
        if not isinstance(items, list):
            return None
        key = tail.lstrip(".")
        return [_resolve(item, key) if key else item for item in items]

    node: Any = ir
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def owned_subset(ir: dict, stage: Stage) -> dict:
    """The portion of the IR a stage is responsible for, for hashing and diffing."""
    return {path: _resolve(ir, path) for path in stage.owns}


def fingerprint(ir: dict, stage: Stage) -> str:
    """Stable hash of a stage's owned fields.

    `sort_keys` matters: dict ordering must not change the hash, or every reload would look
    like drift.
    """
    payload = json.dumps(owned_subset(ir, stage), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def changed_paths(ir: dict, other: dict, stage: Stage) -> list[str]:
    """Which of a stage's owned paths differ between two IRs. Drives the drift message."""
    a, b = owned_subset(ir, stage), owned_subset(other, stage)
    return sorted(p for p in stage.owns if a.get(p) != b.get(p))


# -------------------------------------------------------------------------------- suppressions

#: Neutral clay. Mid grey with plain roughness — no colour, no metal, nothing to admire.
CLAY = {
    "material": "plain",
    "base_color": [0.55, 0.55, 0.55, 1.0],
    "roughness": 0.5,
    "metallic": 0.0,
    "wear": 0.0,
    "wetness": 0.0,
    "droplets": 0.0,
}

#: A fixed, neutral three-quarter key plus fill. Materials are judged against this and never
#: against the scene's own lighting, so surface and mood stay separable decisions.
REFERENCE_LIGHTS = [
    {"id": "ref_key", "type": "area", "energy": 400.0, "azimuth": -35.0,
     "elevation": 30.0, "distance": 4.0, "size": 4.0, "color": [1.0, 1.0, 1.0]},
    {"id": "ref_fill", "type": "area", "energy": 120.0, "azimuth": 55.0,
     "elevation": 15.0, "distance": 4.0, "size": 5.0, "color": [1.0, 1.0, 1.0]},
]

REFERENCE_WORLD = {"color": [0.18, 0.18, 0.19, 1.0], "strength": 1.0}


def suppress_materials(ir: dict) -> dict:
    """Clay everything, and drop the outline. Judge form, not finish."""
    for asset in ir.get("assets", []):
        asset.update(copy.deepcopy(CLAY))
        asset["outline"] = {"mode": "none", "thickness": 0.0}
    floor = (ir.get("environment") or {}).get("floor")
    if floor:
        floor.update({"texture": None, "texture_set": None, "wetness": 0.0, "ripples": 0.0})
    return ir


def suppress_environment(ir: dict) -> dict:
    """No floor, no fog. The subject alone against the void."""
    env = ir.setdefault("environment", {})
    if env.get("floor"):
        env["floor"]["enabled"] = False
    env["volumetrics"] = {"enabled": False}
    return ir


def suppress_lighting(ir: dict) -> dict:
    """Swap the scene's lighting for the neutral reference rig."""
    ir["lights"] = copy.deepcopy(REFERENCE_LIGHTS)
    ir["world"] = copy.deepcopy(REFERENCE_WORLD)
    ir["post"] = {"bloom": 0.0}
    # Light tracks now target lights that no longer exist.
    ir["tracks"] = [t for t in ir.get("tracks", []) if not t["action"].startswith("light.")]
    return ir


def suppress_post(ir: dict) -> dict:
    """Drop compositing. It belongs to `lighting`, and any stage before that must not inherit it.

    Also load-bearing: `post.bloom` raises in the backend on Blender 5.2, so a scene with bloom
    enabled would crash at blocking rather than merely looking wrong.
    """
    ir["post"] = {"bloom": 0.0}
    return ir


def turntable(ir: dict, revolutions: float = 1.0) -> dict:
    """Replace the scene's camera move with a full orbit, for inspecting geometry.

    The authored camera move is designed to flatter the subject; a turntable is designed to
    expose it. Different jobs.
    """
    ir["tracks"] = [t for t in ir.get("tracks", []) if not t["action"].startswith("camera.")]
    ir["tracks"].append({
        "action": "camera.orbit",
        "target": ir.get("camera", {}).get("id", "camera"),
        "start": 0.0,
        "duration": ir["timeline"]["duration"],
        "params": {"start_azimuth": 0.0, "end_azimuth": 360.0 * revolutions,
                   "start_elevation": 18.0, "end_elevation": 18.0, "easing": "linear"},
    })
    return ir


SUPPRESSIONS = {
    "materials": suppress_materials,
    "environment": suppress_environment,
    "lighting": suppress_lighting,
    "post": suppress_post,
    "turntable": turntable,
}


# --------------------------------------------------------------------------------------- stages


@dataclass(frozen=True)
class Stage:
    name: str
    question: str
    #: IR paths this stage owns. Exactly one stage owns each field.
    owns: tuple[str, ...]
    #: Applied in order to a *copy* of the IR before building.
    suppress: tuple[str, ...] = ()
    engine: str = "eevee"
    #: "stills" | "video" | "sequence". `sequence` renders resumable PNGs then encodes, and is
    #: the right choice for anything long enough that losing it would hurt.
    media: str = "stills"
    resolution: tuple[int, int] = (960, 540)
    samples: int = 16
    #: Render every Nth frame. Stills stages sample; motion stages must use 1.
    frame_step: int = 1
    probes: tuple[str, ...] = ()

    def apply(self, ir: dict) -> dict:
        out = copy.deepcopy(ir)
        for name in self.suppress:
            out = SUPPRESSIONS[name](out)
        return out


STAGES: dict[str, Stage] = {
    s.name: s
    for s in (
        Stage(
            name="assets",
            question="Is the model right?",
            owns=("assets[].id", "assets[].source", "assets[].extrude", "assets[].bevel",
                  "assets[].resolution", "assets[].target_size"),
            suppress=("environment", "materials", "lighting", "post", "turntable"),
            media="stills",
            resolution=(720, 720),
            frame_step=40,
            probes=("geometry",),
        ),
        Stage(
            name="blocking",
            question="Does the motion and timing work?",
            # Floor presence and size are composition, not surface — they change what the
            # frame contains, so they settle with the camera rather than with the materials.
            owns=("timeline", "camera", "tracks",
                  "lights[].id", "lights[].type", "lights[].azimuth",
                  "lights[].elevation", "lights[].distance", "lights[].spot_size",
                  "environment.floor.enabled", "environment.floor.size",
                  "environment.floor.offset"),
            suppress=("environment", "materials", "post"),
            media="video",
            resolution=(640, 360),
            samples=8,
            probes=("framing", "motion"),
        ),
        Stage(
            name="materials",
            question="What is everything made of?",
            owns=("assets[].material", "assets[].base_color", "assets[].roughness",
                  "assets[].metallic", "assets[].wear", "assets[].wetness",
                  "assets[].droplets", "assets[].outline",
                  "environment.floor.material", "environment.floor.texture",
                  "environment.floor.texture_resolution", "environment.floor.scale",
                  "environment.floor.wetness", "environment.floor.bump",
                  "environment.floor.wet_roughness", "environment.floor.wet_flatten",
                  "environment.floor.ripples", "environment.floor.ripple_scale",
                  "environment.floor.ripple_speed", "environment.floor.ripple_detail"),
            suppress=("lighting",),
            media="stills",
            resolution=(1280, 720),
            samples=32,
            frame_step=120,
            probes=("materials",),
        ),
        Stage(
            name="lighting",
            question="Is the mood right?",
            owns=("lights[].energy", "lights[].color", "lights[].radius",
                  "lights[].spot_blend", "lights[].angle", "lights[].size",
                  "world", "environment.volumetrics", "post"),
            media="video",
            resolution=(1280, 720),
            samples=24,
            probes=("framing", "light"),
        ),
        Stage(
            name="final",
            question="Ship it",
            owns=(),
            engine="cycles",
            # The only stage long enough for interruption to be expensive.
            media="sequence",
            resolution=(1920, 1080),
            samples=128,
            probes=("framing", "motion", "light", "materials"),
        ),
    )
}


def get(name: str) -> Stage:
    try:
        return STAGES[name]
    except KeyError:
        raise KeyError(f"unknown stage {name!r} (known: {', '.join(STAGE_ORDER)})") from None


def upto(name: str) -> list[Stage]:
    """Every stage from the first through `name`, in order."""
    index = STAGE_ORDER.index(name)
    return [STAGES[n] for n in STAGE_ORDER[: index + 1]]


def leaf_paths(node: Any, prefix: str = "") -> list[str]:
    """Every addressable leaf in an IR, in the same syntax as ownership paths.

    Lists of dicts collapse to `key[].subkey` because ownership is per-field, not per-element —
    a scene with two assets does not need two ownership entries.
    """
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.extend(leaf_paths(value, f"{prefix}.{key}" if prefix else key))
        return out
    if isinstance(node, list) and node and isinstance(node[0], dict):
        merged: list[str] = []
        for item in node:
            for path in leaf_paths(item, f"{prefix}[]"):
                if path not in merged:
                    merged.append(path)
        return merged
    return [prefix]


def unowned_paths(ir: dict) -> list[str]:
    """IR fields no stage claims — a safety net against silent gaps in ownership.

    Walks to the leaves rather than stopping at top-level keys. A shallow check passes as soon
    as *some* stage mentions `environment`, which would hide the fact that nothing owns
    `environment.floor.size` and changes to it are therefore never flagged.
    """
    owned = [p for stage in STAGES.values() for p in stage.owns]
    skip = {"version", "name"}

    def covered(leaf: str) -> bool:
        # An ownership path covers a leaf if it is that leaf or a prefix of it: owning
        # `timeline` owns `timeline.duration`, and owning `tracks` owns `tracks[].action`.
        # Both separators matter — `.` for nested keys and `[]` for lists of records.
        return any(
            leaf == o or leaf.startswith(o + ".") or leaf.startswith(o + "[]")
            for o in owned
        )

    return sorted(
        leaf for leaf in leaf_paths(ir)
        if leaf.split(".")[0].split("[")[0] not in skip and not covered(leaf)
    )


@dataclass
class StageResult:
    stage: str
    approved: bool
    drifted: bool
    changed: list[str] = field(default_factory=list)
    fingerprint: str = ""
