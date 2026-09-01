"""Turn an image into a compositing plate: de-streaked, and feathered for blending.

Two operations that a smooth backdrop almost always needs before it can be panned or layered.

**De-streak.** Photographic gradients carry horizontal features their source had — a horizon,
a waterline, a band of cloud. They can measure far below any sensible threshold and still be
obvious, because the eye locks onto a perfectly straight horizontal line at contrast it would
never notice in any other shape. Panning makes it worse: the line drifts while the gradient
behind it does not.

The fix is a **vertical-only blur**. A horizontal streak is by definition structure that varies
down the frame and not across it, so blurring only vertically destroys it while leaving every
left-to-right variation untouched. An ordinary blur would soften both and flatten the image.

**Feather.** The output is taller than the frame: fully opaque for `--height` rows, then a tail
that ramps to transparent. Putting the ramp *inside* the frame would leave the bottom of shot
semi-transparent from the first frame, needing a backing layer behind it. Hanging the tail
below the frame edge means the frame starts opaque and the blend only arrives as the plate
moves — which is when there is something behind it to blend into.

    python tools/prepare_plate.py sky.jpg --out plates/
    python tools/prepare_plate.py sky.jpg --match under.png   # colour-match the join

Requires `mutool` (mupdf-tools) on PATH to read JPEGs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import png_io


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def load_rgb(path, out_w, out_h):
    """Decode any supported image to a flat RGB buffer at the target size."""
    path = Path(path)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "src.png"
            subprocess.run(["mutool", "draw", "-F", "png", "-w", str(out_w), "-o", str(png),
                            str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            w, h, channels, _, data, _, _ = png_io.decode(str(png))
            return resample(data, w, h, channels, out_w, out_h)
    w, h, channels, _, data, _, _ = png_io.decode(str(path))
    return resample(data, w, h, channels, out_w, out_h)


def resample(data, w, h, channels, out_w, out_h):
    out = bytearray(out_w * out_h * 3)
    for y in range(out_h):
        sy = min(h - 1, y * h // out_h)
        for x in range(out_w):
            sx = min(w - 1, x * w // out_w)
            i = (sy * w + sx) * channels
            j = (y * out_w + x) * 3
            out[j], out[j + 1], out[j + 2] = data[i], data[i + 1], data[i + 2]
    return out


def blur_vertical(buf, w, h, radius, passes=3):
    """Box-blur down each column only. Three passes approximate a Gaussian."""
    src, dst = bytearray(buf), bytearray(len(buf))
    span = radius * 2 + 1
    for _ in range(passes):
        for x in range(w):
            for k in range(3):
                base = x * 3 + k
                # Clamped edges, so the top and bottom do not darken toward nothing.
                total = src[base] * (radius + 1)
                for y in range(1, radius + 1):
                    total += src[min(h - 1, y) * w * 3 + base]
                for y in range(h):
                    dst[y * w * 3 + base] = total // span
                    total += (src[min(h - 1, y + radius + 1) * w * 3 + base]
                              - src[max(0, y - radius) * w * 3 + base])
        src, dst = dst, src
    return src


def extend_downward(buf, w, h, extra):
    """Continue the gradient below the image, per column, tapering to flat.

    Clamping the last row instead would give a flat band that stops following the gradient's
    travel and reads as a stripe of its own. Each column continues with its own local slope,
    smoothed *across* columns first: neighbouring slopes differ by a fraction of a level per
    row, and over hundreds of rows those differences accumulate into vertical banding.
    """
    out = bytearray(buf) + bytearray(w * extra * 3)
    window = min(120, h // 4)

    slopes = []
    for x in range(w):
        row = []
        for k in range(3):
            base = x * 3 + k
            last = buf[(h - 1) * w * 3 + base]
            earlier = buf[(h - 1 - window) * w * 3 + base]
            row.append((last - earlier) / float(window))
        slopes.append(row)

    radius = max(1, w // 30)
    smoothed = [[0.0] * 3 for _ in range(w)]
    for k in range(3):
        total = sum(slopes[min(w - 1, i)][k] for i in range(radius + 1))
        total += slopes[0][k] * radius
        span = radius * 2 + 1
        for x in range(w):
            smoothed[x][k] = total / span
            total += (slopes[min(w - 1, x + radius + 1)][k]
                      - slopes[max(0, x - radius)][k])

    for x in range(w):
        for k in range(3):
            base = x * 3 + k
            value = float(buf[(h - 1) * w * 3 + base])
            slope = smoothed[x][k]
            for j in range(1, extra + 1):
                value += slope * (1.0 - j / float(extra))
                out[(h - 1 + j) * w * 3 + base] = max(0, min(255, int(round(value))))
    return out


def dissolve_tail_streaks(buf, w, total_h, start, max_radius=140):
    """Horizontally blur the tail, radius growing with depth.

    The tail inherits the per-column noise of the last real row and then repeats that pattern
    for hundreds of rows. Frozen noise *is* a vertical stripe, and the vertical blur cannot
    help — not touching horizontal structure is what that blur is for. The radius ramps from
    nothing at the join, so it stays continuous where it meets real pixels.
    """
    depth = total_h - start
    for y in range(start, total_h):
        radius = int(round(max_radius * smoothstep((y - start) / float(depth))))
        if radius < 1:
            continue
        row = y * w * 3
        span = radius * 2 + 1
        for k in range(3):
            original = [buf[row + x * 3 + k] for x in range(w)]
            total = original[0] * (radius + 1)
            for i in range(1, radius + 1):
                total += original[min(w - 1, i)]
            for x in range(w):
                buf[row + x * 3 + k] = total // span
                total += (original[min(w - 1, x + radius + 1)]
                          - original[max(0, x - radius)])
    return buf


def match_to(buf, w, h, other, feather, overlap):
    """Grade the lower region toward another image, so a feathered join has nothing to reveal.

    A feather only hides a *soft* transition; it cannot hide a colour difference. If the plate
    and whatever sits under it disagree by more than a level or two, the blend shifts tone and
    that step reads as an edge no matter how gentle the ramp.
    """
    start = h - feather
    for y in range(start, h):
        grade = smoothstep((y - start) / float(feather))
        oy = max(0, overlap - (h - 1 - y))
        for x in range(w):
            i, j = (y * w + x) * 3, (oy * w + x) * 3
            for k in range(3):
                buf[i + k] = int(round(buf[i + k] + (other[j + k] - buf[i + k]) * grade))
    return buf


def worst_slope_change(buf, w, h, samples=192):
    """Peak second derivative of the row-mean profile — how visible a horizontal line is."""
    step = max(1, w // samples)
    means = []
    for y in range(h):
        acc, n = [0, 0, 0], 0
        for x in range(0, w, step):
            i = (y * w + x) * 3
            for k in range(3):
                acc[k] += buf[i + k]
            n += 1
        means.append([v / n for v in acc])
    worst, at = 0.0, 0
    for y in range(1, h - 1):
        v = max(abs(means[y + 1][k] - 2 * means[y][k] + means[y - 1][k]) for k in range(3))
        if v > worst:
            worst, at = v, y
    return worst, at


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path, help="Image to turn into a plate.")
    ap.add_argument("--out", type=Path, default=Path("plates"))
    ap.add_argument("--name", default=None, help="Filename stem (default: source stem).")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080, help="Opaque region, i.e. frame height.")
    ap.add_argument("--radius", type=int, default=44,
                    help="Vertical blur radius. Must exceed the streak's thickness.")
    ap.add_argument("--feather", type=int, default=460,
                    help="Rows of transparent tail BELOW the frame (0 to skip).")
    ap.add_argument("--match", type=Path, default=None,
                    help="Colour-match the lower region to this image.")
    ap.add_argument("--overlap", type=int, default=240)
    args = ap.parse_args()

    w, h = args.width, args.height
    stem = args.name or args.source.stem
    raw = load_rgb(args.source, w, h)
    before, before_at = worst_slope_change(raw, w, h)

    # Extend BEFORE blurring, then blur across the join. Extending afterwards leaves a slope
    # discontinuity there: the blur clamps at the bottom edge, flattening the last rows, so an
    # extrapolated slope measured from further up no longer matches.
    tall_h = h + args.feather
    tall = raw if not args.feather else blur_vertical(
        extend_downward(raw, w, h, args.feather), w, tall_h, args.radius)
    if not args.feather:
        tall = blur_vertical(raw, w, h, args.radius)
    after, after_at = worst_slope_change(tall, w, h)

    if args.feather:
        tall = dissolve_tail_streaks(tall, w, tall_h, h)
    if args.match:
        tall = match_to(tall, w, tall_h, load_rgb(args.match, w, h), args.feather, args.overlap)

    args.out.mkdir(parents=True, exist_ok=True)
    opaque = args.out / f"{stem}_opaque.png"
    png_io.encode(opaque, w, h, png_io.rgb_rows(tall, w, h))

    written = [opaque]
    if args.feather:
        rows = bytearray()
        for y in range(tall_h):
            rows.append(0)
            alpha = 255 if y < h else int(round(
                255 * (1.0 - smoothstep((y - h) / float(args.feather)))))
            for x in range(w):
                i = (y * w + x) * 3
                rows += bytes((tall[i], tall[i + 1], tall[i + 2], alpha))
        feathered = args.out / f"{stem}_feathered.png"
        png_io.encode(feathered, w, tall_h, rows, alpha=True)
        written.append(feathered)

    print(f"{args.source.name} -> {w}x{h}")
    print("\nworst horizontal slope change (the visible-line metric):")
    print(f"  before  {before:.3f} / 255  at row {before_at}")
    print(f"  after   {after:.3f} / 255  at row {after_at}")
    if before > 0:
        print(f"  reduced {100.0 * (1 - after / before):.0f}%")
    print()
    for path in written:
        print(f"  {path.name}  {path.stat().st_size // 1024} KB")
    if args.feather:
        print(f"      rows 0-{h - 1} opaque, {h}-{tall_h - 1} ramp to clear")


if __name__ == "__main__":
    main()
