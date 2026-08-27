"""Patches: the unit of change (ARCHITECTURE §1).

The founding complaint this project started from was that asking for one change regenerates
everything. So an edit is a **JSON Patch** against Scene IR — RFC 6902, the same shape Tier 1
already emits as `suggested_fix`, which means a diagnostic can be applied directly.

Three properties make this worth having over editing the file by hand:

* **Validated before it lands.** A patch that would break the scene is rejected, not written.
  "Make the camera slower, change nothing else" either works or fails loudly.
* **Reversible.** Every applied patch records its inverse, so undo is exact rather than
  approximate.
* **Scoped.** Applying a patch reports which pipeline stages it invalidates, so a material tweak
  cannot quietly unapprove your blocking without saying so.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blended.errors import BlendedError
from blended.ir.scene import SceneIR
from blended.stages import STAGE_ORDER, get as get_stage
from blended.verify.static import Report, check

HISTORY_DIR = "history"

#: Operations supported. `move`/`copy` are omitted deliberately: they are expressible as
#: add+remove, and every extra operation is another inverse to get right.
OPS = ("replace", "add", "remove")


class PatchError(BlendedError):
    code = "PATCH_INVALID"


# ------------------------------------------------------------------------------- json pointer


def _tokens(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"path must start with '/': {pointer!r}")
    # RFC 6901 escaping: ~1 is '/', ~0 is '~', and the order matters.
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.lstrip("/").split("/")]


def _descend(document: Any, tokens: list[str], pointer: str) -> tuple[Any, str | int]:
    """Walk to the container holding the final token, returning (container, key)."""
    node = document
    for token in tokens[:-1]:
        node = _child(node, token, pointer)
    return node, _key(node, tokens[-1], pointer)


def _child(node: Any, token: str, pointer: str) -> Any:
    key = _key(node, token, pointer)
    try:
        return node[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise PatchError(f"path does not exist: {pointer}") from exc


def _key(node: Any, token: str, pointer: str) -> str | int:
    if isinstance(node, list):
        if token == "-":
            return len(node)
        try:
            return int(token)
        except ValueError:
            raise PatchError(f"list index must be a number: {pointer}") from None
    return token


def read(document: Any, pointer: str) -> Any:
    node = document
    for token in _tokens(pointer):
        node = _child(node, token, pointer)
    return node


def exists(document: Any, pointer: str) -> bool:
    try:
        read(document, pointer)
    except PatchError:
        return False
    return True


# -------------------------------------------------------------------------------------- apply


def apply_one(document: dict, operation: dict) -> dict:
    """Apply one operation to a copy. Never mutates the input."""
    op = operation.get("op")
    pointer = operation.get("path")
    if op not in OPS:
        raise PatchError(f"unsupported op {op!r} (supported: {', '.join(OPS)})")
    if not isinstance(pointer, str):
        raise PatchError("operation is missing a 'path'")

    out = copy.deepcopy(document)
    tokens = _tokens(pointer)
    if not tokens:
        raise PatchError("cannot operate on the document root")

    container, key = _descend(out, tokens, pointer)

    if op == "remove":
        try:
            del container[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise PatchError(f"cannot remove missing path: {pointer}") from exc
        return out

    if "value" not in operation:
        raise PatchError(f"{op!r} needs a 'value': {pointer}")
    value = operation["value"]

    if op == "add" and isinstance(container, list):
        index = len(container) if key == len(container) else key
        container.insert(index, value)
        return out

    if op == "replace" and not exists(document, pointer):
        raise PatchError(f"cannot replace missing path: {pointer}")

    try:
        container[key] = value
    except (IndexError, TypeError) as exc:
        raise PatchError(f"cannot set path: {pointer}") from exc
    return out


def apply(document: dict, operations: list[dict]) -> dict:
    """Apply operations in order. All-or-nothing — a failure leaves the input untouched."""
    out = document
    for index, operation in enumerate(operations):
        try:
            out = apply_one(out, operation)
        except PatchError as exc:
            raise PatchError(f"operation {index}: {exc.message}", hint=exc.hint) from exc
    return out


def invert(document: dict, operations: list[dict]) -> list[dict]:
    """The operations that undo `operations` when applied to the *result*.

    Computed against the document as it is *before* each step, so a sequence inverts correctly
    rather than only its last operation. Returned reversed, since undo runs backwards.
    """
    inverse: list[dict] = []
    state = document
    for operation in operations:
        pointer = operation["path"]
        op = operation["op"]
        if op == "replace":
            inverse.append({"op": "replace", "path": pointer, "value": read(state, pointer)})
        elif op == "add":
            # An add into a list is undone by removing that index; into an object, by removing
            # the key — unless it replaced an existing value, which `add` is allowed to do.
            if pointer.endswith("/-"):
                # "-" means "append", and is not a location `remove` can address. Resolve it to
                # the index the element will actually occupy, which is the current length.
                container = read(state, pointer[:-2] or "/")
                inverse.append({"op": "remove", "path": f"{pointer[:-1]}{len(container)}"})
            elif exists(state, pointer):
                inverse.append({"op": "replace", "path": pointer,
                                "value": read(state, pointer)})
            else:
                inverse.append({"op": "remove", "path": pointer})
        elif op == "remove":
            inverse.append({"op": "add", "path": pointer, "value": read(state, pointer)})
        state = apply_one(state, operation)
    return list(reversed(inverse))


# ------------------------------------------------------------------------------------- result


@dataclass
class PatchResult:
    scene: SceneIR
    document: dict
    operations: list[dict]
    inverse: list[dict]
    report: Report
    invalidates: list[str] = field(default_factory=list)
    record: Path | None = None

    @property
    def ok(self) -> bool:
        return self.report.ok


def touched_stages(operations: list[dict]) -> list[str]:
    """Which stages own the fields a patch changes.

    Reported so applying a patch says what it unsettles. A material tweak that quietly
    unapproved your blocking would be exactly the regression this project exists to prevent.
    """
    from blended.stages import STAGES

    touched = set()
    for operation in operations:
        # "/tracks/0/params/x" -> "tracks", "/assets/0/wetness" -> "assets[].wetness"
        parts = _tokens(operation["path"])
        if not parts:
            continue
        head = parts[0]
        for name, stage in STAGES.items():
            for owned in stage.owns:
                base = owned.split("[")[0].split(".")[0]
                if base != head:
                    continue
                # A whole-subtree claim (e.g. "tracks") matches anything beneath it.
                if "[" not in owned and "." not in owned:
                    touched.add(name)
                    continue
                # Otherwise compare the leaf name, skipping the list index.
                tail = owned.replace("[]", "").split(".")[1:]
                actual = [p for p in parts[1:] if not p.isdigit()]
                if tail and actual[: len(tail)] == tail:
                    touched.add(name)
    return [s for s in STAGE_ORDER if s in touched]


def dry_run(document: dict, operations: list[dict]) -> PatchResult:
    """Apply and validate without writing anything."""
    inverse = invert(document, operations)
    updated = apply(document, operations)
    try:
        scene = SceneIR(**updated)
    except Exception as exc:
        raise PatchError(f"patch produces an invalid scene: {exc}") from exc
    return PatchResult(
        scene=scene,
        document=updated,
        operations=operations,
        inverse=inverse,
        report=check(scene),
        invalidates=touched_stages(operations),
    )


# ------------------------------------------------------------------------------------ history


def history_dir(scene_file: Path) -> Path:
    return Path(scene_file).parent / HISTORY_DIR


def entries(scene_file: Path) -> list[dict]:
    directory = history_dir(scene_file)
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.patch.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        record["file"] = str(path)
        out.append(record)
    return out


def record(scene_file: Path, result: PatchResult, note: str = "") -> Path:
    """Write the applied patch and its inverse, so undo is exact rather than approximate."""
    directory = history_dir(scene_file)
    directory.mkdir(parents=True, exist_ok=True)
    index = len(list(directory.glob("*.patch.json"))) + 1
    path = directory / f"{index:04d}.patch.json"
    path.write_text(json.dumps({
        "index": index,
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
        "operations": result.operations,
        "inverse": result.inverse,
        "invalidates": result.invalidates,
    }, indent=2))
    return path


def apply_to_file(scene_file: Path, operations: list[dict], *, note: str = "",
                  write: bool = True) -> PatchResult:
    """Validate a patch against a scene file and, if it holds, write both file and history.

    Validation happens *before* the write. A patch that breaks Tier 1 never reaches disk, so a
    bad edit cannot leave the project in a state you have to hand-repair.
    """
    scene_file = Path(scene_file)
    document = json.loads(scene_file.read_text())
    result = dry_run(document, operations)
    if not result.ok:
        raise PatchError(
            "patch would break the scene: "
            + "; ".join(d.message for d in result.report.errors),
            hint="No changes were written.",
        )
    if write:
        scene_file.write_text(json.dumps(result.document, indent=2) + "\n")
        result.record = record(scene_file, result, note)
    return result


def revert_last(scene_file: Path) -> PatchResult:
    """Undo the most recent patch by applying its recorded inverse."""
    history = entries(scene_file)
    if not history:
        raise PatchError("no patches to revert")
    last = history[-1]
    result = apply_to_file(scene_file, last["inverse"], note=f"revert {last['index']:04d}",
                           write=True)
    Path(last["file"]).unlink(missing_ok=True)
    # The revert's own record would otherwise sit above the patch it undid, so drop it too and
    # leave history reading as though the change never happened.
    if result.record:
        Path(result.record).unlink(missing_ok=True)
        result.record = None
    return result
