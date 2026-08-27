# Authoring a scene

How to write and iterate on a `scene.json` without reading the compiler's source.

Field-by-field detail lives in `schemas/` (regenerate with `blended schema`). This document
covers the parts you would not guess.

---

## The one rule

**Never write `bpy`.** A scene declares *what* should happen; the compiler decides *how*. If you
find yourself wanting to express something the action library cannot, the answer is a new library
action, never an escape hatch.

---

## Start here

```bash
blended new myproject --asset path/to/logo.svg
blended check projects/myproject/scene.json
```

The scaffold already has a camera move and a light ramp, so `blended stage blocking` produces
motion on the first run. Editing something that moves beats authoring from nothing.

---

## The workflow is staged, and the stages matter

Five stages, each answering one question and stopping for review. Run them in order.

```bash
blended stage assets    scene.json    # Is the model right?          ~3s
blended approve assets  scene.json
blended stage blocking  scene.json    # Does the motion work?        ~2min
blended approve blocking scene.json
blended stage materials scene.json    # What is it made of?          ~1min
blended stage lighting  scene.json    # Is the mood right?           ~7min
blended stage final     scene.json    # Ship it                      ~80min
```

**Each stage hides what it is not deciding.** `blocking` renders in flat grey clay even when real
materials exist, because a finished-looking frame makes you judge the look and miss that the
timing is wrong. `materials` renders under a fixed neutral light so "is that concrete, or just the
orange environment?" cannot arise. This is deliberate; do not work around it.

`blended status scene.json` shows where every stage stands.

### Approval and drift

Approving a stage freezes the IR fields it owns. Change one later and the pipeline warns, naming
exactly what moved, and blocks *downstream* stages until you re-approve (`--force` overrides).

Reverting a change restores approval automatically, so experiments are free.

**Ownership is per-field, and one boundary surprises people:** a light's *position* belongs to
`blocking` (it is composition) while its *energy and colour* belong to `lighting` (it is mood).
So moving a lamp invalidates blocking; brightening it does not.

---

## Structure

```jsonc
{
  "name": "myproject",
  "timeline": { "duration": 16.0, "fps": 30 },   // the authoritative clock
  "assets":   [ { "id": "logo", "source": "logo.svg", ... } ],
  "camera":   { "lens": 55.0, "margin": 1.25, "dof": {...} },
  "lights":   [ { "id": "key", "type": "spot", ... } ],
  "world":    { "color": [0,0,0,1], "hdri": "moonless_golf" },
  "environment": { "floor": {...}, "volumetrics": {...} },
  "post":     { "bloom": 0.0 },
  "tracks":   [ { "action": "camera.orbit", "target": "camera", ... } ]
}
```

### Timeline owns time

`duration` is an input, never derived from the animation. A track that runs past it is an error,
not a scene extension.

**Pick a duration that divides evenly.** 16.0s at 30fps is exactly 480 frames; 16.58s at 24fps is
397.92 and has to round. `blended check` reports the drift either way, but zero is free if you
choose the number well. 16.0s is exact at 24, 25, 30 and 60fps, so frame rate stays a free choice.

### Tracks are library calls, not keyframes

```jsonc
{ "action": "camera.orbit", "target": "camera", "start": 0.0, "duration": 16.0,
  "params": { "start_azimuth": -62, "end_azimuth": 6, "easing": "ease_out" } }
```

Every action declares a **channel footprint** — the properties it animates. Two tracks writing the
same channel of the same target over overlapping frames is a `CHANNEL_CONFLICT`, caught by
`blended check` before Blender launches, with both track indices named and a suggested fix.

Abutting spans are fine: ramp 0→10s then hold 10→16s is the normal way to make a light peak
partway through and stay there.

`object.hold` writes nothing. It exists so "nothing happens here" is stated rather than inferred.

See `schemas/library.json` for every action, its parameters, and what it writes.

---

## Things that will bite you

**`post.bloom` raises.** Blender 5.2's compositor short-circuits background renders — the frame
comes back pure white. Leave it at `0.0`. Apply bloom afterwards in an editor.

**Wetness needs something to reflect.** `floor.wetness` produces specular streaks only if there is
an environment to mirror. With a black world and no `world.hdri`, a wet surface reflects nothing
and looks merely dark. Set an HDRI; the camera can still see black (see below).

**Black background *and* real reflections.** Set `world.color` to black and `world.hdri` to an
environment. The HDRI lights the scene and fills reflections while the camera still sees your
background colour. Without this, everything not directly lit is pure black, which is the single
strongest "computer graphics" tell.

**Glossy + moving camera = shimmer.** A wet, low-roughness surface carrying fine normal detail
makes highlights pop between frames. More samples do **not** help — it is aliasing, not noise.
The levers are `floor.wet_roughness` (raise it) and `floor.wet_flatten` (lower it; water fills
surface pores, so wet ground genuinely *is* flatter). Measure with `blended flicker`.

**Animated bump always shimmers a little.** `floor.ripples` looks like rain but never renders
perfectly stable. Leave it at `0` unless motion in the water matters more than calm.

**Dark matte materials photograph badly.** A perfectly matte black surface returns almost no
directional light and reads as a flat silhouette. Use a dark base with some `metallic` and varied
`roughness`, or make it wet — the specular highlights are what describe the form.

**Outlines: use `lineart`.** It traces silhouettes in screen space, so width is uniform at any
camera angle. The geometry-based modes (`hull`, `offset`) sit *behind* the subject and parallax
off-axis. Freestyle produces no strokes at all in Blender 5.2.

**Cycles is the photorealism lever, and it is slow.** ~10s/frame at 720p on an M4 GPU. EEVEE is a
rasteriser — the same class of technique as a game engine, which is why it looks like one. Use
EEVEE for every stage except `final`.

**Never benchmark Cycles on a cold first frame.** The first render spends minutes compiling Metal
kernels, then caches them. I have measured 150s/frame this way and been wrong by 20×.

---

## Iterating

Edit the field, re-run the stage. Stages downstream of an approved change are blocked until you
re-approve, so a material tweak cannot silently move the camera.

For long renders use `blended stage final`, which renders a resumable PNG sequence and encodes
afterwards. An interrupted mp4 is unplayable — the container needs its trailing atom — so a run
killed at 80% loses everything. A sequence loses only the frame in flight.

Useful while iterating:

```bash
blended contact-sheet "renders/scene_blocking_*.png"   # whole shot at a glance
blended flicker "renders/scene_final_frames/f*.png"    # temporal stability
```

---

## When something looks wrong

Run `blended check` first — it is instant and needs no Blender. Then run the stage with
`--build-only`, which builds the scene and runs Tier-2 probes without rendering. Probes report
coverage, camera path, light ramps, texture resolution and geometry facts in milliseconds.

Most failures in this project have been **silent**: plausible-looking output rather than an
error. Magenta floors from a relative texture path, geometry 9× too large from an uninitialised
bounds read, a contact sheet reporting the wrong frame count. If something looks subtly off,
measure it rather than adjusting by eye — that instinct has been right every time here and the
guessing has not.
