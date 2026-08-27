"""`blended` command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from blended.config import ENV_VAR, find_blender
from blended.engine.runner import RunOptions, run_job
from blended.errors import BlendedError


@click.group()
@click.version_option(package_name="blended")
def main() -> None:
    """A compiler for Blender animations."""


@main.command()
def doctor() -> None:
    """Check that the Blender bridge is working."""
    try:
        blender = find_blender()
    except BlendedError as exc:
        _fail(exc)
        return

    click.echo(f"{_ok()} Blender {blender.version_string}")
    click.echo(f"  {blender.path}")

    click.echo("\nRunning a round-trip job...")
    try:
        result = run_job(
            {
                "scene": {"kind": "demo_cube", "frame_start": 1, "frame_end": 1},
                "render": {
                    "resolution": [160, 90],
                    "frame_start": 1,
                    "frame_end": 1,
                    "samples": 1,
                    "output": "",
                },
            }
        )
    except BlendedError as exc:
        _fail(exc)
        return

    click.echo(f"{_ok()} Bridge working (build {result.stats.get('build_ms')}ms)")
    click.echo(f"  engine: {result.stats.get('engine')}")
    click.echo("\nReady. Try: blended render-demo")


@main.command("render-demo")
@click.option("--out", type=click.Path(path_type=Path), default=Path("out"), show_default=True,
              help="Output directory.")
@click.option("--frames", type=int, default=60, show_default=True, help="Frame count.")
@click.option("--fps", type=int, default=30, show_default=True)
@click.option("--width", type=int, default=960, show_default=True)
@click.option("--height", type=int, default=540, show_default=True)
@click.option("--samples", type=int, default=16, show_default=True, help="EEVEE render samples.")
@click.option("--turns", type=float, default=1.0, show_default=True, help="Cube rotations.")
@click.option("--keep-workdir", is_flag=True, help="Preserve job.json/result.json/blender.log.")
@click.option("--verbose", is_flag=True, help="Stream Blender output to the terminal.")
def render_demo(
    out: Path,
    frames: int,
    fps: int,
    width: int,
    height: int,
    samples: int,
    turns: float,
    keep_workdir: bool,
    verbose: bool,
) -> None:
    """Phase 0 smoke test: render a spinning cube to .blend + mp4."""
    out = out.resolve()
    job = {
        "blend_out": str(out / "demo.blend"),
        "scene": {
            "kind": "demo_cube",
            "frame_start": 1,
            "frame_end": frames,
            "turns": turns,
        },
        "render": {
            "engine": "BLENDER_EEVEE",
            "resolution": [width, height],
            "fps": fps,
            "frame_start": 1,
            "frame_end": frames,
            "samples": samples,
            "media": "video",
            "output": str(out / "demo.mp4"),
        },
    }

    click.echo(f"Rendering {frames} frames at {width}x{height}, {fps}fps...")
    try:
        result = run_job(job, RunOptions(keep_workdir=keep_workdir, verbose=verbose))
    except BlendedError as exc:
        _fail(exc)
        return

    click.echo(f"\n{_ok()} Rendered in {result.stats.get('render_ms', 0) / 1000:.1f}s")
    for name, path in result.artifacts.items():
        size = _size(Path(path))
        click.echo(f"  {name:6} {path}{size}")
    if result.log_path:
        click.echo(f"  log    {result.log_path}")


@main.command("render-logo")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
@click.option("--azimuth", type=float, default=0.0, show_default=True,
              help="Camera angle around the logo, degrees. 0 = head-on.")
@click.option("--elevation", type=float, default=0.0, show_default=True)
@click.option("--outline", type=float, default=0.006, show_default=True,
              help="Outline thickness as a fraction of logo width. 0 disables.")
@click.option("--outline-mode", type=click.Choice(["lineart", "hull", "offset"]),
              default="lineart", show_default=True,
              help="lineart is uniform at any camera angle; the others are geometry-based.")
@click.option("--creases", is_flag=True, help="Draw interior crease lines as well as contours.")
@click.option("--bevel", type=float, default=0.004, show_default=True)
@click.option("--extrude", type=float, default=0.05, show_default=True)
@click.option("--width", type=int, default=960, show_default=True)
@click.option("--height", type=int, default=540, show_default=True)
@click.option("--samples", type=int, default=32, show_default=True)
@click.option("--no-cache", is_flag=True, help="Ignore any cached build of this asset.")
def render_logo(
    source: Path,
    out: Path,
    azimuth: float,
    elevation: float,
    outline: float,
    outline_mode: str,
    creases: bool,
    bevel: float,
    extrude: float,
    width: int,
    height: int,
    samples: int,
    no_cache: bool,
) -> None:
    """Phase 1: ingest a vector logo and render it as a lit 3D still."""
    from blended.assets.resolver import AssetCache, AssetRequest

    out = out.resolve()
    params = {
        "extrude": extrude,
        "bevel": bevel,
        "outline_thickness": outline,
        "outline_mode": outline_mode,
        "outline_creases": creases,
    }
    request = AssetRequest(source=source.resolve(), name=source.stem, params=params)
    try:
        request.validate()
    except BlendedError as exc:
        _fail(exc)
        return

    cache = AssetCache(out / "assets")
    if not no_cache and (hit := cache.lookup(request)):
        click.echo(f"{_ok()} Cached asset  {hit.blend_path}")

    click.echo(f"Ingesting {source.name} (tier {request.tier})...")
    job = {
        "blend_out": str(out / f"{source.stem}.blend"),
        "scene": {
            "kind": "logo_still",
            "source": str(source.resolve()),
            "azimuth": azimuth,
            "elevation": elevation,
            **params,
        },
        "render": {
            "engine": "BLENDER_EEVEE",
            "resolution": [width, height],
            "frame_start": 1,
            "frame_end": 1,
            "samples": samples,
            "media": "stills",
            "output": str(out / f"{source.stem}_"),
        },
    }
    try:
        result = run_job(job)
    except BlendedError as exc:
        _fail(exc)
        return

    asset = result.stats.get("asset", {})
    topo = result.stats.get("topology", {})
    cache.store(request, asset)

    click.echo(f"\n{_ok()} Ingested tier {request.tier}, lossy={request.lossy}")
    click.echo(f"  split at x={result.stats.get('split_x'):.4f} "
               f"(gap {result.stats.get('split_gap'):.4f})")
    click.echo(f"  aspect {asset.get('aspect')}  depth ratio {asset.get('depth_ratio')}")
    for name, report in topo.items():
        flag = "✓" if report["is_manifold"] else "✗"
        click.echo(f"  {flag} {name:14} {report['loose_parts']:3d} parts  "
                   f"{report['holes']:3d} holes  {report['faces']:6d} faces")
    for name, path in result.artifacts.items():
        click.echo(f"  {name:6} {path}")


@main.command()
@click.argument("scene_file", type=click.Path(exists=True, path_type=Path))
def check(scene_file: Path) -> None:
    """Tier 1: validate a scene without launching Blender. Instant."""
    from blended.project import load
    from blended.verify.static import check as run_check

    try:
        scene = load(scene_file)
    except BlendedError as exc:
        _fail(exc)
        return

    report = run_check(scene)
    _print_diagnostics(report)

    tl = scene.timeline
    click.echo(f"\n{scene.name}: {tl.duration}s @ {tl.fps}fps = {tl.frames} frames "
               f"({tl.actual_duration:.4f}s, {tl.drift * 1000:+.1f}ms drift)")
    click.echo(f"  {len(scene.assets)} asset(s), {len(scene.lights)} light(s), "
               f"{len(scene.tracks)} track(s)")
    if not report.ok:
        sys.exit(1)
    click.echo(f"{_ok()} Tier 1 passed")


@main.command()
@click.argument("scene_file", type=click.Path(exists=True, path_type=Path))
@click.option("--quality", type=click.Choice(["draft", "preview", "final"]), default="preview",
              show_default=True)
@click.option("--out", type=click.Path(path_type=Path), default=None,
              help="Output directory. Defaults to the scene file's directory.")
@click.option("--stills", is_flag=True, help="Render a PNG sequence instead of video.")
@click.option("--engine", type=click.Choice(["eevee", "cycles"]), default="eevee",
              show_default=True,
              help="cycles is a path tracer — real GI and soft shadows, far slower.")
@click.option("--samples", type=int, default=None,
              help="Override the quality preset's sample count.")
@click.option("--build-only", is_flag=True, help="Build the .blend and stop before rendering.")
@click.option("--verbose", is_flag=True)
def render(scene_file: Path, quality: str, out: Path | None, stills: bool, engine: str,
           samples: int | None, build_only: bool, verbose: bool) -> None:
    """Compile a scene to a .blend and render it."""
    from blended.project import load, make_job
    from blended.verify.static import check as run_check

    try:
        scene = load(scene_file)
    except BlendedError as exc:
        _fail(exc)
        return

    report = run_check(scene)
    _print_diagnostics(report)
    if not report.ok:
        click.echo("\nTier 1 failed — not building.", err=True)
        sys.exit(1)

    out_dir = (out or scene_file.parent / "renders").resolve()
    job = make_job(scene, quality=quality, out_dir=out_dir,
                   media="stills" if stills else "video", engine=engine)
    if engine == "cycles":
        job["render"]["engine"] = "CYCLES"
        job["render"]["device"] = "GPU"
    if samples is not None:
        job["render"]["samples"] = samples
    if build_only:
        job["render"]["output"] = ""

    tl = scene.timeline
    # Scale the timeout with the work. A fixed one-hour limit silently killed a 105-minute
    # Cycles render at exactly 57% — the render was fine, the ceiling was not. 60s/frame is a
    # generous allowance that still catches a genuine hang rather than a merely slow job.
    rendered_frames = max(1, tl.frames // max(1, job["render"].get("frame_step", 1)))
    timeout_s = max(1800.0, rendered_frames * 60.0)

    click.echo(f"Rendering {scene.name}: {tl.frames} frames @ {tl.fps}fps, "
               f"{quality}/{engine}, {job['render']['samples']} samples "
               f"(timeout {timeout_s / 3600:.1f}h)...")
    try:
        result = run_job(job, RunOptions(timeout_s=timeout_s, verbose=verbose))
    except BlendedError as exc:
        _fail(exc)
        return

    stats = result.stats
    click.echo(f"\n{_ok()} {stats.get('frames')} frames in "
               f"{stats.get('render_ms', 0) / 1000:.1f}s")
    for track in stats.get("tracks", []):
        detail = {k: v for k, v in track.items()
                  if k not in ("action", "target", "frames")}
        click.echo(f"  {track['action']:14} {track['target']:10} "
                   f"f{track['frames'][0]}–{track['frames'][1]}  {detail}")
    for name, path in result.artifacts.items():
        click.echo(f"  {name:6} {path}{_size(Path(path))}")


def _print_diagnostics(report) -> None:
    for diag in report.diagnostics:
        colour = "red" if diag.severity == "error" else "yellow"
        mark = "✗" if diag.severity == "error" else "!"
        click.echo(f"{click.style(mark, fg=colour)} {click.style(diag.code, bold=True)}: "
                   f"{diag.message}")
        if diag.hint:
            click.echo(f"    {diag.hint}")
        if diag.suggested_fix:
            click.echo(f"    fix: {json.dumps(diag.suggested_fix)}")


@main.command()
@click.argument("job_file", type=click.Path(exists=True, path_type=Path))
@click.option("--keep-workdir", is_flag=True)
@click.option("--verbose", is_flag=True)
def run(job_file: Path, keep_workdir: bool, verbose: bool) -> None:
    """Execute a raw job JSON file. Escape hatch for debugging the bridge."""
    job = json.loads(job_file.read_text())
    try:
        result = run_job(job, RunOptions(keep_workdir=keep_workdir, verbose=verbose))
    except BlendedError as exc:
        _fail(exc)
        return
    click.echo(json.dumps({"artifacts": result.artifacts, "stats": result.stats}, indent=2))


def _ok() -> str:
    return click.style("✓", fg="green")


def _size(path: Path) -> str:
    try:
        return f"  ({path.stat().st_size / 1024:.0f} KB)"
    except OSError:
        return ""


def _fail(exc: BlendedError) -> None:
    """Render an error the way the agent will need to read it — code first, then a hint."""
    click.echo(f"\n{click.style('✗', fg='red')} {click.style(exc.code, bold=True)}", err=True)
    click.echo(f"  {exc.message}", err=True)
    if exc.hint:
        click.echo(f"\n{exc.hint}", err=True)
    if tb := getattr(exc, "traceback_text", None):
        click.echo(f"\nBlender traceback:\n{tb}", err=True)
    if log := getattr(exc, "log_path", None):
        click.echo(f"Full log: {log}", err=True)
    if exc.code == "BLENDER_NOT_FOUND":
        click.echo(f"\nHint: export {ENV_VAR}=/path/to/Blender", err=True)
    sys.exit(1)
