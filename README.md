# blended

A compiler for Blender animations, for people who don't know Blender.

You describe what you want. The system resolves assets, composes animations from a vetted library,
verifies the result against your stated intent, and emits a `.blend` plus a video.

**The LLM never writes `bpy`.** It fills a typed schema; a deterministic compiler does the rest.

---

## Status

Phases 0–4 built. A staged pipeline with human gates, Tier-1/Tier-2 verification, resumable
Cycles renders, and a documented authoring contract.

```bash
uv sync
uv run blended doctor
uv run blended new myproject --asset logo.svg
uv run blended stage assets projects/myproject/scene.json
```

- **[docs/setup.md](docs/setup.md)** — live editing in Blender, and what to do when it looks wrong
- **[docs/authoring.md](docs/authoring.md)** — writing a scene: the traps, not just the fields

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the design and why
- **[ROADMAP.md](ROADMAP.md)** — phases, in build order
- **[goal/](goal/)** — the north-star target driving v1
  - [goal.md](goal/goal.md) — the ask, in plain english + resolved parameters
  - [acceptance.md](goal/acceptance.md) — that ask, compiled into 36 machine-checkable assertions

v1 target: **16.0s @ 30fps = 480 frames**, LanceDB logo, 3D + black outline, orbiting camera,
light ramping dim→bright.

---

## The shape of it

```
natural language → INTENT IR → [lower] → SCENE IR → [build] → bpy → .blend → render
                                   ▲                                            │
                                   └──────── JSON Patch ◄── verify (T1/T2/T3) ──┘
```

Scene IR is the source of truth once the first build succeeds. Every edit after that is a **patch**,
never a regeneration — so "change the camera speed, touch nothing else" is a one-line diff.

**In v1 the agent is Claude Code, not an API call.** The interface is `schemas/` + CLI diagnostics,
which means a programmatic agent later is a drop-in rather than a rewrite. See ARCHITECTURE §12.

---

## Environment

| | |
|---|---|
| Blender | 5.2.1 LTS (`/Applications/Blender.app`) |
| Blender Python | 3.13.13 bundled |
| Host Python | 3.13 |
| Engines | EEVEE Next (Metal), Cycles |

`blender/` is a source clone kept as a grep-able API reference. It is gitignored and **not** a
runtime dependency — the engine shells out to the installed Blender app.
