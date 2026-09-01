"""Library action implementations. Runs inside Blender.

Each function here implements one action declared in `blended.library.registry`. The action name
is the contract between the two sides; `tests/test_library.py` asserts they stay in sync.
"""

from __future__ import annotations

from blended_backend.actions import camera, light, obj

#: action name -> implementation. Signature: (ctx, target, frames, params) -> dict of stats.
IMPLEMENTATIONS = {
    "camera.orbit": camera.orbit,
    "light.ramp": light.ramp,
    "object.spin": obj.spin,
    "object.hold": obj.hold,
    "object.move": obj.move,
    "object.reveal": obj.reveal,
    "object.fade": obj.fade,
    "object.morph": obj.morph,
    "object.tint": obj.tint,
}


def names():
    return sorted(IMPLEMENTATIONS)
