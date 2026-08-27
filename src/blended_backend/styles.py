"""Materials and the outline technique. Runs inside Blender.

Scene IR names the *look* ("black outline"); this module picks the *technique*. That indirection
is the point of having an IR — swapping inverted hull for Freestyle later touches nothing else.
"""

from __future__ import annotations

import bpy

from blended_backend import materials

OUTLINE_SUFFIX = "_outline"


# Material construction moved to `materials.py`; these remain as the names callers already use.
# The default surface colour is deliberately light — the source SVG is black-on-transparent, and
# a black logo with a black outline is a black rectangle.
surface_material = materials.surface
unlit_material = materials.unlit


def add_lineart(thickness, color=(0.0, 0.0, 0.0, 1.0), *, name="logo_lineart",
                creases=False, intersections=True, collection=None):
    """Grease Pencil Line Art outline. **This is the default technique.**

    Line Art is a real line renderer: it traces silhouette contours in screen space every frame,
    so the outline is uniform width all the way around an object from *any* camera angle. Every
    geometry-based approach fails at this, because an outline made of solid geometry sitting
    behind the logo is partly occluded by the logo itself, and any depth gap between them turns
    into parallax as soon as the camera leaves head-on. With a camera orbiting for 480 frames,
    that is disqualifying.

    `radius` is what 5.x calls stroke thickness (renamed from `thickness`).

    Creases default OFF. They draw the internal edges where a letter's face meets its side wall,
    which reads as busy comic-book hatching on glyph geometry rather than as an outline.

    Lights are disabled on the layer so the stroke stays pure black while the key light ramps
    from dim to bright — otherwise the "black outline" brightens along with everything else.
    """
    if collection is None:
        bpy.ops.object.grease_pencil_add(type="LINEART_SCENE")
    else:
        bpy.ops.object.grease_pencil_add(type="LINEART_COLLECTION")
    gp = bpy.context.active_object
    gp.name = name
    gp.data.name = name

    modifier = gp.modifiers[0]
    if collection is not None:
        modifier.source_collection = collection
    modifier.radius = thickness
    modifier.use_contour = True
    modifier.use_crease = creases
    modifier.use_intersection = intersections
    modifier.use_material = False
    modifier.use_loose = False

    for material in gp.data.materials:
        if material and material.grease_pencil:
            material.grease_pencil.color = color
    for layer in gp.data.layers:
        layer.use_lights = False
    return gp


def outline_curve(curve_obj, thickness, name=None):
    """Curve-offset outline: a duplicate of the curve grown outward *in its own plane*.

    This is the default, and for vector-derived geometry it is strictly better than an inverted
    hull. Blender's curve `offset` insets/outsets a filled contour using the same fill rule that
    produced it, so holes shrink correctly and the width is exactly uniform. Being a 2D operation
    on the source contour, it cannot produce the spike artifacts that Solidify generates when it
    miters an acute glyph corner.

    Must be called on the curve *before* it is converted to a mesh.
    """
    data = curve_obj.data.copy()
    name = name or curve_obj.name + OUTLINE_SUFFIX
    data.name = name
    obj = bpy.data.objects.new(name, data)
    obj.matrix_world = curve_obj.matrix_world.copy()
    bpy.context.scene.collection.objects.link(obj)

    data.offset = thickness
    return obj


def add_hull_modifier(obj, thickness):
    """Grow a mesh outward into a shell, for use as an outline body.

    Occlusion is handled by depth — the caller keeps the hull shallower than the object it
    outlines, so it can only show around the silhouette. That is simpler and far more robust
    than the classic flipped-normals-plus-backface-culling trick, which hides the rim as
    readily as it hides the front cap.

    Inflate the mesh along its vertex normals. The caller keeps the result shallower than the
    logo in depth, so it can only show around the silhouette — occlusion by depth rather than by
    backface culling.

    Four things were tried here; recording them so they are not retried:

    * **Solidify** gives thickness to an *open* surface. Handed an already-closed solid it builds
      an interior wall and leaves the outer boundary exactly where it was, so the hull comes out
      the same size as the logo and never shows at all. This is why it looked like it "did
      nothing" — it genuinely did nothing to the silhouette.
    * **A true inverted hull** (inflate, reverse faces, backface-culling material) is the textbook
      technique and works well on smooth closed meshes. On a flat-faced extrusion the shell pokes
      through the front cap, streaking black bands across the letter faces.
    * **Curve offset** is clean face-on but Blender's `offset` is a naive per-point normal offset,
      not a real polygon offset: it self-intersects, closes small counters, and merges adjacent
      letters into a slab.
    * **Freestyle** produces no strokes at all under EEVEE in Blender 5.2 ("strokes set empty").

    Feed this *unbeveled* geometry. Inflation self-intersects wherever the distance exceeds the
    local concave feature radius, and a bevel's dense, varying-normal faces make that far worse
    inside letter counters and in the gaps between the logo's dots.

    Known limitation: past roughly 35 degrees off-axis the rim reads as an offset backing rather
    than a uniform outline. ARCHITECTURE 7 already constrains the orbit to a narrower arc for
    legibility, so the two constraints agree — but Phase 2 should confirm it across the sweep.
    """
    import bmesh

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.normal_update()
    for vert in bm.verts:
        vert.co += vert.normal * thickness
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj.visible_shadow = False
    return obj


def paint(objs, material):
    for obj in objs:
        obj.data.materials.clear()
        obj.data.materials.append(material)
