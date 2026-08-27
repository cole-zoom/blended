"""Tier-2 assertions: turning probe facts into verdicts. Host side.

The backend *measures* (`blended_backend.probes`); this module *judges*. Keeping them apart
matters because whether a fact is a failure depends on intent, and intent differs per stage —
`blocking` cares that the subject stays in frame, `materials` cares that textures loaded.

Every check carries the reason it exists, because a threshold with no rationale is a threshold
nobody dares change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from blended.verify.static import Diagnostic


@dataclass(frozen=True)
class Check:
    code: str
    probe: str
    #: Returns None if satisfied, or a message describing the failure.
    test: Callable[[dict, dict], str | None]
    severity: str = "error"
    hint: str | None = None
    #: Stages this check applies to. Empty means every stage that ran the probe.
    stages: tuple[str, ...] = ()


def _get(report: dict, probe: str) -> dict | None:
    data = report.get(probe)
    if not isinstance(data, dict) or "error" in data:
        return None
    return data


# ------------------------------------------------------------------------------------ geometry


def _manifold(data, ir):
    if not data.get("manifold", True):
        bad = [n for n, o in data.get("objects", {}).items() if not o["is_manifold"]]
        return f"non-manifold geometry in {', '.join(bad)}"
    return None


def _has_holes(data, ir):
    # The LanceDB wordmark has counters in a/e/D/B. Zero holes means the SVG fill rule was lost
    # during curve->mesh conversion and the letters filled solid — subtle enough to miss at
    # draft resolution, which is exactly why it is asserted.
    if data.get("total_holes", 0) == 0:
        return "no holes found — an SVG fill rule may have been lost in conversion"
    return None


def _has_depth(data, ir):
    ratio = data.get("depth_ratio")
    if ratio is not None and ratio < 0.02:
        return f"depth ratio {ratio} — the subject is effectively flat, not 3D"
    return None


# ------------------------------------------------------------------------------------- framing


def _ever_visible(data, ir):
    # The real failure is a subject you never actually see. A subject that is briefly huge or
    # briefly small is composition, not a bug, so only the best moment is judged here.
    best = data.get("max_coverage", 1.0)
    if best < 0.05:
        return (f"subject never covers more than {best:.1%} of frame — "
                "it is effectively invisible for the whole shot")
    return None


def _mostly_in_frame(data, ir):
    """Warn, do not fail, on overflow.

    A camera that starts inches from the subject overflows the frame deliberately. Erroring on
    that trains you to ignore the checker, so the threshold is *sustained* absence: more than
    half the sampled frames.
    """
    samples = data.get("samples", [])
    if not samples:
        return None
    off = [s["frame"] for s in samples if not s["in_frame"]]
    if len(off) > len(samples) * 0.5:
        return (f"subject overflows the frame in {len(off)}/{len(samples)} sampled frames "
                f"— intentional for a close opening, a bug otherwise")
    return None


def _stays_in_front(data, ir):
    # Centre-based: the camera has genuinely passed through the subject, not merely clipped a
    # corner of its bounding box.
    if not data.get("always_in_front", True):
        return "camera passes through the subject — its centre falls behind the lens"
    return None


# -------------------------------------------------------------------------------------- motion


def _orbit_is_circular(data, ir):
    # A pivot-based orbit holds distance constant by construction. Meaningful variation means
    # the camera is doing something other than orbiting — worth knowing, not always wrong.
    variation = data.get("distance_variation", 0.0)
    if variation > 0.9:
        return f"camera distance varies by {variation:.0%} — not an orbit"
    return None


def _azimuth_progresses(data, ir):
    if data.get("azimuth_sweep", 0.0) < 1.0:
        return "camera azimuth barely changes — there is no orbit"
    return None


# --------------------------------------------------------------------------------------- light


def _lights_aim_at_subject(data, ir):
    stray = [
        name for name, entry in data.get("lights", {}).items()
        if entry.get("aim_alignment") is not None and entry["aim_alignment"] < 0.2
    ]
    if stray:
        return (f"light(s) {', '.join(stray)} point away from the subject — "
                "the scene will render dark with no error")
    return None


def _lights_are_on(data, ir):
    dead = [n for n, e in data.get("lights", {}).items() if e.get("energy_max", 0) <= 0]
    if dead:
        return f"light(s) {', '.join(dead)} never emit any energy"
    return None


def _ramp_is_monotonic(data, ir):
    wobbly = [n for n, e in data.get("lights", {}).items()
              if not e.get("monotonic", True)]
    if wobbly:
        return f"light(s) {', '.join(wobbly)} brighten and dim rather than ramping"
    return None


# ----------------------------------------------------------------------------------- materials


def _images_loaded(data, ir):
    missing = data.get("missing_images", [])
    if missing:
        return (f"image(s) failed to load: {', '.join(missing)} — "
                "these render as flat magenta with no error")
    return None


def _colorspace_sane(data, ir):
    suspect = data.get("suspect_colorspace", [])
    if suspect:
        return (f"data map(s) {', '.join(suspect)} are not Non-Color — "
                "a gamma curve on non-colour data yields a subtly plastic surface")
    return None


CHECKS: tuple[Check, ...] = (
    Check("GEOMETRY_NON_MANIFOLD", "geometry", _manifold,
          hint="Solidify and inflation produce garbage on non-manifold meshes."),
    Check("GEOMETRY_NO_HOLES", "geometry", _has_holes, severity="warning",
          hint="Check the SVG fill rule survived curve->mesh conversion."),
    Check("GEOMETRY_FLAT", "geometry", _has_depth, severity="warning"),
    Check("FRAMING_NEVER_VISIBLE", "framing", _ever_visible),
    Check("FRAMING_OVERFLOWS", "framing", _mostly_in_frame, severity="warning",
          hint="Deliberate for a close opening; check it is not the whole shot."),
    Check("FRAMING_BEHIND_CAMERA", "framing", _stays_in_front),
    Check("MOTION_NOT_ORBIT", "motion", _orbit_is_circular, severity="warning"),
    Check("MOTION_NO_SWEEP", "motion", _azimuth_progresses, severity="warning"),
    Check("LIGHT_AIMED_AWAY", "light", _lights_aim_at_subject),
    Check("LIGHT_DEAD", "light", _lights_are_on),
    Check("LIGHT_RAMP_NOT_MONOTONIC", "light", _ramp_is_monotonic, severity="warning"),
    Check("TEXTURE_NOT_LOADED", "materials", _images_loaded),
    Check("TEXTURE_COLORSPACE", "materials", _colorspace_sane, severity="warning"),
)


@dataclass
class ProbeReport:
    stage: str
    raw: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "ok": self.ok,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
            "probes": self.raw,
        }


def evaluate(raw: dict, *, stage: str, ir: dict | None = None) -> ProbeReport:
    """Judge a stage's probe output."""
    report = ProbeReport(stage=stage, raw=raw)
    for check in CHECKS:
        if check.stages and stage not in check.stages:
            continue
        data = _get(raw, check.probe)
        if data is None:
            continue
        message = check.test(data, ir or {})
        if message:
            report.diagnostics.append(
                Diagnostic(code=check.code, message=message,
                           severity=check.severity, hint=check.hint)
            )
    return report
