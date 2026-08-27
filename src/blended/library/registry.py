"""The animation library: the closed vocabulary an agent composes from (ARCHITECTURE §2).

Each action declares four things:

  params    a pydantic model — becomes the JSON Schema that constrains authoring
  writes    the **channel footprint**: which properties of the target this action animates
  timing    whether its duration is fixed, stretchable, or has a minimum
  tags      semantic hints for retrieval

The channel footprint is the piece no `bpy`-writing agent can have. Two actions animating the
same channel of the same object over overlapping frames is a compile error naming both tracks,
not a render that silently comes out wrong.

Implementations live in `blended_backend.actions` — this side only declares and validates, since
the host has no `bpy`. `tests/test_library.py` asserts the two sides stay in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from blended.ir.scene import Easing


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------- param models


class OrbitParams(Params):
    """Sweep the camera around a target, optionally rising and pulling away as it goes.

    Implemented as a rotating pivot with the camera parented to it. Azimuth is the pivot's
    rotation; elevation and distance are the camera's local position on that pivot. Keeping the
    sweep on a parent means a *constant-distance* orbit stays exactly circular no matter what
    easing is applied, rather than depending on interpolation between sampled positions.

    Elevation and distance default to constant (end == start), so a plain orbit needs no extra
    parameters.
    """

    start_azimuth: float = Field(default=-30.0, description="Degrees. 0 faces the asset head-on.")
    end_azimuth: float = Field(default=30.0)
    start_elevation: float = Field(default=12.0, description="Degrees above the horizon.")
    end_elevation: float | None = Field(
        default=None, description="Defaults to start_elevation (constant height)."
    )
    distance: float | None = Field(
        default=None, description="Base distance. Auto-framed from the asset when null."
    )
    start_distance_scale: float = Field(
        default=1.0, gt=0, description="Multiplier on the base distance at the start."
    )
    end_distance_scale: float = Field(
        default=1.0, gt=0, description="Multiplier at the end. >1 pulls the camera away."
    )
    easing: Easing = "ease_in_out"


class LightRampParams(Params):
    """Animate a light's energy. The goal's 'dim to bright'."""

    start_energy: float = Field(ge=0)
    end_energy: float = Field(ge=0)
    easing: Easing = "ease_in_out"


class HoldParams(Params):
    """Occupy a span without animating anything.

    Exists so a gap in the timeline can be deliberate and visible in the IR rather than implied.
    """


class SpinParams(Params):
    turns: float = 1.0
    axis: Literal["x", "y", "z"] = "z"
    easing: Easing = "linear"


# ------------------------------------------------------------------------------------- registry

Timing = Literal["fixed", "stretchable"]


@dataclass(frozen=True)
class Action:
    name: str
    params: type[Params]
    #: Channel suffixes this action animates, qualified with the target id at check time.
    writes: tuple[str, ...]
    timing: Timing
    tags: tuple[str, ...]
    #: Which kinds of object this action can target.
    accepts: tuple[str, ...]

    def channels(self, target: str) -> tuple[str, ...]:
        return tuple(f"{target}.{channel}" for channel in self.writes)


ACTIONS: dict[str, Action] = {
    action.name: action
    for action in (
        Action(
            name="camera.orbit",
            params=OrbitParams,
            writes=("transform",),
            timing="stretchable",
            tags=("camera", "reveal", "cinematic"),
            accepts=("camera",),
        ),
        Action(
            name="light.ramp",
            params=LightRampParams,
            writes=("energy",),
            timing="stretchable",
            tags=("light", "reveal", "mood"),
            accepts=("light",),
        ),
        Action(
            name="object.spin",
            params=SpinParams,
            writes=("transform",),
            timing="stretchable",
            tags=("object", "motion"),
            accepts=("asset",),
        ),
        Action(
            name="object.hold",
            params=HoldParams,
            writes=(),
            timing="stretchable",
            tags=("timing",),
            accepts=("asset", "camera", "light"),
        ),
    )
}


def get(name: str) -> Action | None:
    return ACTIONS.get(name)


def names() -> list[str]:
    return sorted(ACTIONS)
