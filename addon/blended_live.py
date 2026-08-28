"""blended — live reload add-on for Blender.

Install once: Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk…, pick this file, enable it.
Then in the 3D viewport press N and open the "blended" tab.

**Point it at a `scene.json` and press Reload.** That is the whole setup — no terminal, no
second window, nothing to copy between them. The add-on finds the project's CLI itself and runs
the host resolve step (~0.1s with textures cached) before rebuilding.

Why a subprocess rather than doing it all in here: the add-on runs in Blender's Python, which has
no network and none of the host's packages. Resolving textures and validating the scene need
both. Shelling out reuses the exact code the terminal uses instead of growing a second, weaker
implementation that drifts.

Building the scene, by contrast, happens in-process — `blended_backend` has been constrained
since the beginning to import nothing but stdlib and `bpy`, so the same code that builds during
a background render builds here.
"""

bl_info = {
    "name": "blended — live reload",
    "author": "blended",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D ▸ Sidebar (N) ▸ blended",
    "description": "Point at a scene.json; rebuild it in the viewport as it changes.",
    "category": "Development",
}

import json
import os
import subprocess
import sys
import time

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

RESOLVE_TIMEOUT = 120


# ------------------------------------------------------------------------------------ project


def find_project(scene_path):
    """Walk up from a scene file to the repo root, identified by `pyproject.toml`."""
    directory = os.path.dirname(os.path.abspath(scene_path))
    while True:
        if os.path.exists(os.path.join(directory, "pyproject.toml")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def find_cli(scene_path):
    """Locate the `blended` executable for this project.

    Prefers the project's own virtualenv over anything on PATH, so a Blender launched from the
    Finder — which inherits almost no environment — still finds the right one.
    """
    root = find_project(scene_path)
    if root:
        candidate = os.path.join(root, ".venv", "bin", "blended")
        if os.path.exists(candidate):
            return candidate
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, "blended")
        if os.path.exists(candidate):
            return candidate
    return None


def resolve(scene_path):
    """Run the host resolve step. Returns (resolved_path, error_message)."""
    cli = find_cli(scene_path)
    if not cli:
        return None, ("Could not find the `blended` command. Expected it at "
                      "<project>/.venv/bin/blended — run `uv sync` in the project.")
    try:
        completed = subprocess.run(
            [cli, "watch", scene_path, "--once"],
            capture_output=True, text=True, timeout=RESOLVE_TIMEOUT,
            cwd=find_project(scene_path) or os.path.dirname(scene_path),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"Could not run {os.path.basename(cli)}: {exc}"

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        return None, message.splitlines()[-1] if message else "resolve failed"

    stem = os.path.splitext(scene_path)[0]
    resolved = f"{stem}.resolved.json"
    if not os.path.exists(resolved):
        return None, "resolve produced no file"
    return resolved, None


# -------------------------------------------------------------------------------------- build


def _build(payload):
    _ensure_src_on_path(payload.get("src"))
    import importlib

    from blended_backend import scene as scene_mod

    # Reloaded each time so edits to the compiler take effect without restarting Blender.
    importlib.reload(scene_mod)

    began = time.perf_counter()
    scene_mod.clear()
    stats = scene_mod.build({"kind": "scene_ir", "ir": payload["ir"]})
    took = (time.perf_counter() - began) * 1000

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = stats.get("frames", scene.frame_end)
    return f"{stats.get('frames', '?')} frames · {took:.0f}ms"


def _ensure_src_on_path(src):
    """The resolved file carries its own source root, so nothing here needs configuring."""
    if src and os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


# -------------------------------------------------------------------------------------- state


class BlendedState(PropertyGroup):
    scene_file: StringProperty(
        name="Scene",
        description="A blended scene.json — everything else is found from it",
        subtype="FILE_PATH",
        default="",
    )
    auto: BoolProperty(name="Auto reload", default=True,
                       description="Rebuild whenever the scene file changes")
    interval: FloatProperty(name="Poll (s)", default=0.5, min=0.1, max=5.0)
    frame_after: BoolProperty(
        name="Frame after reload", default=True,
        description="Zoom to the subject after rebuilding — useful before the lights come up",
    )
    status: StringProperty(default="idle")
    detail: StringProperty(default="")
    last_stamp: StringProperty(default="")


# ---------------------------------------------------------------------------------- operators


class BLENDED_OT_reload(Operator):
    bl_idname = "blended.reload"
    bl_label = "Reload"
    bl_description = "Resolve the scene and rebuild it in the viewport"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = context.scene.blended
        path = bpy.path.abspath(state.scene_file)
        if not path or not os.path.exists(path):
            state.status = "no scene"
            state.detail = "Choose a scene.json above"
            self.report({"ERROR"}, state.detail)
            return {"CANCELLED"}

        resolved, error = resolve(path)
        if error:
            state.status = "error"
            state.detail = error
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        try:
            with open(resolved) as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            state.status = "unreadable"
            state.detail = str(exc)
            return {"CANCELLED"}

        if not payload.get("ok", True):
            # Tier 1 failed. Say why rather than leaving a stale scene on screen.
            state.status = "invalid"
            state.detail = "; ".join(payload.get("errors", []))[:220]
            self.report({"WARNING"}, "Scene failed validation — not rebuilt")
            return {"CANCELLED"}

        try:
            state.detail = _build(payload)
            state.status = "ok"
        except Exception as exc:
            state.status = "error"
            state.detail = f"{type(exc).__name__}: {exc}"
            self.report({"ERROR"}, state.detail)
            return {"CANCELLED"}

        state.last_stamp = str(os.path.getmtime(path))
        if state.frame_after:
            bpy.ops.blended.frame_subject()
        return {"FINISHED"}


class BLENDED_OT_watch(Operator):
    """Poll the scene file and rebuild when it changes.

    A modal timer rather than a thread: `bpy` is not thread-safe, and touching scene data off
    the main thread crashes Blender rather than merely misbehaving.
    """

    bl_idname = "blended.watch"
    bl_label = "Watch"

    _timer = None
    _running = False

    @classmethod
    def is_running(cls):
        return cls._running

    def modal(self, context, event):
        state = context.scene.blended
        if not BLENDED_OT_watch._running:
            return self.cancel(context)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        path = bpy.path.abspath(state.scene_file)
        if path and os.path.exists(path):
            stamp = str(os.path.getmtime(path))
            if stamp != state.last_stamp:
                # Stamped before rebuilding, so a scene that fails to build is not retried in a
                # loop every half second.
                state.last_stamp = stamp
                bpy.ops.blended.reload()
                for area in context.screen.areas:
                    area.tag_redraw()
        return {"PASS_THROUGH"}

    def execute(self, context):
        if BLENDED_OT_watch._running:
            BLENDED_OT_watch._running = False
            return {"FINISHED"}
        state = context.scene.blended
        BLENDED_OT_watch._running = True
        self._timer = context.window_manager.event_timer_add(
            state.interval, window=context.window
        )
        context.window_manager.modal_handler_add(self)
        state.status = "watching"
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        BLENDED_OT_watch._running = False
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.scene.blended.status = "idle"
        return {"CANCELLED"}


#: Staging geometry, excluded when framing. A floor can be 220 units across against a subject of
#: 1 — framing everything zooms out until the subject is a speck.
STAGING_NAMES = {"floor", "atmosphere"}
STAGING_SUFFIXES = ("_outline", "_lineart")


def _is_subject(obj):
    return (obj.type == "MESH" and obj.name not in STAGING_NAMES
            and not obj.name.endswith(STAGING_SUFFIXES))


class BLENDED_OT_frame_subject(Operator):
    bl_idname = "blended.frame_subject"
    bl_label = "Frame"
    bl_description = "Zoom to the subject, ignoring floor and atmosphere"

    def execute(self, context):
        for obj in context.scene.collection.all_objects:
            obj.select_set(_is_subject(obj))
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            return {"CANCELLED"}
        with context.temp_override(area=area):
            bpy.ops.view3d.view_selected()
        return {"FINISHED"}


class BLENDED_OT_look_through_camera(Operator):
    bl_idname = "blended.look_through_camera"
    bl_label = "Camera"
    bl_description = "Look through the scene camera — the framing the shot is composed for"

    def execute(self, context):
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None or context.scene.camera is None:
            self.report({"ERROR"}, "No camera in the scene")
            return {"CANCELLED"}
        with context.temp_override(area=area):
            bpy.ops.view3d.view_camera()
        return {"FINISHED"}


class BLENDED_OT_lights_up(Operator):
    """Jump to the brightest frame.

    Scenes here often open almost black on purpose, so an unlit viewport at frame 1 looks
    broken when it is merely early.
    """

    bl_idname = "blended.lights_up"
    bl_label = "Lights up"

    def execute(self, context):
        scene = context.scene
        lights = [o for o in scene.collection.all_objects if o.type == "LIGHT"]
        if not lights:
            self.report({"WARNING"}, "No lights in the scene")
            return {"CANCELLED"}

        best, best_energy = scene.frame_current, -1.0
        step = max(1, (scene.frame_end - scene.frame_start) // 24)
        for frame in range(scene.frame_start, scene.frame_end + 1, step):
            scene.frame_set(frame)
            total = sum(light.data.energy for light in lights)
            if total > best_energy:
                best, best_energy = frame, total
        scene.frame_set(best)
        self.report({"INFO"}, f"frame {best}")
        return {"FINISHED"}


# -------------------------------------------------------------------------------------- panel


class BLENDED_PT_panel(Panel):
    bl_label = "blended"
    bl_idname = "BLENDED_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "blended"

    def draw(self, context):
        layout = self.layout
        state = context.scene.blended

        layout.prop(state, "scene_file", text="")

        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("blended.reload", icon="FILE_REFRESH")

        watching = BLENDED_OT_watch.is_running()
        row = layout.row(align=True)
        row.operator("blended.watch",
                     text="Stop watching" if watching else "Start watching",
                     icon="PAUSE" if watching else "PLAY",
                     depress=watching)
        row.prop(state, "interval", text="")

        layout.prop(state, "frame_after")

        layout.separator()
        row = layout.row(align=True)
        row.operator("blended.frame_subject", icon="ZOOM_SELECTED")
        row.operator("blended.look_through_camera", icon="CAMERA_DATA")
        layout.operator("blended.lights_up", icon="LIGHT_SUN")

        box = layout.box()
        icon = {"ok": "CHECKMARK", "watching": "TIME", "idle": "DOT"}.get(
            state.status, "ERROR")
        box.label(text=state.status, icon=icon)
        for line in _wrap(state.detail, 32):
            box.label(text=line)


def _wrap(text, width):
    """Blender labels do not wrap, and a truncated error is a useless error."""
    if not text:
        return []
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines[:6]


# ------------------------------------------------------------------------------- registration

CLASSES = (BlendedState, BLENDED_OT_reload, BLENDED_OT_watch, BLENDED_OT_frame_subject,
           BLENDED_OT_look_through_camera, BLENDED_OT_lights_up, BLENDED_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blended = bpy.props.PointerProperty(type=BlendedState)


def unregister():
    BLENDED_OT_watch._running = False
    # Defensive: an unregister that raises leaves the add-on half-removed, and Blender then
    # refuses to re-enable it until you restart.
    if hasattr(bpy.types.Scene, "blended"):
        del bpy.types.Scene.blended
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


if __name__ == "__main__":
    register()
