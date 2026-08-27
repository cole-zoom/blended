"""Tier 3: preparing a render for review. Host side.

The vision tier from ARCHITECTURE §8, in the form that fits how this project actually works.
There is no API call here and no model in the loop — the agent reviewing the render is the one
already in your terminal, and it can read a PNG.

So this module's job is to produce the *artefact* worth looking at and the *questions* worth
asking, then get out of the way:

  * a contact sheet, so a whole shot is one image instead of hundreds
  * a checklist derived from the scene itself, so the review is against stated intent rather
    than vague taste
  * the Tier-2 probe results, so anything measurable is already answered and does not waste a
    judgement call

The rule from ARCHITECTURE §8 still holds: Tier 3 is only asked what Tiers 1 and 2 structurally
cannot answer. "Is the logo in frame" is a probe. "Does this feel premium" is a look.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from blended.project import load
from blended.stages import get as get_stage


@dataclass
class ReviewItem:
    question: str
    #: What the scene claims, so the reviewer compares against intent rather than guessing.
    intent: str
    #: Filled by Tier 2 where it can be. `None` means only a human or vision pass can answer.
    measured: str | None = None


@dataclass
class ReviewPacket:
    scene: str
    stage: str
    frames: list[Path] = field(default_factory=list)
    contact_sheet: Path | None = None
    probe_report: Path | None = None
    items: list[ReviewItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scene": self.scene,
            "stage": self.stage,
            "contact_sheet": str(self.contact_sheet) if self.contact_sheet else None,
            "probe_report": str(self.probe_report) if self.probe_report else None,
            "frames": [str(f) for f in self.frames],
            "checklist": [
                {"question": i.question, "intent": i.intent, "measured": i.measured}
                for i in self.items
            ],
        }


def checklist(scene_file: Path, stage: str, probes: dict | None = None) -> list[ReviewItem]:
    """Build the questions worth asking, from what the scene actually declares.

    Derived rather than fixed: a scene with no floor should not be asked about its floor, and a
    scene with a light ramp should be asked whether the ramp reads.
    """
    scene = load(scene_file)
    probes = probes or {}
    items: list[ReviewItem] = []

    def measured(probe: str, fn) -> str | None:
        data = probes.get(probe)
        if not isinstance(data, dict) or "error" in data:
            return None
        try:
            return fn(data)
        except (KeyError, IndexError, TypeError, ZeroDivisionError):
            return None

    if stage in ("assets",):
        items.append(ReviewItem(
            "Is the geometry right — proportions, bevel, holes where they belong?",
            f"{len(scene.assets)} asset(s) from {Path(scene.assets[0].source).name}",
            measured("geometry", lambda d: f"{len(d['objects'])} parts, "
                                           f"{d['total_holes']} holes, "
                                           f"manifold={d['manifold']}"),
        ))

    if stage in ("blocking", "lighting", "final"):
        orbit = next((t for t in scene.tracks if t.action == "camera.orbit"), None)
        if orbit:
            params = orbit.params
            items.append(ReviewItem(
                "Does the camera move feel right — speed, arc, where it settles?",
                f"{params.get('start_azimuth')}° → {params.get('end_azimuth')}°, "
                f"elevation {params.get('start_elevation')}° → {params.get('end_elevation')}°, "
                f"{params.get('easing')}",
                measured("motion", lambda d: f"sweep {d['azimuth_sweep']}°, "
                                             f"distance {d['distance_min']}→{d['distance_max']}"),
            ))
        items.append(ReviewItem(
            "Is the subject well framed throughout — never lost, never crowded?",
            f"{scene.timeline.duration}s at {scene.timeline.fps}fps",
            measured("framing", lambda d: f"coverage {d['min_coverage']:.1%}–"
                                          f"{d['max_coverage']:.1%}"),
        ))

    ramp = next((t for t in scene.tracks if t.action == "light.ramp"), None)
    if ramp and stage in ("blocking", "lighting", "final"):
        items.append(ReviewItem(
            "Does the light ramp read — does it build, and peak where intended?",
            f"{ramp.params.get('start_energy')}W → {ramp.params.get('end_energy')}W "
            f"over {ramp.duration}s, peaking at {ramp.end}s",
            measured("light", lambda d: "; ".join(
                f"{n}: {e['energy_start']:.0f}→{e['energy_max']:.0f}W peak@f{e['peak_frame']}"
                for n, e in d["lights"].items())),
        ))

    if stage in ("materials", "lighting", "final"):
        asset = scene.assets[0]
        items.append(ReviewItem(
            "Does the subject read as a real material rather than plastic?",
            f"{asset.material}, roughness {asset.roughness}, metallic {asset.metallic}, "
            f"wetness {asset.wetness}",
            measured("materials", lambda d: f"{sum(1 for i in d['images'].values() if i.get('exists'))}"
                                            f"/{len(d['images'])} textures resolved"),
        ))
        floor = scene.environment.floor
        if floor and floor.enabled:
            items.append(ReviewItem(
                "Does the ground read correctly — scale, wetness, reflections?",
                f"{floor.texture or floor.material}, wetness {floor.wetness}, "
                f"scale {floor.scale}",
                None,
            ))

    if stage in ("lighting", "final"):
        items.append(ReviewItem(
            "Does the mood land — is it the image you wanted?",
            f"world {list(scene.world.color[:3])}"
            + (f", hdri {scene.world.hdri}" if scene.world.hdri else ""),
            None,  # the one question no probe can answer
        ))

    return items


def find_frames(renders_dir: Path, scene_name: str, stage: str) -> list[Path]:
    """Rendered stills for a stage, from either a stills pass or a frame sequence."""
    renders_dir = Path(renders_dir)
    stem = f"{scene_name}_{stage}"
    frames = sorted(renders_dir.glob(f"{stem}_[0-9]*.png"))
    if not frames:
        frames = sorted((renders_dir / f"{stem}_frames").glob("*.png"))
    return frames


def prepare(scene_file: Path, stage: str, renders_dir: Path | None = None) -> ReviewPacket:
    """Assemble everything needed to review a stage. Renders nothing."""
    scene_file = Path(scene_file)
    scene = load(scene_file)
    renders = Path(renders_dir) if renders_dir else scene_file.parent / "renders"

    probe_file = renders / f"{scene.name}_{stage}_probes.json"
    probes = {}
    if probe_file.exists():
        try:
            probes = json.loads(probe_file.read_text()).get("probes", {})
        except (OSError, ValueError):
            probes = {}

    get_stage(stage)  # raises on an unknown stage name
    return ReviewPacket(
        scene=scene.name,
        stage=stage,
        frames=find_frames(renders, scene.name, stage),
        probe_report=probe_file if probe_file.exists() else None,
        items=checklist(scene_file, stage, probes),
    )
