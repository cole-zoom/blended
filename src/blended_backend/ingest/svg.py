"""SVG → 3D geometry. Runs inside Blender.

Tier A of the ingestion ladder (ARCHITECTURE §5) — the highest-fidelity path, and the only one
Phase 1 needs. Vector in, beveled solid out, with hole topology preserved.

Verified against `goal/dark-lancedb-logo.svg` (Blender 5.2.1):
  1 curve object · 15 cyclic Bezier splines · 2D · fill_mode=BOTH · 9 loose parts · 6 holes
"""

from __future__ import annotations

import addon_utils
import bpy

#: Splines closer than this fraction of total width are considered the same group.
#: The LanceDB logo's marks/wordmark gap is ~8x wider than its largest inter-letter gap,
#: so the exact value is not delicate.
_GAP_FLOOR = 0.02


def ensure_importer():
    """`--factory-startup` disables bundled extensions, so enable the SVG importer explicitly."""
    addon_utils.enable("io_curve_svg", default_set=False, persistent=True)


def import_svg(filepath):
    """Import an SVG and return the curve objects it produced.

    Blender's importer discards nothing we need: fills become `fill_mode`, and nested cyclic
    splines keep the even-odd relationship that makes letter counters holes rather than blobs.
    Fill *colour* is dropped, which is exactly right — appearance belongs to the style layer.
    """
    ensure_importer()
    before = set(bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=filepath)
    created = [o for o in bpy.data.objects if o not in before]
    curves = [o for o in created if o.type == "CURVE"]
    if not curves:
        raise ValueError(f"No curves imported from {filepath!r} — is it a valid SVG?")
    return curves


def spline_x_ranges(curve_obj):
    """Return `(min_x, max_x)` per spline, in object space."""
    ranges = []
    for spline in curve_obj.data.splines:
        points = spline.bezier_points if spline.type == "BEZIER" else spline.points
        xs = [p.co.x for p in points]
        ranges.append((min(xs), max(xs)))
    return ranges


def find_x_gap(curve_obj):
    """Find the largest horizontal gap between spline clusters.

    Returns `(split_x, gap_width)`, or `(None, 0.0)` if the geometry is one continuous cluster.

    This is how marks get separated from a wordmark without hardcoding a threshold: sweep the
    splines left to right tracking the running right edge, and the biggest jump is the gap.
    """
    ranges = spline_x_ranges(curve_obj)
    if len(ranges) < 2:
        return None, 0.0

    ordered = sorted(ranges)
    total_width = max(r[1] for r in ranges) - min(r[0] for r in ranges)
    if total_width <= 0:
        return None, 0.0

    best_gap = 0.0
    best_x = None
    running_max = ordered[0][1]
    for lo, hi in ordered[1:]:
        gap = lo - running_max
        if gap > best_gap:
            best_gap, best_x = gap, running_max + gap / 2.0
        running_max = max(running_max, hi)

    if best_gap / total_width < _GAP_FLOOR:
        return None, 0.0
    return best_x, best_gap


def split_at_x(curve_obj, split_x, names):
    """Split a curve into two curve objects at `split_x`, preserving hole topology.

    Splitting at the *spline* level rather than separating mesh loose parts is deliberate: a
    loose-parts separation would also detach every letter counter into its own object, turning
    6 holes into 6 stray islands. Splines carry their nesting with them.
    """
    left_name, right_name = names
    ranges = spline_x_ranges(curve_obj)
    left = {i for i, (lo, hi) in enumerate(ranges) if (lo + hi) / 2.0 < split_x}
    right = set(range(len(ranges))) - left

    out = []
    for name, keep in ((left_name, left), (right_name, right)):
        if not keep:
            continue
        data = curve_obj.data.copy()
        data.name = f"{name}_curve"
        for i in reversed(range(len(data.splines))):
            if i not in keep:
                data.splines.remove(data.splines[i])
        obj = bpy.data.objects.new(name, data)
        obj.matrix_world = curve_obj.matrix_world.copy()
        bpy.context.scene.collection.objects.link(obj)
        out.append(obj)
    return out


def solidify_curve(curve_obj, depth, bevel=0.0, resolution=12, bevel_resolution=3):
    """Give a flat curve thickness, then convert it to a mesh.

    Extruding at the *curve* level rather than the mesh level lets Blender generate the caps
    from the fill rule, so holes stay holes. A mesh-level extrude would need the hole topology
    to already be correct, which is the thing we are relying on the curve to provide.
    """
    data = curve_obj.data
    data.dimensions = "2D"
    data.fill_mode = "BOTH"
    data.extrude = depth
    data.bevel_depth = bevel
    data.bevel_resolution = bevel_resolution
    data.resolution_u = resolution
    data.render_resolution_u = resolution

    bpy.ops.object.select_all(action="DESELECT")
    curve_obj.select_set(True)
    bpy.context.view_layer.objects.active = curve_obj
    bpy.ops.object.convert(target="MESH")
    return curve_obj


def mesh_report(obj):
    """Topology facts used by the Tier-2 probes (goal/acceptance.md).

    `holes = parts - euler` is the check that proves the SVG's even-odd fill survived
    conversion. If letter counters had filled solid, holes would read 0.
    """
    mesh = obj.data
    verts, edges, faces = len(mesh.vertices), len(mesh.edges), len(mesh.polygons)
    euler = verts - edges + faces
    parts = count_loose_parts(obj)
    non_manifold = count_non_manifold_edges(obj)
    return {
        "verts": verts,
        "edges": edges,
        "faces": faces,
        "euler": euler,
        "loose_parts": parts,
        "holes": max(0, parts - euler),
        "non_manifold_edges": non_manifold,
        "is_manifold": non_manifold == 0,
    }


def count_loose_parts(obj):
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen = set()
    parts = 0
    for vert in bm.verts:
        if vert.index in seen:
            continue
        parts += 1
        stack = [vert]
        while stack:
            current = stack.pop()
            if current.index in seen:
                continue
            seen.add(current.index)
            for edge in current.link_edges:
                other = edge.other_vert(current)
                if other.index not in seen:
                    stack.append(other)
    bm.free()
    return parts


def count_non_manifold_edges(obj):
    """Non-manifold geometry makes Solidify produce garbage — which is how the outline is built."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    count = sum(1 for edge in bm.edges if len(edge.link_faces) not in (1, 2))
    bm.free()
    return count
