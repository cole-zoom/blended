"""Stage approval and drift detection (ARCHITECTURE §11a).

Approving a stage records a fingerprint of the IR fields that stage owns. If those fields later
change, the stage has *drifted* and needs another look.

This is the concrete form of the project's founding complaint — that asking for one change should
not silently regenerate everything else. Approved work cannot quietly regress: adjusting a
material can never move the camera without saying so.

Drift **warns** rather than blocking, because upstream changes are usually deliberate. The point
is that they are never invisible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from blended.stages import STAGE_ORDER, Stage, changed_paths, fingerprint

LEDGER_NAME = ".stages.json"
LEDGER_VERSION = 1


@dataclass(frozen=True)
class Approval:
    stage: str
    fingerprint: str
    approved_at: str

    def as_dict(self) -> dict:
        return {"fingerprint": self.fingerprint, "approved_at": self.approved_at}


@dataclass(frozen=True)
class StageState:
    stage: str
    approved: bool
    drifted: bool
    changed: tuple[str, ...] = ()
    approved_at: str | None = None

    @property
    def status(self) -> str:
        if not self.approved:
            return "pending"
        return "drifted" if self.drifted else "approved"


class Ledger:
    """Per-project record of which stages have been signed off, and against what.

    Stored next to the scene rather than in the repo: approvals are a property of one person's
    work in progress, not of the project's source.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data = self._load()

    @classmethod
    def for_scene(cls, scene_file: Path) -> Ledger:
        return cls(Path(scene_file).parent / LEDGER_NAME)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": LEDGER_VERSION, "scenes": {}}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # A corrupt ledger must not block work — approvals are recoverable by re-approving.
            return {"version": LEDGER_VERSION, "scenes": {}}
        data.setdefault("scenes", {})
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.partial")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ------------------------------------------------------------------ queries

    def approval(self, scene_name: str, stage: str) -> Approval | None:
        entry = self._data["scenes"].get(scene_name, {}).get(stage)
        if not entry:
            return None
        return Approval(stage, entry["fingerprint"], entry["approved_at"])

    def state(self, scene_name: str, ir: dict, stage: Stage) -> StageState:
        current = fingerprint(ir, stage)
        recorded = self.approval(scene_name, stage.name)
        if recorded is None:
            return StageState(stage.name, approved=False, drifted=False)
        if recorded.fingerprint == current:
            return StageState(stage.name, approved=True, drifted=False,
                              approved_at=recorded.approved_at)
        # Approved, but the owned fields moved. Report *which*, so the warning is actionable.
        changed = tuple(self._changed_since(scene_name, ir, stage))
        return StageState(stage.name, approved=True, drifted=True, changed=changed,
                          approved_at=recorded.approved_at)

    def _changed_since(self, scene_name: str, ir: dict, stage: Stage):
        """Paths that differ from the approved snapshot.

        Falls back to "unknown" when no snapshot was stored — a fingerprint alone proves that
        something moved but cannot say what.
        """
        snapshot = self._data["scenes"].get(scene_name, {}).get(stage.name, {}).get("snapshot")
        if snapshot is None:
            return ["(no snapshot recorded — re-approve to enable precise drift reporting)"]
        return changed_paths(ir, snapshot, stage)

    def states(self, scene_name: str, ir: dict, stages) -> list[StageState]:
        return [self.state(scene_name, ir, s) for s in stages]

    # ------------------------------------------------------------------ mutation

    def approve(self, scene_name: str, ir: dict, stage: Stage) -> Approval:
        """Record approval, storing both a fingerprint and the owned values.

        The snapshot costs little and is what lets a later drift warning name the fields that
        moved instead of merely asserting that something did.
        """
        record = Approval(
            stage.name,
            fingerprint(ir, stage),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        scenes = self._data["scenes"].setdefault(scene_name, {})
        scenes[stage.name] = {**record.as_dict(), "snapshot": ir}
        self._save()
        return record

    def revoke(self, scene_name: str, stage_name: str) -> bool:
        scenes = self._data["scenes"].get(scene_name, {})
        if stage_name in scenes:
            del scenes[stage_name]
            self._save()
            return True
        return False

    def clear(self, scene_name: str) -> None:
        self._data["scenes"].pop(scene_name, None)
        self._save()


def blocking_drift(states: list[StageState], target: str) -> list[StageState]:
    """Approved-but-drifted stages that sit *before* the one being run.

    Only upstream drift matters: re-running `lighting` after changing a material is exactly the
    case worth flagging, whereas an untouched later stage is simply not yet relevant.
    """
    index = STAGE_ORDER.index(target)
    upstream = set(STAGE_ORDER[:index])
    return [s for s in states if s.stage in upstream and s.drifted]
