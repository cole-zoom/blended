"""Grow a frame sequence taller by synthesising sky above each frame.

For when footage has to be taller than it was shot — a plate that must survive a vertical pan,
say, where the frame slides up past the top of the original.

Three approaches fail, and it is worth knowing why before reaching for one:

* **A still strip** pasted above every frame drifts out of match the moment the footage's sky
  changes. In the clip this was built for, the sky darkened by 44.6/255 across ten seconds.
* **A flat fill** ignores the vertical gradient. Real sky at the top of frame still travels
  0.1-0.2 levels per row; a flat band stops matching within a few dozen rows.
* **A mirror** is actively wrong. Reflecting makes the gradient *reverse* — sky getting lighter
  going up when the footage says it should darken. ~24 levels of error 100px above the join.

So the extension is a **point reflection**: `ext(d) = 2*src(0) - src(d)`, `d` being height above
the join. Exact at the join, correct to first order going up, and it continues the trend rather
than reversing it. A taper pulls it toward flat further up so it cannot run away into clipping.

    python tools/extend_footage.py --frames-in frames/ --out taller/ --extra 460
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import png_io


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _rows_rgb(path):
    w, h, channels, _, data, _, _ = png_io.decode(str(path))
    return w, h, channels, data


def build_extension(data, w, h, channels, extra, columns=320, taper_to=0.55):
    """Rows of synthesised sky to sit above the frame, as a flat RGB bytearray.

    Computed on a reduced column grid and expanded back out. Sky varies smoothly from side to
    side, so a few hundred samples carry all of it, and it turns a per-pixel extrapolation
    into something that runs in reasonable time across a few hundred frames.
    """
    step = max(1, w // columns)
    xs = list(range(0, w, step))

    # The anchor must be row 0 EXACTLY, not an average of the first few rows. The reflection
    # is `src0 + (src0 - src(d)) * keep`, so the correction vanishes only where the anchor
    # equals the row it is reflecting about. Anchoring on a 4-row mean to damp sensor noise
    # left the extension's bottom row ~2 levels off source row 0 — a one-row dip precisely at
    # the join, measured at 4.4/255, which is exactly the visible line this is meant to avoid.
    base = []
    for x in xs:
        i = x * channels
        base.append([float(data[i]), float(data[i + 1]), float(data[i + 2])])

    def hblur(values, radius):
        """Box-blur a sampled row in place-ish. radius is in SAMPLES, not pixels."""
        if radius < 1:
            return values
        n = len(values)
        span = radius * 2 + 1
        out_v = [[0.0] * 3 for _ in range(n)]
        for k in range(3):
            total = values[0][k] * (radius + 1)
            for i in range(1, radius + 1):
                total += values[min(n - 1, i)][k]
            for i in range(n):
                out_v[i][k] = total / span
                total -= values[max(0, i - radius)][k]
                total += values[min(n - 1, i + radius + 1)][k]
        return out_v

    # The top of the frame is NOT clean sky — the bridge tower and its cables reach row 0.
    # Reflecting those rows upward copies the tower into the extension as a stretched ghost,
    # which is what the first render did. So structure is dissolved with a horizontal blur
    # whose radius grows with height: zero at the join, so the seam still matches the real
    # pixels exactly, and wide enough within ~100 rows to smear a 150px tower into the sky
    # around it. The anchor is blurred by the same amount, or its own copy of the tower would
    # survive in the reflection term.
    max_r = max(1, len(xs) // 5)
    ramp = max(1, extra // 4)

    out = bytearray(w * extra * 3)
    for row in range(extra):
        # d = 1 at the row directly above the join, NOT 0. At d=0 the reflection returns
        # src(0) exactly, which duplicates source row 0 — a flat two-row step followed by the
        # gradient resuming, i.e. a kink at the seam. Starting at 1 continues the gradient.
        d = extra - row
        keep = 1.0 - (1.0 - taper_to) * _smoothstep(d / float(extra))
        radius = int(round(max_r * _smoothstep(d / float(ramp))))

        src_d = []
        for x in xs:
            i = (min(h - 1, d) * w + x) * channels
            src_d.append([float(data[i]), float(data[i + 1]), float(data[i + 2])])

        anchor = hblur(base, radius)
        far = hblur(src_d, radius)

        line = bytearray()
        for n in range(len(xs)):
            px = []
            for k in range(3):
                v = anchor[n][k] + (anchor[n][k] - far[n][k]) * keep
                px.append(max(0, min(255, int(round(v)))))
            span = step if n < len(xs) - 1 else w - xs[n]
            line += bytes(px) * span
        out[row * w * 3:(row + 1) * w * 3] = line[:w * 3]
    return out


def write_extended(path, data, w, h, channels, extension, extra, level=1):
    """Emit one `h + extra` tall RGB PNG: synthesised sky on top, source below."""
    total = h + extra
    rows = bytearray()
    for y in range(extra):
        rows.append(0)
        rows += extension[y * w * 3:(y + 1) * w * 3]
    for y in range(h):
        rows.append(0)
        if channels == 3:
            rows += data[y * w * 3:(y + 1) * w * 3]
        else:
            base = y * w * channels
            for x in range(w):
                i = base + x * channels
                rows += bytes((data[i], data[i + 1], data[i + 2]))

    # Level 1: these are intermediates that get encoded to video immediately after, and the
    # compression time costs more than the disk does.
    png_io.encode(path, w, total, rows, level=level)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames-in", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--extra", type=int, default=460, help="Rows of sky to add on top.")
    ap.add_argument("--columns", type=int, default=320)
    ap.add_argument("--limit", type=int, default=0, help="Process only the first N frames.")
    args = ap.parse_args()

    frames = sorted(args.frames_in.glob("*.png"))
    if args.limit:
        frames = frames[:args.limit]
    if not frames:
        raise SystemExit(f"no PNGs in {args.frames_in}")
    args.out.mkdir(parents=True, exist_ok=True)

    began = time.time()
    total_h = None
    for n, src in enumerate(frames):
        w, h, channels, data = _rows_rgb(src)
        ext = build_extension(data, w, h, channels, args.extra, args.columns)
        total_h = write_extended(args.out / src.name, data, w, h, channels, ext, args.extra)
        if n == 0 or (n + 1) % 20 == 0 or n == len(frames) - 1:
            rate = (time.time() - began) / (n + 1)
            print(f"  {n + 1}/{len(frames)}  {w}x{total_h}  {rate:.2f}s/frame")
    print(f"\n{len(frames)} frames -> {args.out}  ({w}x{total_h})")


if __name__ == "__main__":
    main()
