"""Scene IR — the typed AST, and the source of truth once a build succeeds (ARCHITECTURE §1).

Everything here is deliberately explicit and absolute: resolved times, named targets, concrete
parameters. Nothing in this file is semantic ("build_tension"); nothing is engine-level
(`rotation_euler[2] = 0.43`). It sits exactly between, which is the altitude an agent can hit
reliably and a compiler can check.

Pydantic serves double duty: validation now, and `model_json_schema()` in Phase 4 as the contract
handed to whoever is authoring scenes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENE_IR_VERSION = 1

Easing = Literal["linear", "ease_in", "ease_out", "ease_in_out"]


class Strict(BaseModel):
    # Reject unknown keys. A typo'd parameter must be a diagnostic, not a silent default.
    model_config = ConfigDict(extra="forbid")


class Timeline(Strict):
    """The authoritative clock. Never derived from the animation (ARCHITECTURE §3)."""

    duration: float = Field(gt=0, description="Seconds. The scene is exactly this long.")
    fps: int = Field(default=30, gt=0)

    @property
    def frames(self) -> int:
        """Seconds→frames quantization happens here and nowhere else."""
        return round(self.duration * self.fps)

    @property
    def actual_duration(self) -> float:
        return self.frames / self.fps

    @property
    def drift(self) -> float:
        return self.actual_duration - self.duration


class Outline(Strict):
    mode: Literal["lineart", "hull", "offset", "none"] = "lineart"
    thickness: float = Field(default=0.006, ge=0)
    color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    creases: bool = False


class Asset(Strict):
    id: str
    source: str
    extrude: float = Field(default=0.05, gt=0)
    bevel: float = Field(default=0.004, ge=0)
    resolution: int = Field(default=12, gt=0)
    target_size: float = Field(default=1.0, gt=0)
    base_color: tuple[float, float, float, float] = (0.92, 0.92, 0.94, 1.0)
    roughness: float = Field(default=0.35, ge=0, le=1)
    metallic: float = Field(default=0.0, ge=0, le=1)
    #: `plain` is a flat Principled surface. `worn` adds roughness variation and micro-relief —
    #: the inconsistency that separates a real object from a CG one.
    material: Literal["plain", "worn"] = "plain"
    wear: float = Field(default=0.5, ge=0, le=1)
    wetness: float = Field(default=0.0, ge=0, le=1)
    #: Water beads clinging to the surface. Reads best in close-ups.
    droplets: float = Field(default=0.0, ge=0, le=1)
    outline: Outline = Field(default_factory=Outline)


class DepthOfField(Strict):
    """Real lenses focus at one distance and blur everything else.

    Its absence is a strong "CG" tell — a perfectly sharp image at every depth is something no
    physical camera produces. Focus tracks an empty at the subject, so it stays locked while the
    camera moves rather than needing to be keyframed.
    """

    enabled: bool = False
    #: Lower = shallower focus and more blur. f/2.8 is dramatic, f/8 nearly deep-focus.
    f_stop: float = Field(default=4.0, gt=0)
    #: Extra distance to push focus behind the subject centre.
    focus_offset: float = 0.0


class Post(Strict):
    """Compositing applied after the render.

    Real optics scatter light inside the lens around bright sources, so highlights bloom. That
    softening is a strong "photographed" cue.
    """

    bloom: float = Field(default=0.0, ge=0, le=1)
    bloom_threshold: float = Field(default=1.0, ge=0)


class Camera(Strict):
    id: str = "camera"
    lens: float = Field(default=50.0, gt=0)
    margin: float = Field(default=1.15, gt=0)
    dof: DepthOfField = Field(default_factory=DepthOfField)
    #: Blurs motion within each frame's shutter interval, as a real camera does.
    motion_blur: bool = False
    shutter: float = Field(default=0.5, gt=0, le=1)
    #: Near clip. Blender's 0.1 default is larger than the whole subject here — a camera that
    #: starts beside the logo sits ~0.1 units from it, and geometry inside the clip vanishes.
    clip_start: float = Field(default=0.01, gt=0)
    clip_end: float = Field(default=1000.0, gt=0)


class Light(Strict):
    id: str
    #: `spot` is a lamp throwing a cone from one position — bounded, so it lights a pool rather
    #: than the whole scene, and its cone is what makes a visible beam in fog.
    #: `sun` is directional: parallel rays, position irrelevant, `distance`/`size` ignored.
    #: `area` is a softbox.
    type: Literal["area", "sun", "spot"] = "area"
    energy: float = Field(default=200.0, ge=0)
    azimuth: float = -35.0
    elevation: float = 35.0
    distance: float = Field(default=4.0, gt=0)
    size: float = Field(default=3.0, gt=0)
    #: Sun only: angular diameter in degrees. 0.526° is the real sun; larger softens shadows.
    angle: float = Field(default=0.526, gt=0)
    #: Spot only: full cone angle in degrees, and the 0–1 softness of its edge.
    spot_size: float = Field(default=45.0, gt=0, le=180.0)
    spot_blend: float = Field(default=0.25, ge=0, le=1)
    #: Spot/point: emitter radius. Larger = softer shadows.
    radius: float = Field(default=0.1, ge=0)
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)


class World(Strict):
    color: tuple[float, float, float, float] = (0.02, 0.02, 0.025, 1.0)
    strength: float = Field(default=1.0, ge=0)
    #: Poly Haven HDRI id. Lights the scene and gives reflective surfaces something to reflect,
    #: while `color` remains what the camera actually sees. Without this, a black world means
    #: unlit areas are pure black and wet surfaces mirror nothing — the main "CG" tell.
    hdri: str | None = None
    hdri_strength: float = Field(default=1.0, ge=0)
    hdri_resolution: Literal["1k", "2k", "4k"] = "2k"
    hdri_rotation: float = 0.0
    #: When true the HDRI is also the visible backdrop, replacing `color`.
    hdri_visible: bool = False


class Floor(Strict):
    """A ground plane under the subject. Sits at the subject's lowest point, not at z=0."""

    enabled: bool = True
    size: float = Field(default=40.0, gt=0)
    material: Literal["stone"] = "stone"
    #: Distance to drop the floor below the subject's base. 0 means the subject rests on it.
    offset: float = Field(default=0.0, ge=0)
    scale: float = Field(default=6.0, gt=0, description="Texture scale (tiles per unit).")
    #: Poly Haven asset id. When set, real scanned PBR maps replace the procedural material —
    #: the host downloads and caches them, the backend only ever sees local paths.
    texture: str | None = None
    texture_resolution: Literal["1k", "2k", "4k"] = "2k"
    #: 0 = dry, 1 = soaked. Darkens and smooths the surface in patches, producing the specular
    #: streaks that read as wet. Needs something to reflect — pair with `world.hdri`.
    wetness: float = Field(default=0.0, ge=0, le=1)
    #: Animated surface agitation on the wet areas. Motion is what makes wet read as actively
    #: raining rather than as having rained earlier.
    ripples: float = Field(default=0.0, ge=0, le=1)
    #: Roughness of the wet areas. Below ~0.1 a wet surface carrying normal detail specular-
    #: aliases: sub-pixel highlights flicker frame to frame. Higher trades mirror sharpness
    #: for temporal stability.
    wet_roughness: float = Field(default=0.12, ge=0.0, le=1.0)
    #: Ripple spatial frequency, evolution rate, and fractal detail. High values of any of these
    #: make the pattern change too much between frames, which reads as flicker rather than flow.
    ripple_scale: float = Field(default=28.0, gt=0)
    ripple_speed: float = Field(default=6.0, ge=0)
    ripple_detail: float = Field(default=6.0, ge=0)
    #: Strength of the surface normal/displacement relief. Fine relief on a glossy wet surface
    #: is what a moving camera's specular highlights alias against.
    bump: float = Field(default=1.0, ge=0)
    #: How much relief survives where the surface is wet. Water fills pores, so wet ground is
    #: genuinely flatter — and damping relief there is the strongest cure for specular shimmer
    #: under a moving camera.
    wet_flatten: float = Field(default=0.25, ge=0, le=1)


class Volumetrics(Strict):
    """Scattering medium that makes light shafts visible.

    Implemented as a bounded volume box, not a world volume — a world volume renders black in
    EEVEE 5.2 (see `staging.add_atmosphere`). Off by default: it is a real render cost and only
    earns it when there is a strong directional light with geometry to cast shafts through.
    """

    enabled: bool = False
    density: float = Field(default=0.12, ge=0)
    #: Box edge length. Auto-sized from the subject when null; must contain the camera too.
    size: float | None = Field(default=None, gt=0)
    anisotropy: float = Field(default=0.4, ge=-1, le=1)
    samples: int = Field(default=96, gt=0)


class Environment(Strict):
    floor: Floor | None = None
    volumetrics: Volumetrics = Field(default_factory=Volumetrics)


class Track(Strict):
    """One library action, scheduled on the timeline.

    The agent writes these. It picks `action` from a closed vocabulary and fills `params` against
    that action's schema — it never expresses motion as raw keyframes.
    """

    action: str
    target: str
    start: float = Field(default=0.0, ge=0)
    duration: float = Field(gt=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.duration


class SceneIR(Strict):
    version: int = SCENE_IR_VERSION
    name: str
    timeline: Timeline
    assets: list[Asset]
    camera: Camera = Field(default_factory=Camera)
    lights: list[Light] = Field(default_factory=list)
    world: World = Field(default_factory=World)
    environment: Environment = Field(default_factory=Environment)
    post: Post = Field(default_factory=Post)
    tracks: list[Track] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> SceneIR:
        ids = [a.id for a in self.assets] + [lt.id for lt in self.lights] + [self.camera.id]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate object ids: {sorted(duplicates)}")
        return self

    def target_ids(self) -> set[str]:
        return {a.id for a in self.assets} | {lt.id for lt in self.lights} | {self.camera.id}
