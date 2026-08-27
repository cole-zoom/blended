# Roadmap

## The ordering principle

**Build the compiler backend and hit the goal by hand, before formalizing the agent.**

If a `scene.json` written by hand cannot produce a good 16s LanceDB animation, then one written by an
agent certainly cannot. The library's quality *is* the product's quality ceiling.

**In v1 the agent is Claude Code, not an API call** (ARCHITECTURE §12). That has a pleasant
consequence for the ordering: the "hand-written" `scene.json` in Phase 2 *is already agent-authored*.
So Phase 4 isn't building a planner — it's **formalizing the contract that Phase 2 discovered**.

---

## Phase 0 — Bridge ✅ **COMPLETE**
*Prove the round-trip. Nothing else.*

- [x] `pyproject.toml`, uv, Python 3.13
- [x] `engine/runner.py` → spawn `blender -b --python blended_backend/__main__.py -- --job J --result R`
- [x] JSON in, structured result out — via a **result file**, not stdout parsing
- [x] Hardcoded spinning cube → `.blend` + mp4
- [x] Blender path discovery + version assertion
- [x] `blended doctor` — one command to verify the bridge
- [x] Host/backend import boundary enforced by test
- [x] 9 tests passing

**Done:** `blended render-demo` produces `out/demo.mp4` (2.000s, H.264, 60f @ 30fps) and
`out/demo.blend`, openable in the GUI.

### What Phase 0 pinned down

Five Blender 5.2 API facts that would each have cost real time later — all now encoded in
[CLAUDE.md](CLAUDE.md) and covered by tests:

| Finding | Where it's handled |
|---|---|
| Blender exits **0** on an uncaught Python exception | `--python-exit-code 1` + backend traps + result-file-is-truth |
| `action.fcurves` is gone (4.4+ slotted actions) | `blended_backend/fcurves.py` — the only place f-curves are touched |
| `media_type='VIDEO'` must precede `file_format='FFMPEG'` | `render._configure_video` |
| Blender appends `0001-0060` to video filenames | `use_file_extension = False` |
| `BLENDER_EEVEE`, not `BLENDER_EEVEE_NEXT`; Cycles unregistered in `-b` | `render.available_engines()` / `enable_cycles()` |

**Design decisions that turned out to matter more than expected:**

- **Results go to a file, never stdout.** Blender's stdout is unparseable noise. This made the
  failure path trivially reliable — a backend exception surfaces as a real traceback with file
  and line, which is exactly the Phase 3 diagnostic shape.
- **`src/blended` and `src/blended_backend` are separate top-level packages.** The boundary is
  physical, not conventional, and `tests/test_boundary.py` fails the build if the backend ever
  imports the host or a third-party package. This is the invariant most likely to erode silently.
- **The workdir is preserved on failure, deleted on success.** `job.json` + `result.json` +
  `blender.log` are exactly what you need to debug, and `BackendError` carries the path.

---

## Phase 1 — Asset ingestion ✅ **COMPLETE**
*`dark-lancedb-logo.svg` → beveled 3D geometry with a clean black outline.*

**Tier A (SVG) only.** Skip raster tracing entirely — it's the fallback path (ARCHITECTURE §5) and
nothing in v1 needs it.

- [x] Source SVG — `goal/dark-lancedb-logo.svg`
- [x] Wordmark is Tier A — 0 `<text>` elements, already outlined
- [x] `ingest/svg.py` — `io_curve_svg` → curves, fills discarded
- [x] **`evenodd` preserved** — verified via `holes = loose_parts − euler`
- [x] **Split the single path** — largest-X-gap detection, no hardcoded threshold
- [x] curve → mesh → extrude → bevel
- [x] **Manifold check** — 0 non-manifold edges on both parts
- [x] `normalize.py` — origin centred, unit bounds, Z-up, upright, materials stripped
- [x] Light base material
- [x] Outline (Displace-inflate + depth separation)
- [x] Content-hash asset cache (`assets/resolver.py`)
- [x] `blended render-logo` → lit 3D still
- [x] 20 tests passing

**Done:** `uv run blended render-logo goal/dark-lancedb-logo.svg` produces a lit 3D logo with a
black outline. Measured: split at x=0.0706 (gap 0.0283), aspect 4.17, both parts manifold,
`logo_text` 26 parts / 22 holes, `logo_marks` 7 parts / 5 holes.

### What Phase 1 pinned down

The SVG behaved exactly as predicted (1 path, 15 splines, all cyclic). **The outline was the hard
part and took ~10 iterations** — three separate bugs compounded, each making the others look worse:

| Bug | Symptom | Cause |
|---|---|---|
| Bounds inflation | Everything ~9× too big | `bound_box` on a new object is an uninitialised unit cube until `view_layer.update()` |
| Materials wiped | Outline rendered white | `normalize()` strips materials, and it ran *after* the outline was painted |
| Modifier scale | Outline 27× too thin | `transform_apply` bakes scale into vertices but not into modifier parameters |

Four outline techniques were tried; **Solidify does not inflate a closed solid at all** (it builds
an inner wall), and Freestyle emits no strokes under EEVEE 5.2. Full table in [CLAUDE.md](CLAUDE.md).

The lesson worth carrying into Phase 2: **measure, don't squint.** Several rounds were spent
guessing from renders when one `evaluated_get(depsgraph).bound_box` dump answered it immediately.
That measurement is now a permanent probe (`outline_bounds`) with a test.

**Not in this phase:** `ingest/raster.py` (Tier E) and any vectorizer dependency. Deferred until a
real use case demands it — at which point `vtracer` is the pick (pip-installable, no C toolchain).

### Outline: resolved properly (post-review)

The first Phase 1 outline was geometry-based and **parallaxed off-axis** — spotted on review of the
hero still, where the black had bunched to the upper-right of the wordmark and vanished from the
left of the `L`. Root cause: the outline is a solid copy behind the logo, and the depth gap between
them displaces on screen as `gap × tan(angle)`. Shrinking the gap from ~26% to 3.8% of logo depth
helped 7× but could not fix it in principle.

**Replaced with Grease Pencil Line Art**, a real screen-space line renderer: uniform width from any
angle, counters correct, no parallax. Costs ~78 ms/frame (113s for the full 480-frame render).
The geometry techniques remain as `--outline-mode hull|offset` for asset types Line Art handles
poorly. Full comparison in [CLAUDE.md](CLAUDE.md).

This removes the constraint that would have forced a narrow orbit — **Phase 2 is free to choose the
arc on composition grounds alone.** ARCHITECTURE §7's legibility limit (the 4.29:1 wordmark
foreshortening to a sliver) still applies and is still enforced by the coverage probe.

---

## Phase 2 — Library + the goal, by hand ✅ **COMPLETE**
*Ship the goal video with no schemas, no planner, no agent formalism.*

**Target: 16.0s @ 30fps = 480 frames.** Achieved with **zero drift**.

- [x] `library/registry.py` — param schema, channel footprint, timing contract
- [x] `camera.orbit` — elevated partial arc, built as a rotating pivot so constant distance is
      structural rather than keyframed
- [x] `light.ramp` — energy dim→bright, easing. Ratio 24× (goal asks ≥5×)
- [x] `object.hold`, `object.spin`
- [x] L2 `ir/scene.py` (pydantic)
- [x] `build.py` — L2 → bpy, single quantization boundary
- [x] **Tier 1 static checks** — pulled forward from Phase 3, because the channel-footprint idea
      is worthless without the checker that uses it
- [x] `blended check` / `blended render --quality draft|preview|final`
- [x] `projects/lancedb_logo/scene.json` + `scene_plain.json`

**Done:** two 16.000s videos at 1280×720 — outlined and plain — from hand-authored Scene IR.
Render cost 129s (outlined) / 96s (plain).

### What Phase 2 established

**Tier 1 is real, not decorative.** Deliberately corrupting a scene produces four distinct
diagnostics with machine-readable `suggested_fix` patches and exit 1 — unknown target, invalid
param, track past the clock, and the **channel conflict** naming both offending tracks. That last
one is the check no `bpy`-writing agent can offer.

**The pivot-based orbit was the right call.** Keyframing camera positions along an arc lets Bezier
handles drift the camera off the circle between keys; a rotating parent makes constant distance
structural. Two keyframes on one channel, and the Tier-2 `distance_to` probe cannot fail for
interpolation reasons.

**A composition bug the checker could not catch:** the first render ended on the logo's *shadowed*
side, because the camera finished at +52° while the key light sat at −38° — so the brightest moment
showed the darkest face. Fixed by ending the orbit near head-on (+6°) with the key at −28°. Worth
noting as the class of problem that motivates Tier 3: structurally valid, aesthetically wrong.

Beat sketch — 480 divides cleanly, so pick from thirds (160f) or eighths (60f):

```
0 ─────────── 160 ─────────────────── 320 ─────────── 480
│  establish   │      orbit + ramp      │    settle     │
│  dim, static │   camera arcs, light   │  hero framing │
│              │   climbs to full       │  light peaks  │
0.0s        5.33s                   10.67s          16.0s
```

**Done when:** the goal animation exists as an mp4 and you like it.
Everything after this makes it *repeatable and editable*, not *possible*.

---

## Phase 2.5 — Environment refactor
*Sun, stone floor, black sky, rising orbit. Driven by review feedback on the Phase 2 renders.*

- [x] `Light.type: "sun" | "area"` — a real directional light at the sun's true 0.526° angular
      diameter. `add_sun` takes **no** `distance`: a directional light has none
- [x] `environment` section in the IR: `floor` + `volumetrics`
- [x] `materials.py` split out of `styles.py`; procedural stone from Voronoi
      (`DISTANCE_TO_EDGE` → crack network) + Noise (grain), relief via Bump so there is no
      geometry cost across 480 frames
- [x] `camera.orbit` extended: `start/end_elevation`, `start/end_distance_scale` — camera rises
      off the floor and pulls away. Still one pivot, so the sweep stays exactly circular
- [x] Sun peaks at 10s: `light.ramp` 0→10s then `object.hold` 10→16s
- [x] 17 Tier-1 tests, all Blender-free (0.11s)

### What this pinned down

**A world Volume Scatter renders completely black in EEVEE 5.2** — at any density down to 0.001
and any sun energy up to 60. It cost several isolation passes to find, because the symptom
(a black frame) looks identical to a lighting or framing bug. A **bounded volume box** works, and
is better practice anyway: it concentrates volumetric samples on the subject instead of spreading
them across infinite empty space.

**Volumetrics cost ~10× render time** — 129s → ~11 min for 480 frames at 720p. `volumetric_samples`
is *not* the driver (96→32 saved only 13%); the cost is volumetric shadow marching.

**The PBR seam is in place.** `materials.stone()` takes a `texture_set` argument that is unused
while procedural. Swapping in Poly Haven maps later touches the resolver, not the IR — the scene
file keeps saying `"material": "stone"`.

---

## Phase 3 — Staged pipeline + verification
*Formalise the workflow that actually produced everything so far.*

Originally scoped as verification alone. Restructured after Phase 2.5, because the session that
built the LanceDB animation demonstrated the real lesson: **nothing here is one-shot.** The logo
outline took ten iterations and two wrong conclusions; lighting took four rounds; the ground
material was rejected outright; the flicker took three diagnostic passes.

Verification is not the structure — it is what makes each *gate* trustworthy. The structure is
five stages, each ending at a human check-in (ARCHITECTURE §11a).

### 3a — Stage machinery

- [ ] `stages.py` — the five stages, and the IR fields each one owns
- [ ] **Overrides per stage**: clay materials for `blocking`, neutral reference light for
      `materials`. Suppression is the point — you cannot judge timing through a finished look
- [ ] `blended stage <name>` — build, probe, render at that stage's fidelity, stop
- [ ] Approval ledger: hash the fields a stage owns; `blended approve <stage>`
- [ ] **Drift detection** — warn and require re-approval when an approved stage's inputs move,
      naming exactly what changed
- [ ] Skip rebuilding upstream stages that are approved and unchanged

### 3b — Verification (the original Phase 3)

- [ ] Tier 1 static: schema, targets resolve, **channel conflicts**, duration fits *(mostly done
      in Phase 2)*
- [ ] Tier 2 probes in-Blender, **routed per stage**: geometry probes at `assets`; `screen_coverage`,
      `in_frame`, `distance_to`, `azimuth_over_time`, `energy_over_time` at `blocking`; texture and
      colourspace at `materials`; flicker and contrast at `lighting`
- [ ] Wire `goal/acceptance.md` to real probes *(treat as the current working target, not a fixed
      spec — it will move as the project does)*
- [ ] **`diagnostics.py` — structured JSON errors with `suggested_fix` patches** (ARCHITECTURE §12)
- [ ] Probe report per stage
- [ ] Contact-sheet generation

### 3c — Render robustness

- [ ] **PNG sequence + encode step.** An mp4 needs its trailing atom, so an interrupted render
      currently loses everything — a timeout kill cost a full hour of Cycles this session. A frame
      sequence survives interruption and lets a restart skip completed frames
- [ ] Progress reporting from frame logs
- [ ] Flicker metric (`temporal second difference`) as a standing probe, not a one-off script

**Done when:** `blended stage blocking` renders clay motion in ~2 minutes with a probe report, and
changing a material afterwards cannot silently move the camera.

---

## Phase 4 — Formalize the agent contract ✅ **COMPLETE**
*No planner to build. Publish what Phase 2 already proved, so it's repeatable.*

- [x] `blended schema` emits `scene.schema.json`, `library.json`, `stages.json` from the live
      models, so the published contract cannot drift from the code
- [x] `docs/authoring.md` — the traps, not just the fields
- [x] `CLAUDE.md` updated; README points at the guide
- [x] **`blended new`** — scaffolds a project that already renders. Not in the original scope,
      but it was the actual usability gap: a second project previously meant hand-writing
      `scene.json` from nothing
- [x] ~~L1 `intent.py` + `lower.py`~~ — **deliberately skipped.** The roadmap made this
      conditional on Phase 2 showing a real need for a semantic layer above Scene IR, and it did
      not: hand-authoring Scene IR was comfortable, the shipped scene is under 1,800 characters,
      and every production edit was a direct field change. A semantic layer would be indirection
      solving a problem never hit. A test asserts the decision stays made.
- [ ] Cold-start test: from `goal.md` alone, **in a fresh session**, author a `scene.json` that
      passes `check`. Deliberately not run by the session that wrote the docs — it cannot unknow
      the IR, so it would pass from memory and prove nothing. Whatever a fresh session fumbles
      is a documentation bug.

**Done when:** the cold-start test converges using only `check` diagnostics as feedback.

---

## Live reload ✅ **COMPLETE** *(unplanned; slotted before Phase 5)*
*Rebuild in an open Blender viewport instead of rendering to look at something.*

- [x] `blended watch` — resolves textures, validates, publishes `*.resolved.json`, republishes
      on change
- [x] `addon/blended_live.py` — Blender add-on: reload button, auto-watch modal timer,
      stage status, frame-subject
- [x] `scene.clear()` — empties built content **without** `read_factory_settings`, so the open
      file, viewport angle and selection all survive
- [x] 14 tests

**Done:** editing `scene.json` updates the viewport in **~36ms**. Measured across repeated
rebuilds with no duplicate objects and no orphaned meshes.

The architectural payoff was unplanned: `blended_backend` has been restricted to stdlib + `bpy`
since Phase 0 purely to keep the host/guest boundary honest. That restriction is exactly what
lets the *same* build code run inside the GUI — no subprocess, no second implementation.

A modal timer rather than a thread, because `bpy` is not thread-safe and touching scene data off
the main thread crashes Blender rather than merely misbehaving.

---

## Phase 5 — Iteration loop
*The thing that makes it usable.*

- [ ] `patch.py` — JSON Patch apply/validate/revert + history
- [ ] `blended review` — contact sheet + intent checklist → Claude reads it against
      `reference_photo.png` → returns a patch, not prose
- [ ] Render cache keyed by IR hash
- [ ] `blended history` / `diff` / `revert`

**Done when:** "make the camera slower, change nothing else" produces a one-line diff and a render
provably identical everywhere else.

---

## Phase 6 — Programmatic agent *(deferred, additive)*

Only once Phases 0–5 are solid. It consumes the **same** schemas, CLI, and diagnostics — so it adds
a driver, it doesn't change the architecture.

- [ ] `agent/planner.py`, `agent/patcher.py` — API-driven
- [ ] Retry loop on `check` failure, bounded attempts
- [ ] Model selection, cost tracking

---

## Deferred (explicitly not now)

- Raster ingest (Tier E) + vectorizer dependency
- Audio / SRT timeline — keep the `Timeline` seam, don't build it
- Downloaded 3D assets / asset marketplace / HDRI fetching
- Rigged characters, physics, particles
- Multi-scene sequences, cuts
- GUI

---

## Decisions resolved

| Decision | Resolution |
|---|---|
| Duration | **16.0s** — exact at 24/25/30/60fps |
| Frame rate | **30 fps** → 480 frames (`2⁵×3×5`, every subdivision integral) |
| Logo source | **`dark-lancedb-logo.svg`** (Tier A). Wrong fill color is irrelevant — materials are assigned in Blender |
| `reference_photo.png` | Demoted to **Tier-3 visual target**, not a geometry source |
| Vectorizer | **Not needed in v1.** `vtracer` if Tier E ever ships |
| Outline | **Inverted hull** default; Freestyle as alternate backend |
| Agent | **Claude Code + CLI**, no API in v1 |
| Blender interface | **Subprocess** to installed 5.2.1, not `pip install bpy` |

## Still open

| Decision | Resolve by |
|---|---|
| Final engine: EEVEE Next vs Cycles | Phase 2 — EEVEE first, escalate only if the look demands it |
| Whether L1 Intent IR is needed at all | Phase 4 — see the conditional above |
| Logo base material (white? metal? emissive?) — must contrast with the black outline | Phase 1 |
| Orbit arc + elevation that keeps a 4.29:1 object readable | Phase 2, driven by the coverage probe |
