# blended

A compiler for Blender animations, for people who don't know Blender.

You describe what you want. The system resolves assets, composes animations from a vetted library,
verifies the result against your stated intent, and emits a `.blend` plus a video.

**The LLM never writes `bpy`.** It fills a typed schema; a deterministic compiler does the rest.

---

## Install

**1. Blender 5.2 or newer** — [blender.org/download](https://www.blender.org/download/). Install
to `/Applications`. If it lives anywhere else, point `BLENDED_BLENDER` at the executable inside
the app bundle:

```bash
export BLENDED_BLENDER=/path/to/Blender.app/Contents/MacOS/Blender
```

**2. uv** — the project uses it for dependencies and for the Python it runs on.

```bash
brew install uv                                  # or:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**3. The project.**

```bash
git clone https://github.com/cole-zoom/blended.git
cd blended
uv sync
uv run blended doctor      # expect ✓ Blender 5.2.1 and ✓ Bridge working
uv run pytest              # expect 136 passed, 9 skipped
```

`doctor` is the real check — it launches Blender, builds a scene and reads the result back, so
if it passes, the whole bridge works. It needs no asset and no project.

The 9 skips are the SVG ingestion tests, which want a logo the repo no longer ships. Point them
at any SVG to run them:

```bash
BLENDED_TEST_LOGO=~/path/to/logo.svg uv run pytest
```

**4. The live-reload add-on** (optional, but it is most of the fun). Symlink it rather than using
Blender's *Install from Disk*, which copies the file — later changes to the repo would never
reach it, and the add-on keeps working as a silently stale version.

```bash
mkdir -p ~/Library/Application\ Support/Blender/5.2/scripts/addons
ln -sf "$PWD/addon/blended_live.py" \
  ~/Library/Application\ Support/Blender/5.2/scripts/addons/blended_live.py
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons**, search `blended`, tick it. Press **N** in the
3D viewport for the **blended** tab. Full walkthrough in [docs/setup.md](docs/setup.md).

### Make something

```bash
uv run blended new myproject --asset path/to/logo.svg
uv run blended stage assets projects/myproject/scene.json
```

`new` scaffolds a scene that already has a camera move and a light ramp wired, so the first
render shows something moving. **Bring your own SVG** — scenes, source art and renders are
gitignored, so a clone gives you the tool and none of anyone's work.

---

## The staged pipeline

Five stages, each answering one question and ending at a human gate. Render fidelity matches the
decision being made, and each stage **suppresses what is not yet being decided** — `blocking`
forces grey clay even when real materials exist, because a finished-looking frame gets judged on
its look instead of its timing.

```
assets  →  blocking  →  materials  →  lighting  →  final
```

```bash
uv run blended check   scene.json      # Tier 1, instant, no Blender
uv run blended stage   blocking scene.json
uv run blended approve blocking scene.json
uv run blended status  scene.json
```

Approving a stage freezes the IR fields it owns, so a later material tweak can never quietly
move the camera — it is reported by name.

---

## The shape of it

```
natural language → SCENE IR → [build] → bpy → .blend → render
                       ▲                                  │
                       └────── JSON Patch ◄── verify ──────┘
```

Scene IR is the source of truth once the first build succeeds. Every edit after that is a
**patch**, never a regeneration — so "change the camera speed, touch nothing else" is a one-line
diff with a recorded history and an exact undo.

**The agent is Claude Code, not an API call.** The interface is `schemas/` plus CLI diagnostics,
which makes a programmatic agent later a drop-in rather than a rewrite. See ARCHITECTURE §12.

The action library is a closed vocabulary, and each action declares the channels it writes — so
two tracks animating the same channel over overlapping frames is a compile error naming both,
not a render that silently comes out wrong.

```
camera.orbit   light.ramp   object.move   object.reveal   object.fade
object.morph   object.tint  object.spin   object.hold
```

---

## Docs

| For | Read |
|---|---|
| Setting up, live reload, and what to do when it looks wrong | [docs/setup.md](docs/setup.md) |
| Writing a scene — the traps, not just the fields | [docs/authoring.md](docs/authoring.md) |
| Timing and easing motion so it reads as authored | `.claude/skills/motion-easing/` |
| Field reference | `schemas/` — generated, never hand-edited |
| Asset-prep scripts that aren't part of the compiler | [tools/](tools/) |
| The design, and why it is shaped this way | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Conventions and the things that are easy to get wrong | [CLAUDE.md](CLAUDE.md) |
| What is built and what is next | [ROADMAP.md](ROADMAP.md) |

---

## Environment

| | |
|---|---|
| Blender | 5.2.1 LTS (`/Applications/Blender.app`) |
| Blender Python | 3.13.13 bundled — stdlib + `bpy` only |
| Host Python | 3.13, managed by uv |
| Engines | EEVEE Next (Metal), Cycles |

The host and the Blender backend never import each other; they talk over JSON files. That
boundary is what lets the same backend code run inside a background render and inside the GUI
add-on.

`blender/` is a source clone kept as a grep-able API reference. It is gitignored and **not** a
runtime dependency — the engine shells out to the installed Blender app.
