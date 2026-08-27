"""Render configuration and execution. Runs inside Blender.

Encodes the Blender 5.x API facts verified on this machine — see CLAUDE.md.
"""

from __future__ import annotations

import os

import bpy

#: Engine identifier is `BLENDER_EEVEE` in 5.x — NOT the 4.2-era `BLENDER_EEVEE_NEXT`.
DEFAULT_ENGINE = "BLENDER_EEVEE"


def available_engines():
    prop = type(bpy.context.scene.render).bl_rna.properties["engine"]
    return [item.identifier for item in prop.enum_items]


def enable_cycles(device="GPU", samples=128, denoise=True):
    """Register Cycles and point it at the GPU.

    `default_set=True` matters: with `default_set=False` the add-on loads but the engine is
    never registered, so `render.engine = 'CYCLES'` fails and the enum still shows only EEVEE.

    Cycles is a **path tracer** — it follows light rays through the scene and lets them bounce,
    so global illumination, soft shadows and accurate reflections come out of the simulation
    rather than being approximated. EEVEE is a rasteriser (the same class of technique as a game
    engine), which is why it renders in milliseconds and why it looks like a game.

    Denoising is on by default. Path tracing converges slowly and the residual noise is what
    forces sample counts up; an AI denoiser gets a clean image from far fewer samples.
    """
    import addon_utils

    addon_utils.enable("cycles", default_set=True, persistent=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = denoise
    scene.cycles.use_adaptive_sampling = True

    # Filter Glossy: blur glossy reflections slightly at the first bounce. Built for exactly the
    # failure this scene hits — a near-mirror surface carrying fine normal detail produces
    # sub-pixel highlights that flicker between frames. Spreading each highlight over more than
    # a pixel is what makes it temporally stable.
    scene.cycles.blur_glossy = 1.0
    # Clamp indirect light so a single very bright bounce cannot spike one pixel (a "firefly"),
    # which also reads as flicker across frames.
    scene.cycles.sample_clamp_indirect = 10.0

    if device == "GPU":
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for device_type in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
            try:
                prefs.compute_device_type = device_type
            except TypeError:
                continue
            prefs.get_devices()
            if any(d.type == device_type for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == device_type
                scene.cycles.device = "GPU"
                return device_type
        scene.cycles.device = "CPU"
    return "CPU"


def apply_settings(scene, cfg):
    """Apply a render config dict to a Blender scene.

    Recognised keys: engine, resolution [w, h], resolution_percentage, fps,
    frame_start, frame_end, samples, film_transparent, output, media.
    """
    render = scene.render

    engine = cfg.get("engine", DEFAULT_ENGINE)
    if engine == "CYCLES":
        cfg["_cycles_device"] = enable_cycles(
            device=cfg.get("device", "GPU"),
            samples=int(cfg.get("samples", 128)),
            denoise=cfg.get("denoise", True),
        )
    else:
        if engine not in available_engines():
            raise ValueError(
                f"Render engine {engine!r} is not available. Have: {available_engines()}"
            )
        render.engine = engine

    width, height = cfg.get("resolution", [960, 540])
    if cfg.get("media") in ("video", "sequence"):
        # H.264 requires even dimensions. Blender only complains at encode time, which for a
        # frame sequence means after every frame has already been paid for — so round here,
        # where it costs at most one pixel and nothing else.
        width, height = int(width) // 2 * 2, int(height) // 2 * 2
    render.resolution_x = int(width)
    render.resolution_y = int(height)
    render.resolution_percentage = int(cfg.get("resolution_percentage", 100))

    render.fps = int(cfg.get("fps", 30))
    render.fps_base = 1.0
    scene.frame_start = int(cfg.get("frame_start", 1))
    scene.frame_end = int(cfg.get("frame_end", 60))
    # Draft quality renders every Nth frame into a contact sheet; video output always steps by 1
    # or the result plays back at the wrong speed.
    scene.frame_step = int(cfg.get("frame_step", 1)) if cfg.get("media") != "video" else 1

    if engine == "BLENDER_EEVEE":
        scene.eevee.taa_render_samples = int(cfg.get("samples", 16))
    elif engine == "CYCLES":
        scene.cycles.samples = int(cfg.get("samples", 64))

    render.film_transparent = bool(cfg.get("film_transparent", False))

    media = cfg.get("media", "video")
    output = cfg["output"]
    if media == "video":
        _configure_video(render, output)
    else:
        _configure_stills(render, output)


def _configure_video(render, output):
    settings = render.image_settings
    # ORDER MATTERS: in Blender 5.x, FFMPEG only appears in the file_format enum once
    # media_type is VIDEO. Setting file_format first raises a TypeError.
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"

    render.ffmpeg.format = "MPEG4"
    render.ffmpeg.codec = "H264"
    render.ffmpeg.constant_rate_factor = "HIGH"
    render.ffmpeg.ffmpeg_preset = "GOOD"
    render.ffmpeg.gopsize = 12
    # Blender appends a `0001-0060` frame range unless we own the full filename.
    render.use_file_extension = False
    render.filepath = output


def _configure_stills(render, output):
    settings = render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGBA"
    render.use_file_extension = True
    render.filepath = output


def render_animation(scene, cfg):
    """Render the frame range and return the artifact path that actually landed on disk."""
    output = cfg["output"]
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    bpy.ops.render.render(animation=True)
    return output


def save_blend(path):
    """Persist the scene so it can be opened in the Blender GUI — a core affordance for a
    user learning Blender, and the reason we shell out to the real app (ARCHITECTURE §4)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return path
