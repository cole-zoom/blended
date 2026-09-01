---
name: motion-easing
description: How to time and ease motion in a blended scene so it reads as authored rather than generated. Load before writing or adjusting any `tracks` in a scene.json — moves, fades, reveals, staggered sequences — and whenever motion is described as feeling static, mechanical, floaty, or "AI-ish".
---

# Easing and timing

Written after v2 of `01_reveal`, where one move landed and the rest did not. The R's slide was
called good and the letter fades were called "kinda AIy". Same file, same session, same
easing vocabulary — so the difference is worth being precise about, because it is repeatable.

## The one that worked, and why

```json
{"action": "object.move", "target": "word_g0", "start": 1.50, "duration": 1.60,
 "params": {"start_x": 0.404, "end_x": 0.0, "easing": "ease_in_out_strong"}}
```

Four things were true at once. Miss any of them and it stops working:

1. **The curve matched the boundary conditions.** The R is stationary before and stationary
   after, so a symmetric ease-in-out is the *correct* shape, not a default.
2. **The move was long enough to see the curve** — 1.6s is 48 frames, so the acceleration has
   ~15 frames to happen in and is legible as acceleration.
3. **The distance was real** — 40% of the wordmark's width. A curve on a small move is a curve
   nobody can see.
4. **It was the only thing happening.** One clear action, with a clear before and after.

## The four tells that read as machine-generated

These are what made the letter fades feel wrong. They are all *uniformity*.

**Uniform stagger.** Six letters, each exactly 0.20s after the last, each exactly 0.85s long,
each on the same curve. That is a `for` loop and it looks like one. Vary the interval, or ease
the stagger itself so letters bunch and spread. Vary durations by 10–20%.

**Properties moving in lockstep.** The fade and the drift started on the same frame and ended
on the same frame. Nothing physical does that. Offset them — let position lead opacity by a few
frames, or let opacity finish while position is still settling. This is the single strongest
tell and the cheapest to fix.

**Displacement below the perception threshold.** The letters drifted up 0.018 of the wordmark
width — about 19px at 1080p spread over 25 frames, so under a pixel per frame. It cost
complexity and bought nothing. If a drift is meant to be *felt*, it needs to be several percent.
If it is not meant to be felt, delete it.

**Strict sequencing.** The R landed at 3.10s, the first letter began at 2.95s — a 0.15s overlap
that is really none. Actions that never overlap read as a slideshow. Real motion has the next
thing beginning while the last is still settling.

## Rules

**Match the curve to what the motion is doing.** This is the one that matters most.

| The motion | Curve | Why |
|---|---|---|
| Starts at rest, ends at rest | `ease_in_out_strong` | Symmetric, because the boundaries are symmetric |
| Enters from off-screen / from nothing | `ease_out_strong`, `drift_out` | It already has speed; easing *in* makes it crawl on |
| Leaves the frame / goes to nothing | `ease_in_strong` | It should still be accelerating when it goes |
| Something with weight arriving | `overshoot_out` | Nothing with mass stops dead on its mark |
| Constant-rate (a spin, an orbit) | `linear` | Any easing makes it look like it is struggling |

Using `ease_in_out` on something entering from off-screen is the classic mistake — it arrives
apologetically.

**Pick a curve strong enough to see.** `ease_in_out` is SINE, and over a short move SINE is
close enough to linear to read as no easing at all. That was the literal note on v1: *"you can
barely tell it does that."* The cause was the curve, not the timing.

```
linear              ease_in_out (SINE)     ease_in_out_strong (QUART)
────────────        ──────────────         ──────────────
straight            barely bent            unmistakable
```

`drift_*` is EXPO — it settles so slowly it reads as drifting rather than stopping, which is
what soft, atmospheric motion wants.

**Give the curve room.** Under about 0.5s at 30fps the whole move is 15 frames and any easing
is two or three frames of ramp. Short moves should be linear or very strong; there is no middle.

**One clock, and check it.** `blended check` reports quantisation drift. Pick durations where
`seconds × fps` is a whole number and it stays zero.

## Diagnosing "it feels static"

Work down this list; it is ordered by how often it is the answer.

1. Is anything **overlapping**, or does each action wait for the last? → overlap them
2. Do all channels **start and stop together**? → offset them by 3–6 frames
3. Is the stagger **perfectly even**? → vary it
4. Is the easing **SINE** where it should be QUART or EXPO? → strengthen it
5. Is the only animated property **opacity**? → opacity alone has no physicality; pair it with
   displacement large enough to see
6. Does anything **overshoot**, or does everything stop dead? → `overshoot_out` on one element

## Available easings

`linear` · `ease_in|out|in_out` (SINE, gentle) · `ease_in|out|in_out_strong` (QUART, the
workhorse) · `drift_in|out|in_out` (EXPO, very soft settle) · `overshoot_out`,
`overshoot_in_out` (BACK, overshoots and returns)

Defined in `src/blended_backend/actions/common.py`; the host mirrors the vocabulary in
`Easing` in `src/blended/ir/scene.py`. Adding one means adding it to both — `blended schema`
regenerates the contract.

## Known-good reference

`projects/reverie-promo/scenes/01_reveal/` — the R's `object.move` is the example to copy.
`DECISIONS.md` in that folder records what was tried and rejected, including why the letters
cannot fade in while the R is still crossing their positions.
