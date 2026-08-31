# Acceptance criteria — LanceDB logo

Derived from `goal.md`. This file is the worked example of the system's central claim:
**a goal statement compiles into a test suite.** Each clause below maps to a tier and a probe.

Source clause → check. Nothing here requires a human to watch the video.

---

## "~16 second animation" → **16.0s @ 30fps = 480 frames**

| Tier | Check |
|---|---|
| T1 | `scene.duration == 16.0` and `scene.fps == 30` |
| T1 | every track's `end <= scene.frames` — no action extends the clock |
| T2 | `render.frame_count == 480` |
| T2 | quantization is exact — `duration * fps` is integral, `drift == 0.0` |

Unlike 16.58s, this quantizes exactly at 24/25/30/60fps, so frame rate stays a free choice.
The machinery still reports drift; here it reports zero.

---

## "of the LanceDB logo"

| Tier | Check |
|---|---|
| T1 | asset `logo` resolves; manifest declares objects `logo_marks`, `logo_text` |
| T1 | `manifest.provenance.lossy == false` — vector source, **not** a trace |
| T1 | normalization contract satisfied: origin centered, unit bounds, Z-up, materials stripped |
| T2 | mesh non-degenerate: `vert_count > 0`, `volume > 0`, bounds finite |
| T2 | curve→mesh conversion left no open/non-manifold boundaries (breaks solidify) |
| T2 | **`evenodd` survived** — `logo_text` has interior holes; `euler_characteristic < 2` proves the counters in `a`/`e`/`D`/`B` didn't fill solid |
| T2 | split succeeded — both `logo_marks` and `logo_text` exist, disjoint bounds, marks left of text |
| T2 | `logo_marks` has the expected loose-part count (dot cluster), not one fused blob |
| T3 | vision vs. `reference_photo.png`: "is this recognizably the LanceDB logo — dot grid + wordmark?" |

Shape fidelity against the reference is the one thing a probe genuinely can't judge → T3.
The `lossy == false` check is what mechanically enforces "use the SVG, not the screenshot."

---

## "but the logo must be 3d"

| Tier | Check |
|---|---|
| T1 | asset pipeline includes an `extrude` step with `depth > 0` |
| T2 | `bbox("logo").depth / bbox("logo").width > 0.02` — real Z extent, not a decal |
| T2 | at some sampled frame, camera azimuth is oblique enough that depth is visible |

---

## "have a black outline"

| Tier | Check |
|---|---|
| T1 | `style.outline` present; `color == #000000`; `thickness > 0` |
| T2 | inverted-hull object exists, normals flipped, material is black emission, backface-culled |
| T2 | hull is uniformly larger than source: `bbox(hull) > bbox(logo)` on every axis |
| T3 | vision: "is a clean black outline visible around the logo edges?" |

⚠️ Outline thickness reads differently at oblique angles — see ARCHITECTURE §7.
If T3 flags inconsistency, the fix is a distance-compensated hull scale, not a thicker hull.

⚠️ Inverted hull requires manifold geometry. Curve→mesh conversion from SVG can leave open
boundaries, which makes solidify produce garbage — hence the manifold probe above.

---

## "and have a lightsource shining on it"

| Tier | Check |
|---|---|
| T1 | ≥1 light object declared |
| T2 | `light.energy_at(t) > 0` for all `t` |
| T2 | light actually points at the subject — `dot(normalize(light.pos - logo.pos), light.direction) < -0.5` |
| T2 | logo is not fully in shadow at any sampled frame |

The "points at the subject" probe exists because a light that exists but aims into the void is the
single most likely silent failure here.

---

## "the camera must pan around the logo"

| Tier | Check |
|---|---|
| T1 | exactly one action writes `camera.transform` — no conflicting tracks |
| T2 | `distance(camera, logo)` constant within ±5% across all frames → **orbit, not drift** |
| T2 | `camera.azimuth(t)` is strictly monotonic → actually circling, not oscillating |
| T2 | total azimuth sweep `>= 60°` → a real move, not a nudge (**revised down from 180°, see below**) |
| T2 | `camera.tracks_target == "logo"` at every frame |
| T2 | **`screen_coverage("logo", t) > 0.15` at EVERY sampled frame** ← the important one |
| T2 | `in_frame("logo", t)` — full bbox inside NDC, all frames |
| T2 | `logo_text` projected width `> 0.25` of frame width for `>= 40%` of frames — a 4.29:1 object foreshortens hard off-axis, and an illegible wordmark is the failure mode |

That coverage probe is what forces the elevated partial arc instead of a flat 360°. A flat orbit
puts the camera edge-on to an extruded logo and coverage collapses toward zero. The probe fails,
loudly, in seconds — before you ever watch a render.

### Why the sweep requirement dropped from 180° to 60°

The 180° figure was written before the source SVG had been inspected. The logo is **4.29:1** — at
90° off-axis it is a vertical sliver, and the wordmark is unreadable well before that. Any sweep
wide enough to satisfy "180°" necessarily spends a large fraction of its runtime showing something
illegible, which fails the coverage probe on the same page.

The two criteria were in direct conflict; coverage wins because legibility is the point of showing
a logo at all. The shipped animation sweeps **68°** (−62° → +6°), which reads clearly as the camera
moving around the logo while keeping it readable throughout, and lands nearly head-on at the
brightest moment. ARCHITECTURE §7 flagged exactly this tension before either was built.

---

## "and the light source must go from dim to bright"

| Tier | Check |
|---|---|
| T1 | a `light.ramp` action exists targeting a declared light |
| T2 | `energy_at(0) < energy_at(end)` |
| T2 | `energy_over_time()` is monotonically non-decreasing — no dips |
| T2 | contrast is real: `energy_at(end) / energy_at(0) >= 5.0` |
| T2 | `energy_at(0) > 0` — "dim", not "off" |

The 5× ratio encodes the intent. "Dim to bright" that goes 100→110 W technically passes a naive
monotonic check and looks like nothing happened.

---

## Not stated, enforced anyway

| Tier | Check |
|---|---|
| T1 | no channel written by two overlapping actions |
| T2 | no object interpenetration |
| T2 | camera never inside the logo's bounding volume |
| T2 | logo is never fully backfacing to camera |
| T3 | wordmark is legible for `>= 40%` of runtime |

---

## Summary

**32 of 36 checks are Tier 1 or Tier 2** — free, deterministic, and complete before a single pixel
is rendered. Only 4 need a vision pass, and every one of those is a genuine aesthetic judgment
rather than something a probe could have answered.

That ratio is the argument for the whole architecture.

In v1 the Tier-3 pass is Claude Code reading a contact sheet against `reference_photo.png` — no API
call. See ARCHITECTURE §12.
