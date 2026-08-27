"""blended — live reload add-on for Blender.

Install: Edit ▸ Preferences ▸ Add-ons ▸ Install…, pick this file, enable it.
Then in the 3D viewport press N and open the "blended" tab.

Point it at a `*.resolved.json` written by `blended watch`, and the scene rebuilds in place
whenever you change `scene.json` from the terminal. No render, no reopening the file, and the
viewport stays exactly where you put it.

This works because `blended_backend` has been constrained since the beginning to import nothing
but stdlib and `bpy` — so the same code that builds a scene during a background render builds it
here, inside the GUI, with no subprocess and no duplicated logic.
"""

bl_info = {
    "name": "blended — live reload",
    "author": "blended",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D ▸ Sidebar (N) ▸ blended",
    "description": "Rebuild a blended scene in the viewport when its source changes.",
    "category": "Development",
}

import json
import os
import sys
import time

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup


# --------------------------------------------------------------------------------------- state


def _ensure_src_on_path(src):
    """Put the project's `src/` on sys.path so `blended_backend` is importable.

    The resolved file carries its own source root, so the add-on is self-configuring — there is
    no preference to set and get wrong.
    """
    if src and os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def _read_resolved(path):
    with open(path) as handle:
        return json.load(handle)


def _build(payload):
    """Clear and rebuild the scene from a resolved IR. Returns a short status string."""
    _ensure_src_on_path(payload.get("src"))

    # Imported late and reloaded each time: editing the compiler in the terminal should take
    # effect here without restarting Blender.
    import importlib

    from blended_backend import scene as scene_mod

    importlib.reload(scene_mod)

    began = time.perf_counter()
    scene_mod.clear()
    stats = scene_mod.build({"kind": "scene_ir", "ir": payload["ir"]})
    took = (time.perf_counter() - began) * 1000

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = stats.get("frames", scene.frame_end)
    return f"{stats.get('frames', '?')} frames · {took:.0f}ms"


class BlendedState(PropertyGroup):
    resolved: StringProperty(
        name="Resolved scene",
        description="A *.resolved.json written by `blended watch`",
        subtype="FILE_PATH",
        default="",
    )
    auto: BoolProperty(
        name="Auto reload",
        description="Rebuild whenever the resolved file changes",
        default=True,
    )
    interval: FloatProperty(
        name="Poll (s)", default=0.5, min=0.1, max=5.0,
        description="How often to check for changes",
    )
    status: StringProperty(default="idle")
    detail: StringProperty(default="")
    last_stamp: StringProperty(default="")


# ----------------------------------------------------------------------------------- operators


class BLENDED_OT_reload(Operator):
    bl_idname = "blended.reload"
    bl_label = "Reload scene"
    bl_description = "Rebuild the scene from the resolved file"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = context.scene.blended
        path = bpy.path.abspath(state.resolved)
        if not path or not os.path.exists(path):
            self.report({"ERROR"}, "Set a resolved scene file first")
            return {"CANCELLED"}

        try:
            payload = _read_resolved(path)
        except (OSError, ValueError) as exc:
            state.status = "unreadable"
            state.detail = str(exc)
            self.report({"ERROR"}, f"Could not read: {exc}")
            return {"CANCELLED"}

        if not payload.get("ok", True):
            # Tier 1 failed on the host. Show why rather than building something invalid.
            state.status = "invalid"
            state.detail = "; ".join(payload.get("errors", []))[:200]
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
        return {"FINISHED"}


class BLENDED_OT_watch(Operator):
    """Poll the resolved file and rebuild when it changes.

    A modal timer rather than a thread: `bpy` is not thread-safe, and touching scene data from
    anywhere but the main thread crashes Blender rather than merely misbehaving.
    """

    bl_idname = "blended.watch"
    bl_label = "Toggle watching"

    _timer = None
    _running = False

    @classmethod
    def is_running(cls):
        return cls._running

    def modal(self, context, event):
        state = context.scene.blended
        if not BLENDED_OT_watch._running or not state.auto:
            return self.cancel(context)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        path = bpy.path.abspath(state.resolved)
        if path and os.path.exists(path):
            stamp = str(os.path.getmtime(path))
            if stamp != state.last_stamp:
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


#: Staging geometry, excluded when framing. A floor can be 220 units across against a subject
#: of 1 — framing everything zooms out until the subject is a speck, which is the opposite of
#: what the button is for.
STAGING_NAMES = {"floor", "atmosphere"}
STAGING_SUFFIXES = ("_outline", "_lineart")


def _is_subject(obj):
    return (
        obj.type == "MESH"
        and obj.name not in STAGING_NAMES
        and not obj.name.endswith(STAGING_SUFFIXES)
    )


class BLENDED_OT_frame_subject(Operator):
    """Zoom the viewport to the subject, ignoring floor and atmosphere."""

    bl_idname = "blended.frame_subject"
    bl_label = "Frame subject"

    def execute(self, context):
        for obj in context.scene.collection.all_objects:
            obj.select_set(_is_subject(obj))
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            self.report({"ERROR"}, "No 3D viewport to frame")
            return {"CANCELLED"}
        with context.temp_override(area=area):
            bpy.ops.view3d.view_selected()
        return {"FINISHED"}


class BLENDED_OT_look_through_camera(Operator):
    """Look through the scene camera — the framing the animation is actually composed for."""

    bl_idname = "blended.look_through_camera"
    bl_label = "Look through camera"

    def execute(self, context):
        area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
        if area is None or context.scene.camera is None:
            self.report({"ERROR"}, "No camera in the scene")
            return {"CANCELLED"}
        with context.temp_override(area=area):
            bpy.ops.view3d.view_camera()
        return {"FINISHED"}


# --------------------------------------------------------------------------------------- panel


class BLENDED_PT_panel(Panel):
    bl_label = "blended"
    bl_idname = "BLENDED_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "blended"

    def draw(self, context):
        layout = self.layout
        state = context.scene.blended

        column = layout.column(align=True)
        column.prop(state, "resolved", text="")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("blended.reload", icon="FILE_REFRESH")

        row = layout.row(align=True)
        row.prop(state, "auto", toggle=True,
                 icon="PLAY" if BLENDED_OT_watch.is_running() else "PAUSE")
        row.prop(state, "interval")

        if state.auto and not BLENDED_OT_watch.is_running():
            layout.operator("blended.watch", text="Start watching", icon="TIME")
        elif BLENDED_OT_watch.is_running():
            layout.operator("blended.watch", text="Stop watching", icon="SNAP_FACE")

        row = layout.row(align=True)
        row.operator("blended.frame_subject", text="Frame", icon="ZOOM_SELECTED")
        row.operator("blended.look_through_camera", text="Camera", icon="CAMERA_DATA")

        box = layout.box()
        icon = {"ok": "CHECKMARK", "watching": "TIME", "idle": "DOT",
                "error": "ERROR", "invalid": "ERROR",
                "unreadable": "ERROR"}.get(state.status, "DOT")
        box.label(text=state.status, icon=icon)
        if state.detail:
            for line in _wrap(state.detail, 34):
                box.label(text=line)


def _wrap(text, width):
    """Blender labels do not wrap, so do it here rather than truncating an error."""
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

CLASSES = (BlendedState, BLENDED_OT_reload, BLENDED_OT_watch,
           BLENDED_OT_frame_subject, BLENDED_OT_look_through_camera, BLENDED_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blended = bpy.props.PointerProperty(type=BlendedState)


def unregister():
    BLENDED_OT_watch._running = False
    del bpy.types.Scene.blended
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
