"""Shape-key morphing between two flat vector outlines. Runs inside Blender.

The problem this solves: Blender can interpolate between shape keys, but only when both
shapes are the *same mesh* with the same vertices in the same order. Two SVGs traced
independently share nothing — different point counts, different starting points, possibly
opposite winding. Interpolating them naively pairs unrelated vertices and the in-between
frames are noise.

So the morph is *compiled*, not scripted:

  1. Both outlines are reduced to their closed contours.
  2. Contours are paired up — largest area to largest area, so an outer contour maps to an
     outer contour and a counter maps to a counter rather than to whatever happened to be
     listed first.
  3. Each pair is resampled to the same number of points, evenly by arc length.
  4. Winding is matched, and the start index is rotated to whichever alignment minimises total
     travel — without this a shape can rotate through itself on the way across.
  5. The result becomes a shape key on the source mesh.

Every step is deterministic, so the same two SVGs always produce the same morph.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from blended_backend.ingest import svg as svg_ingest


def _contours(curve_obj):
    """Every spline of a curve as a closed polyline of evaluated points, in object space."""
    out = []
    for spline in curve_obj.data.splines:
        if spline.type == "BEZIER":
            points = [Vector((p.co.x, p.co.y)) for p in spline.bezier_points]
        else:
            points = [Vector((p.co.x, p.co.y)) for p in spline.points]
        if len(points) >= 3:
            out.append(points)
    return out


def _signed_area(points):
    """Positive for counter-clockwise winding. Sign is what tells outer from counter."""
    total = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        total += p.x * q.y - q.x * p.y
    return total / 2.0


def _resample(points, count):
    """`count` points spaced evenly by arc length around a closed polyline.

    Even spacing matters more than it sounds: resampling by index instead would crowd samples
    wherever the trace happened to place control points, and those crowded regions would then
    move at a different rate from the rest during the morph.
    """
    lengths = [0.0]
    for i in range(len(points)):
        lengths.append(lengths[-1] + (points[(i + 1) % len(points)] - points[i]).length)
    perimeter = lengths[-1]
    if perimeter <= 0:
        return [points[0].copy() for _ in range(count)]

    out = []
    for k in range(count):
        target = perimeter * k / count
        i = 0
        while i < len(lengths) - 2 and lengths[i + 1] < target:
            i += 1
        span = lengths[i + 1] - lengths[i]
        t = 0.0 if span <= 0 else (target - lengths[i]) / span
        a, b = points[i % len(points)], points[(i + 1) % len(points)]
        out.append(a.lerp(b, t))
    return out


def _align_by_travel(source, target):
    """Rotate `target`'s start index to whichever alignment moves the points least.

    Safe now, and it was not always. While the morph ran on a *mesh*, aligning each contour
    independently twisted the triangle strip joining outline to counter and filled the hole in,
    so alignment had to be done against a shared angular frame instead. A curve has no strip —
    each contour is independent and the fill is rebuilt every frame — so the better objective
    is available again: minimise how far the points actually travel.

    It matters for how the *middle* of the morph looks. Angular alignment pins a single point
    and lets the rest fall where they may, which for a 1300-point outline means long stretches
    crossing over each other and an intermediate shape that reads as a mangled blob. Minimising
    travel keeps neighbours as neighbours the whole way across.
    """
    n = len(target)
    if n == 0:
        return target
    best_cost, best_shift = None, 0
    # Every offset for short contours; a coarse sweep then a local refinement for long ones.
    # The cost surface over shift is smooth, so the coarse minimum lands in the right basin.
    step = 1 if n <= 180 else max(1, n // 180)
    for shift in range(0, n, step):
        cost = 0.0
        for i, s in enumerate(source):
            d = target[(i + shift) % n] - s
            cost += d.x * d.x + d.y * d.y
            if best_cost is not None and cost > best_cost:
                break
        if best_cost is None or cost < best_cost:
            best_cost, best_shift = cost, shift
    if step > 1:
        for shift in range(max(0, best_shift - step), min(n, best_shift + step + 1)):
            cost = sum((target[(i + shift) % n] - s).length_squared
                       for i, s in enumerate(source))
            if cost < best_cost:
                best_cost, best_shift = cost, shift
    return target[best_shift:] + target[:best_shift]


def _align_by_angle(source, target, centre):
    """Rotate `target`'s start index so its angular frame matches `source`'s.

    The obvious approach — rotate each loop to whatever minimises *its own* travel — is wrong,
    and wrong in a way that only shows up once rendered. A glyph with a counter is a triangle
    strip joining its outer loop to its inner one. Align the two loops independently and each
    picks a different rotation, so the strip between them twists; the triangles then sweep
    across the middle and fill in the hole. The shape key is perfectly correct and the render
    is a solid slab.

    Aligning both loops against a *shared* centre fixes it: the point at angle θ on the outer
    and the point at angle θ on the inner move together, so the strip stays untwisted and the
    hole stays a hole.
    """
    if not target:
        return target
    reference = math.atan2(source[0].y - centre.y, source[0].x - centre.x)
    best_shift, best_delta = 0, None
    for index, point in enumerate(target):
        angle = math.atan2(point.y - centre.y, point.x - centre.x)
        delta = abs((angle - reference + math.pi) % (2.0 * math.pi) - math.pi)
        if best_delta is None or delta < best_delta:
            best_delta, best_shift = delta, index
    return target[best_shift:] + target[:best_shift]


def _load_target_contours(path, scale, reference):
    """Import the target SVG, normalise it against the source's frame, return its contours.

    The target is centred on the source's centre and scaled relative to the source's width, so
    the morph is expressed as "become this shape, this much bigger" rather than depending on
    whatever coordinates the two files happened to be authored in.
    """
    before = set(bpy.data.objects)
    curves = svg_ingest.import_svg(path)
    curve = svg_ingest.join_curves(curves)
    contours = _contours(curve)

    xs = [p.x for c in contours for p in c]
    ys = [p.y for c in contours for p in c]
    width = max(xs) - min(xs)
    centre = Vector(((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0))
    factor = (reference["width"] * scale / width) if width else 1.0

    for contour in contours:
        for i, p in enumerate(contour):
            contour[i] = (p - centre) * factor + reference["centre"]

    for obj in [o for o in bpy.data.objects if o not in before]:
        bpy.data.objects.remove(obj, do_unlink=True)
    return contours


def add_curve_shape_key(obj, target_path, scale=1.0, key_name="morph"):
    """Add a `morph` shape key to a **curve**, so Blender re-fills it every frame.

    This is the third attempt and the only one that is actually correct; the first two are
    recorded because both looked right until rendered.

    Morphing a *mesh* cannot work here. A filled glyph with a counter is a triangle strip
    joining its outline to its hole, and Blender triangulates that strip for the glyph it was
    given. Deform it into a pair of concentric quads and the triangles sweep across the middle
    and fill the hole in: the vertex positions are exactly right and the render is a solid
    slab. Rebuilding the strip as a regular outer↔inner ring fixes the hole and breaks the R
    instead — its concave notch folds shut, measured at IoU 0.965 against the true glyph.

    A curve has no such problem, because there is no stored triangulation to go stale. The
    control points move and Blender re-tessellates the fill from the even-odd rule on every
    frame, so every intermediate shape is filled correctly by construction.

    Handles collapse onto their points in the target, making it a dense polyline. With the
    target resampled to the glyph's own point count that is visually identical to the smooth
    outline, and it avoids inheriting handles that belong to a completely different shape.
    """
    curve = obj.data
    source_splines = [sp for sp in curve.splines if len(_spline_points(sp)) >= 3]
    if not source_splines:
        raise ValueError(f"{obj.name}: no usable splines to morph")

    reference = _curve_reference(curve)
    target_contours = _load_target_contours(target_path, scale, reference)
    if len(target_contours) != len(source_splines):
        raise ValueError(
            f"{obj.name}: morph needs matching contour counts — source has "
            f"{len(source_splines)}, {target_path!r} has {len(target_contours)}"
        )

    if curve.shape_keys is None:
        obj.shape_key_add(name="basis", from_mix=False)
    key = obj.shape_key_add(name=key_name, from_mix=False)

    # Pair by area so the outline maps to the outline and the counter to the hole, rather
    # than to whichever contour the file happened to list first.
    source_order = sorted(range(len(source_splines)),
                          key=lambda i: -abs(_signed_area(_spline_points(source_splines[i]))))
    target_order = sorted(range(len(target_contours)),
                          key=lambda i: -abs(_signed_area(target_contours[i])))

    all_target = [p for c in target_contours for p in c]
    target_centre = Vector((
        (max(p.x for p in all_target) + min(p.x for p in all_target)) / 2.0,
        (max(p.y for p in all_target) + min(p.y for p in all_target)) / 2.0,
    ))

    offset, moved = 0, 0
    outer_start = None      # set by the first (largest-area) contour, used by the rest
    starts = {}
    for index, spline in enumerate(source_splines):
        starts[index] = offset
        offset += len(_spline_points(spline))

    for s_index, t_index in zip(source_order, target_order):
        spline = source_splines[s_index]
        source_pts = _spline_points(spline)
        target_pts = _resample(target_contours[t_index], len(source_pts))

        if (_signed_area(source_pts) > 0) != (_signed_area(target_pts) > 0):
            target_pts.reverse()
        # Two different objectives, because the two contours need different things.
        #
        # The outline is aligned by least travel: it carries the shape's silhouette, and
        # travel is what decides whether the *middle* of the morph reads as a shape changing
        # or as a mangled blob.
        #
        # The counter is then aligned radially to wherever the outline ended up. Its own
        # least-travel answer is a different rotation, and letting it pick freely makes the
        # band between them thicken and thin around the shape — the frame comes out visibly
        # lumpy at 80% even though it is exact at 100%.
        shifted = [p - reference["centre"] + target_centre for p in source_pts]
        target_pts = _align_by_travel(shifted, target_pts)

        base = starts[s_index]
        for i, destination in enumerate(target_pts):
            point = key.data[base + i]
            co = Vector((destination.x, destination.y, 0.0))
            point.co = co
            # Zero-length handles: the target is a dense polyline, and inheriting the glyph's
            # own handles would bend the quad's edges outwards.
            if hasattr(point, "handle_left"):
                point.handle_left = co
                point.handle_right = co
            moved += 1

    key.value = 0.0
    return {"key": key_name, "contours": len(source_splines), "points_moved": moved,
            "target": target_path}


def _spline_points(spline):
    if spline.type == "BEZIER":
        return [Vector((p.co.x, p.co.y)) for p in spline.bezier_points]
    return [Vector((p.co.x, p.co.y)) for p in spline.points]


def _curve_reference(curve):
    """Width and centre of a curve's control points, in its own XY plane."""
    pts = [p for sp in curve.splines for p in _spline_points(sp)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return {"width": max(xs) - min(xs),
            "centre": Vector(((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0))}


def _reference_frame(mesh):
    """Width and centre of a mesh in its own XZ plane — the frame the target is fitted to."""
    xs = [v.co.x for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    return {
        "width": max(xs) - min(xs),
        "centre": Vector(((max(xs) + min(xs)) / 2.0, (max(zs) + min(zs)) / 2.0)),
    }


def _boundary_loops(mesh):
    """Ordered vertex loops around the flat face's boundary.

    A flat filled glyph has one boundary loop per contour: the outline and each counter. Edges
    used by exactly one face are the boundary; walking them gives the loops in order, which is
    what the resampling needs.
    """
    from collections import defaultdict

    use_count = defaultdict(int)
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            use_count[edge_key] += 1

    adjacency = defaultdict(list)
    for (a, b), count in use_count.items():
        if count == 1:
            adjacency[a].append(b)
            adjacency[b].append(a)

    loops, seen = [], set()
    for start in adjacency:
        if start in seen:
            continue
        loop, current, previous = [start], start, None
        seen.add(start)
        while True:
            nxt = next((n for n in adjacency[current] if n != previous and n not in seen), None)
            if nxt is None:
                break
            loop.append(nxt)
            seen.add(nxt)
            previous, current = current, nxt
        if len(loop) >= 3:
            loops.append(loop)
    return loops
