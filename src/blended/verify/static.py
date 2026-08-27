"""Tier 1 verification: static checks on the Scene IR (ARCHITECTURE §8).

Free, deterministic, instant, and complete before Blender is launched. Every diagnostic is
machine-readable with a `suggested_fix` where one exists, because these messages are the primary
interface for whoever is authoring scenes (ARCHITECTURE §12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from blended.ir.scene import SceneIR
from blended.library import registry

#: Two float times are the same instant if they land on the same frame; comparing raw floats
#: would flag adjacent tracks as overlapping over rounding dust.
_EPS = 1e-9


@dataclass
class Diagnostic:
    code: str
    message: str
    severity: str = "error"
    track: int | None = None
    hint: str | None = None
    suggested_fix: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Report:
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.diagnostics.append(Diagnostic(*args, **kwargs))


def check(scene: SceneIR) -> Report:
    report = Report()
    _check_targets_and_params(scene, report)
    _check_clock(scene, report)
    _check_channel_conflicts(scene, report)
    _check_coverage(scene, report)
    return report


def _kind_of(scene: SceneIR, target: str) -> str | None:
    if target == scene.camera.id:
        return "camera"
    if any(a.id == target for a in scene.assets):
        return "asset"
    if any(lt.id == target for lt in scene.lights):
        return "light"
    return None


def _check_targets_and_params(scene: SceneIR, report: Report) -> None:
    for i, track in enumerate(scene.tracks):
        action = registry.get(track.action)
        if action is None:
            report.add(
                "UNKNOWN_ACTION",
                f"track {i}: no action named {track.action!r}",
                track=i,
                hint=f"Available: {', '.join(registry.names())}",
            )
            continue

        kind = _kind_of(scene, track.target)
        if kind is None:
            report.add(
                "UNKNOWN_TARGET",
                f"track {i}: {track.action} targets {track.target!r}, which is not declared",
                track=i,
                hint=f"Declared: {', '.join(sorted(scene.target_ids()))}",
            )
        elif kind not in action.accepts:
            report.add(
                "TARGET_KIND_MISMATCH",
                f"track {i}: {track.action} cannot target a {kind} ({track.target!r})",
                track=i,
                hint=f"{track.action} accepts: {', '.join(action.accepts)}",
            )

        try:
            action.params(**track.params)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                report.add(
                    "INVALID_PARAM",
                    f"track {i}: {track.action} param {loc}: {err['msg']}",
                    track=i,
                    hint=f"See the schema for {track.action}.",
                )


def _check_clock(scene: SceneIR, report: Report) -> None:
    """The timeline owns time; actions schedule inside it and never extend it."""
    end = scene.timeline.duration
    for i, track in enumerate(scene.tracks):
        if track.end > end + _EPS:
            report.add(
                "TRACK_EXCEEDS_CLOCK",
                f"track {i}: {track.action} ends at {track.end:.3f}s but the scene is "
                f"{end:.3f}s long",
                track=i,
                suggested_fix={
                    "op": "replace",
                    "path": f"/tracks/{i}/duration",
                    "value": round(end - track.start, 4),
                },
            )

    if abs(scene.timeline.drift) > 1e-9:
        report.add(
            "CLOCK_QUANTIZATION_DRIFT",
            f"{scene.timeline.duration}s at {scene.timeline.fps}fps is "
            f"{scene.timeline.duration * scene.timeline.fps:.3f} frames; rendering "
            f"{scene.timeline.frames} = {scene.timeline.actual_duration:.4f}s "
            f"({scene.timeline.drift * 1000:+.1f}ms)",
            severity="warning",
        )


def _check_channel_conflicts(scene: SceneIR, report: Report) -> None:
    """The compiler check no bpy-writing agent can offer.

    Two tracks animating the same channel of the same object over overlapping frames produce a
    render where one silently wins. Here it is an error naming both tracks.
    """
    occupied: dict[str, list[tuple[int, float, float]]] = {}
    for i, track in enumerate(scene.tracks):
        action = registry.get(track.action)
        if action is None:
            continue
        for channel in action.channels(track.target):
            for j, start, end in occupied.get(channel, []):
                if track.start < end - _EPS and start < track.end - _EPS:
                    overlap = (max(track.start, start), min(track.end, end))
                    report.add(
                        "CHANNEL_CONFLICT",
                        f"tracks {j} ({scene.tracks[j].action}) and {i} ({track.action}) both "
                        f"write {channel} over {overlap[0]:.2f}–{overlap[1]:.2f}s",
                        track=i,
                        hint="Split the span, or drop one of the tracks.",
                        suggested_fix={
                            "op": "replace",
                            "path": f"/tracks/{i}/start",
                            "value": round(end, 4),
                        },
                    )
            occupied.setdefault(channel, []).append((i, track.start, track.end))


def _check_coverage(scene: SceneIR, report: Report) -> None:
    """Warn about declared objects nothing ever animates, and about dead air at the end."""
    animated = {t.target for t in scene.tracks}
    for light in scene.lights:
        if light.id not in animated:
            report.add(
                "STATIC_OBJECT",
                f"light {light.id!r} is never animated",
                severity="warning",
            )
    if scene.tracks:
        last = max(t.end for t in scene.tracks)
        if last < scene.timeline.duration - 0.01:
            report.add(
                "TRAILING_GAP",
                f"nothing is scheduled after {last:.2f}s, but the scene runs to "
                f"{scene.timeline.duration:.2f}s",
                severity="warning",
                hint="Add an object.hold if the still frame is intentional.",
            )
