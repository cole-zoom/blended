"""Tier-2 judgement: does a probe payload produce the right verdict?

Host-side and synthetic. The backend's measurements are exercised by the integration tests; what
matters here is that the *thresholds* fire when they should and stay quiet when they should not.
A checker that cries wolf on intentional choices gets ignored, which is worse than no checker.
"""

from __future__ import annotations

import pytest

from blended.verify.probes import CHECKS, evaluate


def codes(report, severity=None):
    return {d.code for d in report.diagnostics
            if severity is None or d.severity == severity}


def framing(**overrides):
    data = {
        "samples": [{"frame": f, "coverage": 0.3, "width": 0.5,
                     "in_frame": True, "in_front": True} for f in range(0, 240, 24)],
        "min_coverage": 0.3, "max_coverage": 0.6, "min_width": 0.5,
        "always_in_frame": True, "always_in_front": True, "frames_off_screen": [],
    }
    data.update(overrides)
    return {"framing": data}


# ------------------------------------------------------------------------------------- quiet


def test_a_healthy_scene_reports_nothing() -> None:
    report = evaluate(
        {
            **framing(),
            "geometry": {"manifold": True, "total_holes": 27, "depth_ratio": 0.1,
                         "objects": {"logo": {"is_manifold": True, "holes": 27}}},
            "motion": {"distance_variation": 0.02, "azimuth_sweep": 68.0},
            "light": {"lights": {"key": {"energy_start": 10, "energy_max": 500,
                                         "monotonic": True, "aim_alignment": 0.95}}},
            "materials": {"images": {}, "missing_images": [], "suspect_colorspace": []},
        },
        stage="final",
    )
    assert report.diagnostics == []
    assert report.ok


def test_a_deliberate_close_opening_is_not_an_error() -> None:
    """The camera starts inches from the logo on purpose. Erroring here trains you to ignore
    the checker, so overflow is a warning and only when it dominates the shot."""
    samples = [{"frame": f, "coverage": 0.9, "width": 1.0,
                "in_frame": f > 120, "in_front": True} for f in range(0, 240, 24)]
    report = evaluate(
        framing(samples=samples, always_in_frame=False, max_coverage=1.0, min_coverage=0.5),
        stage="blocking",
    )
    assert "FRAMING_NEVER_VISIBLE" not in codes(report)
    assert codes(report, "error") == set()


# ------------------------------------------------------------------------------------- fires


def test_subject_never_visible_is_an_error() -> None:
    report = evaluate(framing(min_coverage=0.001, max_coverage=0.01), stage="blocking")
    assert "FRAMING_NEVER_VISIBLE" in codes(report, "error")


def test_sustained_overflow_warns() -> None:
    samples = [{"frame": f, "coverage": 0.9, "width": 1.0,
                "in_frame": False, "in_front": True} for f in range(0, 240, 24)]
    report = evaluate(framing(samples=samples, always_in_frame=False), stage="blocking")
    assert "FRAMING_OVERFLOWS" in codes(report, "warning")


def test_camera_through_subject_is_an_error() -> None:
    report = evaluate(framing(always_in_front=False), stage="blocking")
    assert "FRAMING_BEHIND_CAMERA" in codes(report, "error")


def test_non_manifold_geometry_is_an_error() -> None:
    report = evaluate(
        {"geometry": {"manifold": False, "total_holes": 3,
                      "objects": {"logo_text": {"is_manifold": False, "holes": 3}}}},
        stage="assets",
    )
    assert "GEOMETRY_NON_MANIFOLD" in codes(report, "error")
    assert "logo_text" in next(d.message for d in report.diagnostics
                               if d.code == "GEOMETRY_NON_MANIFOLD")


def test_lost_fill_rule_warns() -> None:
    """Zero holes means the SVG evenodd rule was lost and letter counters filled solid."""
    report = evaluate(
        {"geometry": {"manifold": True, "total_holes": 0, "objects": {}}}, stage="assets"
    )
    assert "GEOMETRY_NO_HOLES" in codes(report, "warning")


def test_light_aimed_away_is_an_error() -> None:
    """The most likely silent failure: a light that exists but faces the void."""
    report = evaluate(
        {"light": {"lights": {"key": {"energy_start": 5, "energy_max": 100,
                                      "monotonic": True, "aim_alignment": -0.8}}}},
        stage="lighting",
    )
    assert "LIGHT_AIMED_AWAY" in codes(report, "error")


def test_dead_light_is_an_error() -> None:
    report = evaluate(
        {"light": {"lights": {"key": {"energy_start": 0, "energy_max": 0,
                                      "monotonic": True, "aim_alignment": 0.9}}}},
        stage="lighting",
    )
    assert "LIGHT_DEAD" in codes(report, "error")


def test_missing_texture_is_an_error() -> None:
    """The magenta bug, caught before it renders."""
    report = evaluate(
        {"materials": {"images": {}, "missing_images": ["diffuse.jpg"],
                       "suspect_colorspace": []}},
        stage="materials",
    )
    assert "TEXTURE_NOT_LOADED" in codes(report, "error")


def test_wrong_colorspace_warns() -> None:
    report = evaluate(
        {"materials": {"images": {}, "missing_images": [],
                       "suspect_colorspace": ["normal.jpg"]}},
        stage="materials",
    )
    assert "TEXTURE_COLORSPACE" in codes(report, "warning")


# -------------------------------------------------------------------------------- robustness


def test_a_probe_error_is_skipped_not_crashed() -> None:
    """A probe that failed reports `error`; judging it would be judging nothing."""
    report = evaluate({"framing": {"error": "no camera"}}, stage="blocking")
    assert report.diagnostics == []


def test_missing_probes_are_simply_not_judged() -> None:
    assert evaluate({}, stage="blocking").diagnostics == []


def test_report_serialises() -> None:
    report = evaluate(framing(always_in_front=False), stage="blocking")
    payload = report.as_dict()
    assert payload["stage"] == "blocking"
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "FRAMING_BEHIND_CAMERA"


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.code)
def test_every_check_tolerates_an_empty_payload(check) -> None:
    """A partial probe payload must not raise — probes are best-effort by design."""
    assert check.test({}, {}) is None or isinstance(check.test({}, {}), str)
