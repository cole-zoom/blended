I want to make a ~16 second animation of the LanceDB logo, but the logo must be 3d, have a black outline, and have a lightsource shining on it. Also the camera must pan around the logo, and the light source must go from dim to bright.

---

## Resolved parameters

| | | |
|---|---|---|
| Duration | **16.0s** | was "16.58s"; ~16s ±2 was the real requirement. 16.0 is exact at 24/25/30/60fps |
| Frame rate | **30 fps** | 24's judder is most visible on a slow orbit |
| Frames | **480** | `2⁵ × 3 × 5` — every beat subdivision lands on an integer frame |

## Source assets

| File | Role |
|---|---|
| `dark-lancedb-logo.svg` | **geometry source** — ingest Tier A. Fill color irrelevant; materials assigned in Blender |
| `reference_photo.png` | **visual target** for the Tier-3 critic — *"does the render look like this?"* Not traced |

### What's actually in the SVG

```
viewBox    0 0 857 200        aspect 4.29:1 — very wide
<text>     0                  ✓ wordmark is already outlined → Tier A, no font needed
<path>     1  (16 subpaths)   ⚠ marks + wordmark are ONE object → needs a split step
fill-rule  evenodd            ⚠ counters in a/e/D/B are holes, not solid
fill       black              irrelevant — but the material must be LIGHT so a black outline reads
```

Three consequences for Phase 1:

1. **Tier B (font) is not needed.** Everything is curves already.
2. **Split by loose parts after mesh conversion**, then classify by X: marks `x < ~210`,
   wordmark `x > ~210` → the `logo_marks` / `logo_text` names the Scene IR targets.
3. **`evenodd` must survive curve→mesh.** If it doesn't, the holes in `a`, `e`, `D`, `B` fill in
   solid and the wordmark looks subtly wrong in a way that's easy to miss at draft resolution.

The 4.29:1 aspect makes ARCHITECTURE §7 worse, not better — a very wide object foreshortens hard
as the camera swings off-axis. The elevated partial arc isn't optional.

See [acceptance.md](acceptance.md) for these clauses compiled into checkable assertions.