# blended — conventions

Read [ARCHITECTURE.md](ARCHITECTURE.md) first. This file is the short list of things that are easy
to get wrong.

## The three hard rules

**1. Never hand-write `bpy` outside `src/blended_backend/`.**
The whole point of the project is that animation is compiled, not scripted. If you find yourself
wanting to emit `bpy` from the host, you actually want a new library action (`library/actions/`).

**2. `blended_backend` imports stdlib and `bpy`. Nothing else. Ever.**
It runs inside Blender's bundled Python (3.13.13), which has no access to the host venv. It must
never import `blended`, `click`, `pydantic`, or anything from `pyproject.toml` dependencies.
The two packages talk over JSON files, never over Python imports.

```
src/blended/          host   · system Python 3.13 · deps allowed
src/blended_backend/  guest  · Blender Python     · stdlib + bpy ONLY
                             ↕ JSON files only
```

**3. Scene IR is the source of truth once a build succeeds.** Edits are patches, never
regenerations. See ARCHITECTURE §1.

## Blender 5.2 gotchas (verified on this machine)

| Gotcha | Consequence |
|---|---|
| **Blender exits 0 even on an uncaught Python exception** | Pass `--python-exit-code 1` (verified: fixes it). Belt-and-braces: the backend also traps everything and calls `sys.exit(1)`, and the runner treats a missing result file as failure |
| **`action.fcurves` no longer exists.** 4.4+ uses slotted actions: `action.layers[N].strips[M].channelbag(slot).fcurves` | Always go through `blended_backend.fcurves` — never touch f-curves directly |
| `image_settings.media_type` must be `'VIDEO'` **before** `file_format='FFMPEG'` is valid | Set media type first, or you get a confusing enum TypeError. New in 5.x |
| Blender appends `0001-0060` to video filenames | Set `use_file_extension = False` and own the full path |
| Engine id is `BLENDER_EEVEE`, not `BLENDER_EEVEE_NEXT` | The 4.2-era name is wrong here |
| Cycles is not registered in background mode by default | Needs `addon_utils.enable('cycles')` |
| Blender's stdout is extremely noisy | Results go to a **file**, never stdout. stdout is captured only for diagnostics |
| New keyframes default to BEZIER (eases in/out) | Set LINEAR explicitly for constant-rate motion (spins, orbits) |
| **A new object's `bound_box` is an uninitialised unit cube (−1…1) until the view layer updates** | `normalize.world_bounds()` calls `view_layer.update()` first. Reading early inflated every derived measurement ~9× with no error anywhere |
| `bound_box` ignores modifiers | Measure through `obj.evaluated_get(depsgraph)` |
| `transform_apply` does not rescale modifier parameters | Add geometry modifiers *after* normalization, or their strength silently shrinks by the normalization factor |
| `bpy.ops.import_curve.svg` needs `addon_utils.enable("io_curve_svg")` under `--factory-startup` | `ingest.svg.ensure_importer()` |
| Freestyle emits no strokes under EEVEE in 5.2 (`strokes set empty`) | Not a usable outline backend here |
| **A world Volume Scatter renders the frame completely black** in EEVEE 5.2 — at any density (tested to 0.001) and any light energy | Use a bounded volume box instead (`staging.add_atmosphere`) |
| Volumetric shadows cost ~10× render time, and `volumetric_samples` is *not* the driver (96→32 saved only 13%) | Budget for it; don't tune samples expecting a win |
| A `SUN` lamp ignores position entirely — only rotation matters | `add_sun` takes no `distance`; accepting one would imply an effect it cannot have |
| **Relative image paths break once a `.blend` is saved** — Blender re-resolves them against the .blend's directory, not the cwd. A failed image load renders as flat **magenta** with no error | Texture paths are absolute; `materials.pbr` raises on relative or missing paths |
| `addon_utils.enable("cycles", default_set=False)` loads the add-on but never registers the engine | Use `default_set=True`, then `render.engine = "CYCLES"` |
| First Cycles render on Metal spends **~2.5 min compiling kernels**, then caches them | Never benchmark Cycles on a cold first frame |
| Cycles adaptive sampling makes 128 and 256 samples nearly the same cost | Raising the sample cap is cheap; it stops early once converged |

## Outline techniques — what was tried

Recording the dead ends so they aren't retried (details in `styles.add_lineart`):

| Technique | Result |
|---|---|
| **Grease Pencil Line Art** | ✅ **In use.** A real line renderer — traces silhouettes in screen space per frame, so width is uniform from *any* angle. Costs ~78 ms/frame at 960×540 (~50% overhead; 113s for 480 frames) |
| Displace-inflate + depth separation | Clean head-on, but **parallax**: the outline is a solid copy behind the logo, so any depth gap slides it sideways off-axis. Kept as `--outline-mode hull` |
| Solidify | Gives thickness to *open* surfaces. On a closed solid it builds an inner wall and leaves the silhouette unchanged — looks like it does nothing |
| True inverted hull (flip faces + backface culling) | Textbook, but on flat-faced extrusions the shell pokes through the front cap and streaks the letters. Invisible at any thickness safe enough to avoid that |
| Curve `offset` | Naive per-point offset, not a real polygon offset: self-intersects, closes small counters, merges letters into a slab. Kept as `--outline-mode offset` |
| Freestyle | Produces no strokes under EEVEE 5.2 (`strokes set empty`) |

**The lesson:** any outline built from geometry sitting behind the object is partly occluded by
that object and parallaxes when the camera moves. Only a screen-space line renderer is stable
under an orbiting camera.

Line Art specifics: thickness is `radius` (renamed from `thickness` in 5.x); creases default OFF
(they read as busy hatching on glyphs); layer `use_lights = False` so the stroke stays black while
the key light ramps dim→bright.

## Layout

```
src/blended/            cli.py · config.py · errors.py · engine/{runner,result}.py
src/blended_backend/    __main__.py · scene.py · render.py
goal/                   the north-star target + acceptance criteria
blender/                Blender source clone. gitignored. API oracle only, NOT a dependency.
```

## Commands

```bash
uv sync                                   # install
uv run blended doctor                     # verify the Blender bridge
uv run blended new NAME --asset logo.svg  # scaffold a project that already renders
uv run blended schema                     # regenerate schemas/ from the live models
uv run pytest
```

Then the staged workflow — see [docs/authoring.md](docs/authoring.md):

```bash
uv run blended stage assets   scene.json   # clay turntable, ~3s
uv run blended approve assets scene.json
uv run blended stage blocking scene.json   # motion, clay, ~2min
uv run blended stage lighting scene.json   # mood, ~7min
uv run blended stage final    scene.json   # Cycles, resumable
uv run blended status         scene.json
```

Live reload — rebuild in an open Blender viewport, ~40ms, no render:

```bash
uv run blended watch scene.json    # then install addon/blended_live.py in Blender
```

The add-on imports `blended_backend` directly. That only works because the backend is
constrained to stdlib + `bpy`, which makes it as valid inside the GUI as in a background render.
It calls `scene.clear()`, **never** `reset()` — the latter calls `read_factory_settings` and
would throw away the open file.

## Where things are documented

| For | Read |
|---|---|
| Getting set up / live reload | [docs/setup.md](docs/setup.md) |
| Writing a scene | [docs/authoring.md](docs/authoring.md) — the traps, not just the fields |
| Field reference | `schemas/` — generated, never hand-edited |
| Why it is shaped this way | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What is built and what is next | [ROADMAP.md](ROADMAP.md) |

**No L1 "intent" IR, deliberately.** The roadmap made it conditional on Phase 2 showing a need
for a semantic layer above Scene IR; it did not. Hand-authoring Scene IR was comfortable
throughout, and every production edit was a direct field change.
