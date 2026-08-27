"""Enforce the host/backend separation.

`blended_backend` runs inside Blender's bundled Python, which has no access to the host venv.
An import of `blended`, `click`, or any third-party package works fine on a developer's machine
right up until it runs under Blender, where it fails with an opaque ImportError.

Static checks, so they run without Blender installed.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
BACKEND = SRC / "blended_backend"

#: Modules the backend is allowed to import. Blender's Python is a stock CPython, so its stdlib
#: is available; `bpy` and friends come from Blender itself.
BLENDER_PROVIDED = {"bpy", "mathutils", "bmesh", "gpu", "aud", "bl_math", "addon_utils", "bpy_extras"}


def _third_party_names() -> set[str]:
    pyproject = tomllib.loads((SRC.parent / "pyproject.toml").read_text())
    deps = pyproject["project"]["dependencies"]
    return {d.split(">=")[0].split("==")[0].split("[")[0].strip() for d in deps}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_backend_never_imports_host() -> None:
    for path in BACKEND.rglob("*.py"):
        assert "blended" not in (_imported_roots(path) - {"blended_backend"}), (
            f"{path.relative_to(SRC)} imports the host package `blended`. The backend runs "
            "inside Blender's Python and cannot see it. Communicate over JSON instead."
        )


def test_backend_never_imports_third_party() -> None:
    forbidden = _third_party_names()
    for path in BACKEND.rglob("*.py"):
        offending = _imported_roots(path) & forbidden
        assert not offending, (
            f"{path.relative_to(SRC)} imports {offending}, which is not installed in "
            "Blender's bundled Python. Backend imports are stdlib + bpy only."
        )


def test_backend_imports_are_stdlib_or_blender() -> None:
    """Catch anything that is neither stdlib, nor Blender-provided, nor our own package."""
    import sys

    allowed = set(sys.stdlib_module_names) | BLENDER_PROVIDED | {"blended_backend", "__future__"}
    for path in BACKEND.rglob("*.py"):
        unknown = _imported_roots(path) - allowed
        assert not unknown, (
            f"{path.relative_to(SRC)} imports {unknown}, which may not exist under Blender."
        )


def test_result_schema_versions_match() -> None:
    """The two sides of the wire contract mirror each other by hand — catch drift."""
    host = ast.parse((SRC / "blended" / "engine" / "result.py").read_text())
    guest = ast.parse((BACKEND / "result.py").read_text())

    def schema_version(tree: ast.Module) -> int:
        for node in tree.body:
            if isinstance(node, ast.Assign) and node.targets[0].id == "SCHEMA_VERSION":  # type: ignore[attr-defined]
                return node.value.value  # type: ignore[attr-defined]
        raise AssertionError("SCHEMA_VERSION not found")

    assert schema_version(host) == schema_version(guest), (
        "blended.engine.result and blended_backend.result have drifted."
    )
