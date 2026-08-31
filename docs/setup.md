# Setup

Live editing in Blender. Do this once; after that it's one field and one button.

---

## On a brand new machine

```bash
# 1. Blender 5.2 or newer — https://www.blender.org/download/
#    Install to /Applications. If it lives elsewhere, set BLENDED_BLENDER to the
#    executable inside it, e.g. /Applications/Blender.app/Contents/MacOS/Blender

# 2. uv — https://docs.astral.sh/uv/
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. The project
git clone https://github.com/cole-zoom/blended.git
cd blended
uv sync
uv run blended doctor          # expect ✓ Blender 5.2.1 and ✓ Bridge working
```

`uv run blended doctor` is the real check — it launches Blender, builds a scene and reads the
result back, so if it passes the whole bridge works.

What a clone gives you: the code, the docs, the source art in `goal/`, and any scene files.
What it does not: renders, approvals and caches, which are per-machine and regenerable.

---

## Updating an existing machine

```bash
cd ~/Documents/Workspace/blended
git pull
uv sync
uv run blended doctor
```

**Install the add-on as a symlink, not a copy.** Blender's *Install from Disk* copies the file,
so later changes to the repo never reach it — which is a genuinely confusing failure, because
the add-on keeps working, just as an old version.

```bash
ln -sf ~/Documents/Workspace/blended/addon/blended_live.py \
  ~/Library/Application\ Support/Blender/5.2/scripts/addons/blended_live.py
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons**, search `blended`, tick it. Confirm it reads
**version 2.0.0**.

---

## Every session

1. **Quit Blender fully** (⌘Q) and reopen — this is what picks up any add-on changes
2. Press **N** in the 3D viewport, click the **blended** tab
3. Choose a `scene.json` in the file field
4. Press **Reload**

The panel should read `ok` with something like `480 frames · 84ms`. No terminal needed.

Press **Start watching** for live sync: every edit to the scene file rebuilds the viewport in
about a second, preserving your view angle, selection and playhead.

---

## The buttons

| | |
|---|---|
| **Reload** | Resolve and rebuild now |
| **Start watching** | Rebuild automatically on every change |
| **Frame** | Zoom to the subject, ignoring floor and atmosphere |
| **Camera** | Look through the scene camera — the framing the shot is composed for |
| **Lights up** | Jump to the brightest frame |

**Lights up exists for a reason.** These scenes often open almost black on purpose. An unlit
viewport at frame 1 looks broken when it is merely early. If in doubt, switch viewport shading
to **Solid** (third sphere, top right) — it ignores lights entirely, so geometry shows
regardless. That is the definitive "did the scene load" test.

---

## Editing

```bash
uv run blended patch projects/lancedb/scene.json \
  '{"op":"replace","path":"/assets/0/extrude","value":0.12}'

uv run blended revert projects/lancedb/scene.json     # exact undo
uv run blended history projects/lancedb/scene.json
```

A patch is validated before it is written, so a change that would break the scene is refused
and nothing lands on disk.

---

## When something looks wrong

**Panel says `invalid`** — the scene failed Tier 1. The reason is in the panel; fix the field it
names.

**Panel says `error`** — the build itself failed. The message carries the exception.

**Panel says `ok` but you see nothing** — almost always lighting or framing, not a failure.
Press **Camera**, then **Lights up**, or switch to Solid shading.

**Add-on seems to be an old version** — Blender caches compiled bytecode alongside the add-on,
and a stale cache survives even a correct symlink:

```bash
rm -rf ~/Library/Application\ Support/Blender/5.2/scripts/addons/__pycache__/blended_live*
```

Then restart Blender. Check the version in Preferences ▸ Add-ons.

**`Could not find the blended command`** — the add-on looks for `<project>/.venv/bin/blended`.
Run `uv sync` in the project.

---

## Starting a new project

```bash
uv run blended new myproject --asset path/to/logo.svg
```

Scaffolds a scene that already has a camera move and a light ramp, so the first reload shows
something moving. See [authoring.md](authoring.md) for what to change and what will bite you.
