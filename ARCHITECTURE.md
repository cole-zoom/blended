# blended — Architecture

A compiler for Blender animations. You describe intent; the system emits a `.blend` and a video.

The user is assumed to know **nothing about Blender**. Every design decision below follows from that,
plus one more: **the LLM never writes `bpy`.**

---

## 0. Verified environment

| Thing | Value |
|---|---|
| Blender | 5.2.1 LTS, `/Applications/Blender.app/Contents/MacOS/Blender` |
| Blender Python | 3.13.13 (bundled) |
| Host Python | 3.13.7 |
| SVG import | `io_curve_svg` — bundled extension, available |
| Engines | EEVEE Next (Metal), Cycles |
| Raster→vector | none installed — only needed for the **fallback** ingest path (§5) |

---

## 1. The core idea: three layers, one source of truth

The planning discussion proposed a semantic "beats" IR. That's right as an *input* and wrong as *state*.
`"purpose": "build_tension"` is not compilable, and if it's the thing you persist, every edit re-runs an
LLM over the whole scene — which is exactly the regression problem you were trying to kill.

So: two IRs, with a **deterministic** pass between them.

```
  natural language  ─────────────────────────────────────┐
         │                                               │
         ▼                                               │
  ┌──────────────────┐   LLM authors this once           │
  │  L1  INTENT IR   │   beats · purpose · subject        │
  │                  │   "camera circles the logo"        │
  └────────┬─────────┘                                    │
           │  lower()  ← deterministic. no LLM. no I/O.   │
           ▼                                              │
  ┌──────────────────┐                                    │
  │  L2  SCENE IR    │   ★ SOURCE OF TRUTH ★              │
  │                  │   objects · tracks · library calls  │
  │                  │   absolute frames · resolved params │
  └────────┬─────────┘         ▲                          │
           │  build()          │  JSON Patch ◄────────────┘
           ▼                   │  (all later edits)
  ┌──────────────────┐         │
  │  L3  bpy         │  ───────┘
  │  keyframes/nodes │
  └──────────────────┘
```

**The rule that makes this work:** L2 becomes the source of truth the moment the first build succeeds.
L1 degrades into a changelog / provenance record. It is **never re-lowered**. Every subsequent edit —
whether from you or from the vision critic — is a **JSON Patch against L2**.

That single rule is what buys you:

> "Make the light ramp slower, keep everything else identical."
> → `{"op":"replace","path":"/tracks/2/duration","value":6.0}`
> → one channel changes. Camera, materials, framing provably untouched.

### What altitude does the LLM actually work at?

Between the two options in the discussion. It emits **library calls with semantic parameters**:

```json
{ "action": "camera.orbit",
  "target": "logo",
  "start": 0.0, "duration": 16.0,
  "params": { "revolutions": 0.75, "arc": "elevated", "easing": "ease_in_out" } }
```

Not `"purpose": "build_tension"` (uncompilable), and not `rotation_euler[2] = 0.43 @ frame 173`
(the thing that keeps breaking). The LLM picks from a **closed vocabulary** and fills a **typed schema**.
Its output space is small enough to validate before anything touches Blender.

---

## 2. The animation library — the whole product lives here

This is the quality ceiling. If a human hand-writing `scene.json` can't make something good,
no LLM on top of it will either.

Every action is a versioned Python function with four pieces of metadata:

```python
@action("camera.orbit", version=1)
class Orbit:
    params  = OrbitParams          # pydantic → JSON Schema → constrains the LLM
    writes  = ["camera.transform"] # ← channel footprint. the important one.
    timing  = Stretchable(min=1.0) # fixed | stretchable | minimum
    tags    = ["camera", "reveal", "cinematic"]
```

**Channel footprint is the feature no `bpy`-writing agent can have.** Two actions writing
`camera.transform` over overlapping frames is a **compile error with a source location**, not a
mysteriously broken render. That's the difference between a compiler and a prompt.

### Start with ~10 actions, not 40

The goal animation needs exactly four. Ship those, ship the goal, then grow.

```
camera/   orbit  dolly  track  focal_ramp
light/    ramp   three_point_rig  flicker
object/   fade_in  scale_in  spin  hold
```

Growth model: hand-tune a one-off → it looks great → **promote** it into the library with a schema
and a footprint. The library is a ratchet of things that are known to work.

---

## 3. Time: one clock, one quantization boundary

**`scene.duration` is an explicit input. It is never derived from the animations.** (The audio-owns-time
rule from the discussion, generalized — audio becomes just one possible clock owner later.)

Actions that overrun the clock are a compile error, or get time-scaled according to their `timing`
contract. They never silently extend the scene.

### Quantization: once, at the scene boundary

`16.58 × 24 = 397.92 frames`. Not an integer. This class of bug (drift, off-by-one, "why is my
animation 3 frames short") is why timing broke last time.

Fix: **seconds→frames quantization happens exactly once**, at the scene boundary, and the result is
recorded and reported. Everything downstream is integer frames — no float seconds survive past the
compiler front-end.

### v1 picks a duration where that's a no-op: **16.0s @ 30fps = 480 frames**

The original ask was "16.58s", but it only ever needed to be *~16s*. Choosing the number well makes
an entire class of problem vanish for v1:

```
16.0s │  24fps → 384f  ✓ exact
      │  25fps → 400f  ✓ exact
      │  30fps → 480f  ✓ exact   ← chosen
      │  60fps → 960f  ✓ exact
```

**Duration is fps-agnostic.** You can switch 30 → 60 for final delivery and re-time nothing.

And 480 factors beautifully — `2⁵ × 3 × 5`. Every natural beat subdivision lands on an integer frame:

| Split | Frames | Seconds |
|---|---|---|
| halves | 240 | 8.0 |
| **thirds** | **160** | **5.333** |
| quarters | 120 | 4.0 |
| sixths | 80 | 2.667 |
| eighths | 60 | 2.0 |

30fps over 24fps because a slow orbit is where 24's judder is most visible, and cinematic judder
fights a clean product-logo look. At draft resolution the render cost difference is noise.

**The quantization machinery stays regardless.** v1 just picked a number where it does nothing —
the general case (a user asking for 16.58s, or audio owning the clock later) still needs it.

---

## 4. Talking to Blender: subprocess, not `pip install bpy`

**Decision: subprocess.** `blender -b --python backend/__main__.py -- scene.json`

| | subprocess (chosen) | `pip install bpy` |
|---|---|---|
| Version | your real 5.2.1 LTS | PyPI lags; 5.2 likely unavailable |
| Addons | `io_curve_svg` etc. all present | must reimplement |
| GPU | Metal / EEVEE Next | often unavailable |
| `.blend` output | you can open it in the GUI | awkward |
| Crashes | isolated, recoverable | takes down your process |

The process boundary is a **feature** — it's the compiler backend, with a clean JSON in / JSON out
contract. And "you can open the `.blend` in the app and poke at it" matters a lot for a user
who is learning.

### The `blender/` source clone (345MB)

Not needed to run anything. Keep it **gitignored**, as a grep-able API oracle — RNA definitions,
bundled addon source, Freestyle modules are genuinely useful ground truth when the agent is unsure
about an API. Add `fake-bpy-module` stubs for editor/agent completion. Do **not** vendor it.

---

## 5. Assets: an ingestion ladder, not a tracing pipeline

"Make my logo 3D" is the hard part, not the animation. It deserves a real subsystem — and the
subsystem's job is **routing by input quality**, because the input is different every time.

### The ladder — best source wins, always

| Tier | Input | Path | Quality |
|---|---|---|---|
| **A** | **SVG / AI / PDF** | `io_curve_svg` → curves → mesh → extrude → bevel | **exact.** clean beziers, infinite resolution |
| **B** | Live text + font file | Blender text object → curves → extrude | **exact**, and stays editable as text |
| **C** | GLB / OBJ / FBX | import → normalize | as good as the source model |
| **D** | Procedural | geometry nodes / primitive ops | exact, fully parametric |
| **E** | **PNG / JPG** | threshold → vectorize → Tier A | ⚠️ **lossy fallback only** |

**Tier E is a fallback, never a default.** If a vector source exists, the raster path is never taken.
It exists for the case where a user genuinely has nothing else, and it flags itself as lossy in the
asset manifest so the vision critic knows to scrutinize shape fidelity.

### Two things that make this simpler than it looks

**Fill color is irrelevant.** Converting curves to mesh discards SVG fills entirely — you assign a
Blender material downstream. A logo SVG "in the wrong color" is a perfect input. Color is a
*style* concern in the IR, not an *asset* concern.

**Wordmarks are usually Tier B, not Tier A.** If the SVG has live `<text>`, you need the font — but
then you get real glyph curves, better than any trace, and the text stays editable. If the SVG has
outlined paths, Tier A handles it directly. A single logo often splits: marks via A, wordmark via B.

### Generated vs. downloaded — route by class of thing

| Class | Source | Why |
|---|---|---|
| Logos, icons, wordmarks | **generated** from vector/font | you have the authoritative source; tracing throws it away |
| Text, titles, captions | **generated** from font | parametric, restylable, no asset needed |
| Abstract / geometric shapes | **generated** procedurally | fully parametric, zero dependencies |
| Real-world objects (a horse, a chair) | **downloaded** / asset library | can't generate a horse. this is where a library matters |

For v1 — logos — you never download a model. Everything is generated from a vector or font source.
The download path is real but deferred; the resolver interface is designed so it slots in later.

### The normalization contract

Every path converges on the same normalized output, and **nothing downstream sees anything else**:

```
asset manifest {
  objects:  {logo_marks, logo_text}    ← stable names. L2 targets these.
  origin:   centered at bbox center
  scale:    normalized to unit bounds
  up_axis:  Z
  materials: stripped                  ← style layer owns all appearance
  provenance: {tier: "A", source: "logo.svg", lossy: false}
}
```

Content-hash cached. That naming contract is what lets Scene IR say `"target": "logo"` while knowing
nothing whatsoever about where the geometry came from.

### What `reference_photo.png` is actually for

Not geometry. It's the **visual target for the Tier-3 critic**: *"does the render look like this?"*
That's a much better use of it than tracing, and it's a job nothing else can do.

---

## 6. The black outline — IR names the *look*, engine picks the *technique*

The IR knob is `style.outline: {color, thickness, mode: auto}`. Three implementations behind it:

| Technique | Verdict |
|---|---|
| **Inverted hull** — dup mesh, flip normals, solidify out, black emission, backface cull | **Default.** Fast, EEVEE-compatible, deterministic, animation-stable |
| **Freestyle** | Alternate backend. Truest lines, CPU-only, slow |
| **Grease Pencil Line Art** | Highest quality, heaviest to script. Later |

This is the payoff of having an IR at all: you say "black outline", and you can swap the technique
six months from now without touching a single scene file.

---

## 7. ⚠️ A real conflict in the goal, worth stating up front

**"Camera pans around the logo" + "extruded flat logo" fight each other.**

An extruded 2D logo viewed edge-on at 90° is a **sliver**. A full 360° in-plane orbit spends a third
of its runtime showing you nothing readable. Inverted-hull outline thickness also reads differently
from oblique angles.

Fix, and it's a design constraint not a bug:
- orbit is a **partial elevated arc** (≈0.6–0.8 revolutions, camera raised 20–35°), never a flat 360°
- a Tier-2 probe asserts `logo.screen_coverage > 0.15` at **every** sampled frame

This is exactly the kind of thing the system should catch mechanically rather than after you watch
a 16-second render and go "huh."

---

## 8. Verification: three tiers, cheapest first

The discussion put vision feedback at the center. I'd demote it to **last resort**. Most failures are
catchable for free, deterministically, before a single pixel renders.

### Tier 1 — Static, on the IR (free, instant, no Blender)
- action params validate against schema
- every `target` resolves to a declared object
- **no channel conflicts** (the footprint check)
- total duration fits the clock
- assets resolve

### Tier 2 — Scene probes (cheap, deterministic, inside Blender, post-build **pre-render**) ★
The underrated tier. Inside `bpy` you can query ground truth without rendering — this is where
most of the value is, and where the goal file turns into a test suite:

```python
# goal.md: "camera must pan around the logo"
assert_constant(camera.distance_to("logo"), tolerance=0.05)   # orbit, not drift
assert_monotonic(camera.azimuth_over_time())                  # actually circling
# goal.md: "light source must go from dim to bright"
assert_monotonic_increasing(light.energy_over_time())
assert light.energy_at(0) < light.energy_at(END) * 0.2
# goal.md: implicit — you must be able to see the thing
for t in sample(0, END, 24):
    assert screen_coverage("logo", t) > 0.15
    assert in_frame("logo", t)
```

**The goal statement compiles into assertions.** That's the headline feature.

### Tier 3 — Vision critic (expensive, non-deterministic, last)
Contact sheet → vision model. Two hard rules:
1. It is **given the intent as a checklist** and returns per-item verdict + confidence + frame refs.
   Not "how does this look."
2. It **emits a JSON Patch in the L2 schema**, never prose. If it can't express the fix as a patch,
   it isn't actionable and gets surfaced to you instead.

It's only asked about what Tiers 1–2 structurally cannot check: readability, pacing, "does this feel
premium."

---

## 9. Render ladder + caching

| Level | Engine | Res | Frames | For |
|---|---|---|---|---|
| `draft` | EEVEE | 480p | every 8th → contact sheet | the agent loop |
| `preview` | EEVEE | 720p | all → mp4 | you, iterating |
| `final` | Cycles/EEVEE hi | 1080p+ | all → mp4/PNG seq | done |

Every render keyed by `hash(scene_ir + asset_manifest + engine_cfg + library_version)`.
Same hash → skip. Makes "did my patch actually change anything?" a decidable question.

---

## 10. Repo layout

```
blended/
├── goal/                       # north star (exists)
│   ├── goal.md
│   ├── reference_photo.png
│   └── acceptance.md           # goal → probe assertions
├── src/blended/
│   ├── cli.py
│   ├── ir/
│   │   ├── intent.py           # L1 schema
│   │   ├── scene.py            # L2 schema  ← the typed AST
│   │   ├── patch.py            # JSON Patch ops / apply / validate
│   │   └── lower.py            # L1 → L2, deterministic
│   ├── library/
│   │   ├── registry.py         # action registry: schema · footprint · timing
│   │   ├── actions/            # camera.py  light.py  object.py
│   │   └── styles/             # outline.py  materials.py
│   ├── assets/
│   │   ├── resolver.py         # route by tier → normalized manifest, content-hashed
│   │   ├── ingest/             # svg.py (A) · text.py (B) · model.py (C)
│   │   │                       # procedural.py (D) · raster.py (E, lossy fallback)
│   │   └── normalize.py        # → origin/scale/up-axis/naming contract
│   ├── engine/
│   │   ├── runner.py           # host: spawn blender, marshal JSON
│   │   └── backend/            # ← runs INSIDE blender's python
│   │       ├── __main__.py     #   read scene.json → build → probe → render
│   │       ├── build.py
│   │       ├── probe.py        #   Tier 2
│   │       └── render.py
│   └── verify/                 # static.py (T1) · probes.py · diagnostics.py
├── schemas/                    # ← the agent contract. generated from pydantic.
│   ├── scene.schema.json
│   └── library.json
├── docs/authoring.md           # how to author scene.json (the "system prompt")
├── CLAUDE.md                   # repo conventions + invariants
└── projects/
    └── lancedb_logo/
        ├── intent.json         # L1 — provenance
        ├── scene.json          # L2 — SOURCE OF TRUTH
        ├── history/0001.patch.json
        ├── assets/  renders/  probes/
```

No `agent/` module in v1 — see §12. The agent is Claude Code, and its interface is
`schemas/` + `docs/authoring.md` + CLI diagnostics.

---

## 11. CLI

```bash
blended new lancedb_logo --goal goal/goal.md
blended assets add goal/dark-lancedb-logo.svg --as logo   # ingest → normalized manifest
blended check                    # Tier 1. instant. no Blender. ← the agent's inner loop
blended build                    # → .blend + Tier 2 probe report. NO RENDER.
blended render --quality draft|preview|final
blended review                   # → contact sheet for Tier 3
blended patch <file.patch.json>  # apply · validate · record
blended history | diff | revert
```

`build` and `render` are **deliberately separate**. Build+probe is seconds and catches most errors
before you pay for pixels. Compile vs. link.

There is no `blended plan` in v1 — authoring `scene.json` is the agent's job, and `check` is how it
finds out whether it got it right.

---

## 11a. The staged pipeline — the organising structure

**Nothing in this project has ever been one-shot.** The logo outline took ten iterations and two
wrong conclusions. The lighting took four rounds. The ground material was rejected once outright.
The water flicker took three diagnostic passes. Every good result came from building a little,
showing it, and adjusting.

So the pipeline is built that way on purpose: **five stages, each ending at a human gate.**

| Stage | Question it answers | Renders | Deliberately suppresses | Cost |
|---|---|---|---|---|
| 1 · `assets` | Is the model right? | stills / turntable | lighting, motion, materials | seconds |
| 2 · `blocking` | Does the motion and timing work? | full motion, 480p, **clay** | materials, floor, environment | ~2 min |
| 3 · `materials` | What is everything made of? | key stills, **neutral reference light** | the scene's real lighting | ~1 min |
| 4 · `lighting` | Is the mood right? | full motion, 720p EEVEE | — | ~7 min |
| 5 · `final` | Ship it | Cycles, 1080p | — | ~80 min |

### Two principles make this work

**Render fidelity matches the decision, not the ambition.** Judging camera timing does not need
materials, and paying 80 minutes to find out the camera is wrong is the failure mode this exists
to prevent.

**Each stage suppresses what is not yet being decided.** This is the non-obvious half. Stage 2
forces flat clay materials *even when real ones exist*, because if you can see the materials you
will judge the look and miss that the timing is wrong. Blocking passes are grey in every animation
studio for exactly this reason — suppression is a feature, not a limitation.

The same logic drives stage 3's **neutral reference light**: materials are authored under a known
flat environment, so "is this concrete or is that just the orange HDRI?" cannot arise. That
question cost a full round in practice.

### Stage ownership of the IR

Each Scene IR field belongs to exactly one stage. That mapping is what makes gates meaningful —
without it, "has this stage changed?" is unanswerable.

| Stage | Owns |
|---|---|
| `assets` | `assets[].{source, extrude, bevel, resolution, target_size}` |
| `blocking` | `timeline`, `camera`, `tracks`, light `{type, azimuth, elevation, distance, spot_size}` |
| `materials` | asset `{material, base_color, roughness, metallic, wear, wetness, droplets}`, `outline`, floor `{material, texture, scale, wetness, bump, wet_*}` |
| `lighting` | light `{energy, color, radius}`, ramp track params, `world`, `environment.volumetrics`, `post` |
| `final` | render settings only — engine, samples, resolution |

Light *position* is blocking (it is composition); light *intensity and colour* are lighting (it is
mood). Splitting one object across two stages is a judgement call, but the alternative — putting
all lighting in one stage — means you cannot block a shot without also deciding its mood.

### Approval and drift

An approved stage records a hash of the fields it owns. When a later edit changes those fields,
the pipeline **warns and requires re-approval**, naming exactly what moved.

This is the "don't regenerate everything" concern from the project's first conversation, finally
landing somewhere concrete: approved work cannot silently regress, and changing a material can
never quietly move the camera. It also makes iteration cheap — approving blocking means stage 3
edits no longer rebuild stages 1 and 2.

Re-approval warns rather than hard-blocks, because upstream changes are often deliberate. The
point is that they are never *invisible*.

---

## 12. The agent in v1 is Claude Code — so the CLI *is* the agent interface

v1 has **no API calls and no `agent/` module.** The planner and patcher are Claude Code, working in
this repo, driving the same CLI you drive.

This is a genuine architectural improvement, not just deferred work:

**The schemas are the interface either way.** A hand-authored `scene.json` exercises the exact JSON
Schema an API call would fill. The contract gets validated for free, and swapping in a programmatic
agent later is a drop-in — nothing about the surface changes.

**It collapses the feedback loop.** Instead of building a compiler, then building an agent, then
discovering the IR isn't expressive enough — the agent is in the loop from day one, hitting the IR's
limits while you can still cheaply change it.

**It removes the whole non-determinism budget from v1.** No prompt engineering, no retry loops, no
API cost, no flaky-output handling. All of it deferred until the compiler is known-good.

### What this replaces `agent/` with

```
schemas/scene.schema.json     generated from pydantic. the contract.
schemas/library.json          every action: params · footprint · timing · tags
docs/authoring.md             how to write scene.json. the "system prompt", as docs.
CLAUDE.md                     repo conventions, pointers, invariants
```

The loop is identical to the programmatic one, just with a different driver:

```
   author scene.json  ──►  blended check  ──►  diagnostic  ──┐
          ▲                                                   │
          └───────────────────────────────────────────────────┘
                            read, patch, retry
```

### The consequence that actually matters: **diagnostics are now the API**

If a model is the primary consumer of `blended check`, then error messages stop being a nicety and
become the load-bearing interface. Every diagnostic emits machine-readable JSON:

```json
{ "code": "CHANNEL_CONFLICT",
  "severity": "error",
  "channel": "camera.transform",
  "conflicting_tracks": [2, 5],
  "frames": [180, 240],
  "message": "tracks 2 (camera.orbit) and 5 (camera.dolly) both write camera.transform on frames 180-240",
  "suggested_fix": { "op": "replace", "path": "/tracks/5/start", "value": 8.0 } }
```

`suggested_fix` in the patch schema means most failures are mechanically recoverable. This is worth
building well even after an API agent arrives — it's what makes the retry loop converge.

### Tier 3 works too

Claude Code reads PNGs directly, so the vision critic needs no API either. `blended review` writes a
contact sheet to `projects/*/renders/contact/` and the agent looks at it against
`goal/reference_photo.png` and the intent checklist.

**Deferred, not designed away:** a programmatic `agent/` module remains the obvious Phase 6. It
consumes the same schemas, CLI, and diagnostics — so it's additive.

---

## 13. Where I'd push back on the original plan

| Discussion said | Adjustment |
|---|---|
| Beats IR is the representation | Beats are a great *input*, bad *state*. Two layers; L2 is truth after first build |
| "The renderer decides how to accomplish those things" | Hides enormous intelligence in "the renderer." Be honest: lowering is a lookup table + defaults. The LLM picks library actions + params |
| Immutable per-object scene files | Same safety, less awkwardness, via **channel ownership + patches**. Files-per-object gets ugly fast |
| Vision feedback is the biggest upgrade | It's the *last* tier. Tier-2 scene probes are cheaper, deterministic, and catch more |
| SRT / audio timeline | Not in this goal. Keep the `Timeline` abstraction so audio slots in later as one clock owner; don't build SRT parsing now |
| Clone Blender source | Not needed at runtime. Gitignore it, keep as API oracle |
| Raster logo → vectorize → 3D | Tracing is the **worst** ingest path. Use the vector/font source when it exists; raster is a lossy fallback (§5). The reference PNG becomes the *visual target*, not the geometry source |
| LLM planner as a module | v1's agent is Claude Code driving the CLI. Same schemas, no API, and it makes **diagnostics** the real interface (§12) |
