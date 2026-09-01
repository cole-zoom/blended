# tools

Standalone asset-preparation scripts. **Not part of the `blended` package** — nothing in
`src/` imports them and they never touch Blender. They take a file in, do arithmetic on pixels
or path data, and write a file out.

They live here rather than in a project folder because they are general: point them at any
image, any frame sequence, any SVG. Anything with a specific brand's palette or geometry baked
into it belongs with that project, not here.

Run them directly — no CLI subcommand, no import:

```bash
uv run python tools/svg_layers.py artwork.svg --out layers/
```

## What each one makes

| | In → out |
|---|---|
| `svg_layers.py` | **SVG → transparent PNGs.** Splits artwork into per-shape layers, each rendered on the *full* source canvas so they restack in register with no transform. For editors that cannot read SVG — Resolve has no SVG loader in the media pool or in Fusion. |
| `prepare_plate.py` | **Image → compositing plate.** Removes horizontal streaks, and optionally emits a taller version whose bottom rows ramp to transparent for blending against a layer beneath. |
| `extend_footage.py` | **Frame sequence → taller frame sequence.** Synthesises sky above each frame, derived per-frame so it tracks footage whose sky drifts. For plates that must survive a vertical pan. |

Two shared modules, not tools in their own right:

| | |
|---|---|
| `png_io.py` | Minimal 8-bit PNG decode/encode. Exists because `blended` declares three dependencies and none of them read images |
| `svg_paths.py` | SVG path parser. Computes real bounding boxes from relative commands |

## External binaries

`svg_layers` and `prepare_plate` shell out to **`mutool`** (mupdf-tools) to rasterise SVG and
JPEG. `blended doctor` does not check for it — these are not part of the compiler.

```bash
brew install mupdf-tools
```

Tracing raster art to vector additionally wants **`potrace`**.

## A note on the technique comments

Each file carries a longer docstring than its size suggests it needs. That is deliberate: every
one of these encodes at least one approach that looked obviously correct and rendered wrong —
a mirror that reverses a gradient, a feather that reveals a colour mismatch, an alpha ramp
anchored one row off. The measurements are recorded so the next person does not have to
rediscover them by eye.
