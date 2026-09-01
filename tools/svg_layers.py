"""Split an SVG into per-shape transparent PNGs, all on a shared canvas.

For getting vector artwork into an editor that cannot read SVG — DaVinci Resolve, among
others, has no SVG loader in either its media pool or Fusion. Rasterising by hand loses the
one property that makes the layers usable: **register**.

Every layer keeps the *full* source canvas rather than being cropped to its own ink. Drop all
of them onto a timeline with no transform and they reassemble into the original artwork,
because each already knows where it sits. Crop them individually and you are re-registering N
layers by eye.

Shapes are clustered by horizontal overlap, so a letter keeps its counters and a mark keeps
its parts. Alpha comes from ink darkness, which preserves the antialiased edge that a hard
luma key would throw away.

    python tools/svg_layers.py artwork.svg --out layers/
    python tools/svg_layers.py logo.svg --width 6000 --colors light=#FFFFFF dark=#24152F

Requires `mutool` (mupdf-tools) on PATH for rasterisation.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import png_io
from svg_paths import path_bbox


def _paths(svg_text):
    return re.findall(r'<path[^>]*\sd="([^"]+)"', svg_text)


def _header(svg_text):
    """The `<svg>` open tag and any `<g>` transform, so subsets keep the full canvas."""
    svg_open = re.search(r"<svg[^>]*>", svg_text).group(0)
    group = re.search(r"<g[^>]*>", svg_text)
    return svg_open, (group.group(0) if group else "<g>")


def cluster(paths, gap=0.0):
    """Group path indices by horizontal overlap, left to right.

    Overlap rather than proximity: a letter and its counters occupy the same x-range, so they
    stay together, while the next letter starts beyond the running right edge. `gap` allows a
    tolerance in user units for artwork whose shapes almost touch.
    """
    spans = [path_bbox(d) for d in paths]
    order = sorted(range(len(paths)), key=lambda i: spans[i][0])
    groups, current, edge = [], [order[0]], spans[order[0]][1]
    for i in order[1:]:
        lo, hi = spans[i]
        if lo > edge + gap:
            groups.append(current)
            current = []
        current.append(i)
        edge = max(edge, hi)
    groups.append(current)
    return groups


def rasterise_alpha(svg_path, width, scratch):
    """Render an SVG and return (w, h, alpha) with alpha taken from ink darkness.

    The SVG renders black on white, so a pixel's darkness *is* its coverage. A half-covered
    edge pixel lands at alpha 128 — the antialiasing preserved rather than keyed away.
    """
    png = scratch / "render.png"
    subprocess.run(["mutool", "draw", "-F", "png", "-w", str(width), "-o", str(png),
                    str(svg_path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    w, h, channels, _, data, _, _ = png_io.decode(str(png))
    alpha = bytearray(w * h)
    for i in range(w * h):
        base = i * channels
        if channels >= 3:
            lum = (data[base] * 299 + data[base + 1] * 587 + data[base + 2] * 114) // 1000
        else:
            lum = data[base]
        alpha[i] = 255 - lum
    return w, h, alpha


def write_layer(path, w, h, rgb, alpha):
    r, g, b = rgb
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        base = y * w
        for x in range(w):
            rows += bytes((r, g, b, alpha[base + x]))
    png_io.encode(path, w, h, rows, alpha=True)


def _colour(text):
    name, _, value = text.partition("=")
    if not value:
        name, value = "ink", text
    value = value.lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError(f"expected name=#rrggbb, got {text!r}")
    return name, tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", type=Path, help="SVG to split.")
    ap.add_argument("--out", type=Path, default=Path("layers"))
    ap.add_argument("--width", type=int, default=4000,
                    help="Raster width in px (default %(default)s). Aim high enough that the "
                         "editor is scaling down.")
    ap.add_argument("--colors", type=_colour, nargs="+", default=[("ink", (0, 0, 0))],
                    metavar="NAME=#RRGGBB", help="One or more fill colours to emit.")
    ap.add_argument("--gap", type=float, default=0.0,
                    help="Horizontal tolerance when clustering shapes, in user units.")
    ap.add_argument("--prefix", default=None, help="Filename prefix (default: source stem).")
    args = ap.parse_args()

    text = args.source.read_text()
    paths = _paths(text)
    if not paths:
        raise SystemExit(f"no <path> elements in {args.source}")
    svg_open, group_open = _header(text)
    groups = cluster(paths, args.gap)
    prefix = args.prefix or args.source.stem

    # `all` plus one per cluster. Deliberately not named for what the shapes *are* — this
    # cannot know, and a wrong semantic name is worse than an index.
    subsets = {"all": list(range(len(paths)))}
    for n, grp in enumerate(groups):
        subsets[f"g{n}"] = grp

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        for name, indices in subsets.items():
            subset = scratch / f"{name}.svg"
            body = "\n".join(f'<path d="{paths[i]}"/>' for i in sorted(indices))
            subset.write_text(f"{svg_open}\n{group_open}\n{body}\n</g>\n</svg>\n")
            w, h, alpha = rasterise_alpha(subset, args.width, scratch)
            for tone, rgb in args.colors:
                out = args.out / f"{prefix}_{name}_{tone}.png"
                write_layer(out, w, h, rgb, alpha)
                written.append(out)

    print(f"{args.source.name}: {len(paths)} path(s) -> {len(groups)} cluster(s)")
    print(f"{len(written)} layer(s) at {w}x{h} -> {args.out}")
    for path in written[:6]:
        print(f"  {path.name}")
    if len(written) > 6:
        print(f"  ... and {len(written) - 6} more")


if __name__ == "__main__":
    main()
