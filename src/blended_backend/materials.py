"""Material library. Runs inside Blender.

Split out of `styles.py`, which now owns only outline technique. Materials are built from
Blender's own texture nodes rather than hand-authored maps — Voronoi and Noise *are* the
library here, so there is nothing to reinvent.

Every builder takes an optional `texture_set`, currently unused. That is the seam where real
PBR maps (Poly Haven and friends) slot in later without the callers changing: the IR keeps
saying `"material": "stone"`, and only the resolver learns to fetch images.
"""

from __future__ import annotations

import os

import bpy


def _clear(material):
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    return tree


def surface(name, base_color=(0.9, 0.9, 0.92, 1.0), roughness=0.35, metallic=0.0):
    """A plain Principled surface."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return material


def unlit(name, color=(0.0, 0.0, 0.0, 1.0), backface_culling=True, wipe=False,
          fade=False):
    """Shadeless colour — used by the geometry outline modes and by 2D `flat` assets.

    With `wipe`, the surface additionally gets a hard-edged reveal: world X is compared against
    a threshold, and everything to the right of it is transparent. Two nodes are named so the
    actions can find them without walking the tree — `flat_color` for `object.tint` and
    `wipe_threshold` for `object.reveal`.

    Alpha **CLIP** rather than BLEND: the edge is binary, so there is nothing to sort, and
    blended transparency in EEVEE sorts per-object and would let one letter incorrectly occlude
    another the moment two overlap.
    """
    material = bpy.data.materials.new(name)
    tree = _clear(material)
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.name = "flat_color"
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    material.use_backface_culling = backface_culling

    if not (wipe or fade):
        tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
        return material

    # One alpha chain serves both effects, because they multiply: a letter can be half faded
    # in *and* half revealed at once without either action knowing about the other. Keeping
    # them as separate named Value nodes is what lets two actions animate one material
    # without fighting over the same socket.
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    mix = tree.nodes.new("ShaderNodeMixShader")

    opacity = tree.nodes.new("ShaderNodeValue")
    opacity.name = "opacity"
    opacity.outputs[0].default_value = 1.0

    if not wipe:
        tree.links.new(opacity.outputs[0], mix.inputs["Fac"])
    else:
        geometry = tree.nodes.new("ShaderNodeNewGeometry")
        separate = tree.nodes.new("ShaderNodeSeparateXYZ")
        threshold = tree.nodes.new("ShaderNodeValue")
        threshold.name = "wipe_threshold"
        # Far to the right, so a wipe-capable material is fully visible until something
        # animates it. Starting at 0 would half-hide any object crossing the origin.
        threshold.outputs[0].default_value = 1.0e6

        compare = tree.nodes.new("ShaderNodeMath")
        compare.operation = "LESS_THAN"
        combine = tree.nodes.new("ShaderNodeMath")
        combine.operation = "MULTIPLY"

        tree.links.new(geometry.outputs["Position"], separate.inputs["Vector"])
        tree.links.new(separate.outputs["X"], compare.inputs[0])
        tree.links.new(threshold.outputs[0], compare.inputs[1])
        tree.links.new(compare.outputs["Value"], combine.inputs[0])
        tree.links.new(opacity.outputs[0], combine.inputs[1])
        tree.links.new(combine.outputs["Value"], mix.inputs["Fac"])

    # fac 0 -> transparent, fac 1 -> emission.
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    tree.links.new(emission.outputs["Emission"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], out.inputs["Surface"])

    # EEVEE Next resolves transparency by `surface_render_method`, and its default —
    # DITHERED — is stochastic: it discards pixels to approximate alpha, which stipples the
    # *opaque* parts of the glyph with black speckle even where the wipe is fully open. The
    # legacy `blend_method` alone does not override it. BLENDED does real alpha compositing,
    # which is what a hard binary edge needs, and the usual objection to it (per-object sort
    # order) cannot bite here: the letters are coplanar and never overlap.
    material.blend_method = "BLEND"
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    material.use_backface_culling = backface_culling
    return material


def backdrop(name, image_path=None, color=(1.0, 1.0, 1.0, 1.0), strength=1.0):
    """Shadeless card material: an image, or a flat colour, emitted exactly as authored.

    The image is loaded as **sRGB**, unlike every other texture in this module. The others are
    data — roughness, normals — where sRGB would apply a gamma curve to numbers that are not
    colours. This one is a photograph and genuinely is colour, so sRGB is what reproduces it.

    A missing image renders as flat magenta with no error, so the path is checked here rather
    than discovered in the render.
    """
    material = bpy.data.materials.new(name)
    tree = _clear(material)
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = strength

    if image_path:
        if not os.path.isabs(image_path):
            raise ValueError(
                f"backdrop image must be an absolute path, got {image_path!r} — Blender "
                "re-resolves relative paths against the saved .blend and a failed load "
                "renders as flat magenta with no error"
            )
        if not os.path.exists(image_path):
            raise ValueError(f"backdrop image not found: {image_path!r}")
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(image_path, check_existing=True)
        tex.image.colorspace_settings.name = "sRGB"
        tex.extension = "EXTEND"
        tree.links.new(tex.outputs["Color"], emission.inputs["Color"])
    else:
        emission.inputs["Color"].default_value = color

    tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return material


def stone(name="stone", *, scale=6.0, color_dark=(0.055, 0.052, 0.050, 1.0),
          color_light=(0.185, 0.180, 0.172, 1.0), roughness=0.82, bump=0.28,
          texture_set=None):
    """Procedural rock.

    Two texture layers doing distinct jobs:

    * **Voronoi, `DISTANCE_TO_EDGE`** — the distance to the nearest cell boundary, which reads
      directly as the crack network between stones. This is the layer that makes it look like
      rock rather than noisy concrete.
    * **Noise, high detail** — surface grain, breaking up both colour and roughness so the
      material never looks uniform under a raking light.

    Both feed a Bump node, so the relief is shading-only — no subdivision, no displacement, and
    therefore no geometry cost at 480 frames.

    Object coordinates rather than UVs: the floor is a generated plane with no meaningful UV
    layout, and object space tiles forever without seams.

    `texture_set` is the PBR seam described in the module docstring; procedural is used while it
    is None.
    """
    material = bpy.data.materials.new(name)
    tree = _clear(material)
    links = tree.links

    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    coord = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    # Crack network.
    voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
    voronoi.feature = "DISTANCE_TO_EDGE"
    voronoi.inputs["Scale"].default_value = 2.2
    links.new(mapping.outputs["Vector"], voronoi.inputs["Vector"])

    cracks = tree.nodes.new("ShaderNodeValToRGB")
    cracks.color_ramp.elements[0].position = 0.0
    cracks.color_ramp.elements[1].position = 0.09  # tight ramp = narrow, dark crack lines
    links.new(voronoi.outputs["Distance"], cracks.inputs["Fac"])

    # Surface grain.
    grain = tree.nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 9.0
    grain.inputs["Detail"].default_value = 12.0
    grain.inputs["Roughness"].default_value = 0.62
    links.new(mapping.outputs["Vector"], grain.inputs["Vector"])

    # Colour: grain picks between two greys, cracks darken it.
    grain_mix = tree.nodes.new("ShaderNodeMixRGB")
    grain_mix.blend_type = "MIX"
    grain_mix.inputs["Color1"].default_value = color_dark
    grain_mix.inputs["Color2"].default_value = color_light
    links.new(grain.outputs["Fac"], grain_mix.inputs["Fac"])

    crack_mix = tree.nodes.new("ShaderNodeMixRGB")
    crack_mix.blend_type = "MULTIPLY"
    crack_mix.inputs["Fac"].default_value = 0.85
    links.new(grain_mix.outputs["Color"], crack_mix.inputs["Color1"])
    links.new(cracks.outputs["Color"], crack_mix.inputs["Color2"])
    links.new(crack_mix.outputs["Color"], bsdf.inputs["Base Color"])

    # Roughness varies with grain so highlights break up instead of forming one broad sheen.
    rough_map = tree.nodes.new("ShaderNodeMapRange")
    rough_map.inputs["To Min"].default_value = max(0.0, roughness - 0.12)
    rough_map.inputs["To Max"].default_value = min(1.0, roughness + 0.12)
    links.new(grain.outputs["Fac"], rough_map.inputs["Value"])
    links.new(rough_map.outputs["Result"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.0

    # Relief from both layers: cracks cut in, grain roughens the surface between them.
    height = tree.nodes.new("ShaderNodeMixRGB")
    height.blend_type = "MULTIPLY"
    height.inputs["Fac"].default_value = 0.6
    links.new(cracks.outputs["Color"], height.inputs["Color1"])
    links.new(grain.outputs["Color"], height.inputs["Color2"])

    bump_node = tree.nodes.new("ShaderNodeBump")
    bump_node.inputs["Strength"].default_value = bump
    bump_node.inputs["Distance"].default_value = 0.12
    links.new(height.outputs["Color"], bump_node.inputs["Height"])
    links.new(bump_node.outputs["Normal"], bsdf.inputs["Normal"])

    return material


def pbr(name, texture_set, *, scale=4.0, bump=1.0, wetness=0.0, ripple_strength=0.0,
        frames=None, wet_roughness=0.12, ripple_scale=28.0, ripple_speed=6.0,
        ripple_detail=6.0, wet_flatten=0.25):
    """A scanned PBR material from an image texture set.

    `texture_set` is `{role: absolute_path}`, already downloaded and cached by the **host**
    (`blended.assets.textures`) — the backend has no network and never fetches anything.

    Colour space is the classic trap and is set explicitly per map: the diffuse map is sRGB,
    every other map is data (`Non-Color`). Leaving roughness or normal maps on sRGB applies a
    gamma curve to numbers that are not colours, which quietly produces a flat, plasticky
    surface that looks *almost* right — the hardest kind of wrong to spot.
    """
    material = bpy.data.materials.new(name)
    tree = _clear(material)
    links = tree.links

    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    coord = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coord.outputs["UV"], mapping.inputs["Vector"])

    def image(role, colorspace="Non-Color"):
        path = texture_set.get(role)
        if not path:
            return None
        # Fail loudly. A missing texture otherwise renders as flat magenta with no error at
        # all, which looks like a material bug rather than a missing file.
        if not os.path.isabs(path):
            raise ValueError(f"texture path for {role!r} must be absolute, got {path!r}")
        if not os.path.exists(path):
            raise FileNotFoundError(f"texture {role!r} not found at {path}")
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path, check_existing=True)
        node.image.colorspace_settings.name = colorspace
        node.extension = "REPEAT"
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    diffuse = image("diffuse", "sRGB")
    roughness = image("roughness")
    normal = image("normal")
    ao = image("ao")
    displacement = image("displacement")

    colour_output = diffuse.outputs["Color"] if diffuse else None
    if diffuse and ao:
        # Ambient occlusion multiplied into base colour. Cheap contact shading that keeps
        # crevices dark even under a single hard light.
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = 0.7
        links.new(diffuse.outputs["Color"], mix.inputs["Color1"])
        links.new(ao.outputs["Color"], mix.inputs["Color2"])
        colour_output = mix.outputs["Color"]

    roughness_output = roughness.outputs["Color"] if roughness else None
    bsdf.inputs["Metallic"].default_value = 0.0

    # One wetness mask, shared by colour, roughness and relief, so all three agree on which
    # patches are wet.
    wet_mask = _wetness_mask(tree, mapping, coverage=wetness) if wetness > 0 else None

    if wet_mask is not None and colour_output is not None:
        _apply_wetness(tree, bsdf, mapping, colour_output, roughness_output, wetness,
                       wet_roughness=wet_roughness, mask=wet_mask)
    else:
        if colour_output is not None:
            links.new(colour_output, bsdf.inputs["Base Color"])
        if roughness_output is not None:
            links.new(roughness_output, bsdf.inputs["Roughness"])

    # Relief strength, attenuated where the surface is wet.
    #
    # Water fills surface pores, so wet ground is physically *flatter* than dry ground. Damping
    # relief where wet is therefore correct rather than a cheat — and it is the single biggest
    # cure for the shimmer a moving camera produces on glossy ground, because that shimmer is
    # specular highlights sliding across fine normal detail. Dry patches keep full relief, so
    # the concrete never goes plastic.
    #
    # Measured: relief 1.0 -> 0.25 halved temporal flicker, roughly 8x more effective than 2x
    # supersampling, which barely moved it. This is shading aliasing, not pixel aliasing.
    relief = None
    if wet_mask is not None:
        relief = tree.nodes.new("ShaderNodeMixRGB")
        relief.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
        relief.inputs["Color2"].default_value = (wet_flatten,) * 3 + (1.0,)
        links.new(wet_mask.outputs["Color"], relief.inputs["Fac"])

    normal_output = None
    if normal:
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = bump
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        if relief is not None:
            links.new(relief.outputs["Color"], normal_map.inputs["Strength"])
        normal_output = normal_map.outputs["Normal"]

        if displacement:
            # Bump on top of the normal map: the normal map carries fine detail, the
            # displacement map the larger relief. Chaining them via Bump.Normal keeps both
            # without needing real geometry displacement.
            bump_node = tree.nodes.new("ShaderNodeBump")
            bump_node.inputs["Strength"].default_value = bump * 0.4
            bump_node.inputs["Distance"].default_value = 0.05
            links.new(displacement.outputs["Color"], bump_node.inputs["Height"])
            links.new(normal_map.outputs["Normal"], bump_node.inputs["Normal"])
            if relief is not None:
                # Preserve the 0.4 weighting the displacement layer carries relative to the
                # normal map. Linking `relief` straight in would silently override it and make
                # "no flattening" apply *more* relief than not wiring it at all.
                scaled = tree.nodes.new("ShaderNodeMath")
                scaled.operation = "MULTIPLY"
                scaled.inputs[1].default_value = bump * 0.4
                links.new(relief.outputs["Color"], scaled.inputs[0])
                links.new(scaled.outputs["Value"], bump_node.inputs["Strength"])
            normal_output = bump_node.outputs["Normal"]

    if ripple_strength > 0:
        # Ripples ride on top of the surface normal and are gated by the wetness mask, so the
        # water moves and the dry concrete around it stays still. Without that gate the whole
        # floor shimmers, which reads as a shader artefact rather than as rain.
        ripple_bump = ripples(tree, mapping, frames, strength=ripple_strength,
                              scale=ripple_scale, speed=ripple_speed, detail=ripple_detail)
        if normal_output is not None:
            links.new(normal_output, ripple_bump.inputs["Normal"])
        normal_output = ripple_bump.outputs["Normal"]

    if normal_output is not None:
        links.new(normal_output, bsdf.inputs["Normal"])

    return material


def _animate_socket(tree, socket, start_value, end_value, frames, interpolation="LINEAR"):
    """Keyframe a node socket across the scene's frame range.

    Node trees carry their own `animation_data`, separate from any object's, so texture
    animation lives on the material rather than on the geometry using it.

    Keyframes rather than drivers deliberately: a driver is an expression string evaluated at
    render time, which is exactly the kind of un-inspectable logic this project keeps out of
    scenes. Keyframes are data the Tier-2 probes can read back.
    """
    start_frame, end_frame = frames
    socket.default_value = start_value
    socket.keyframe_insert("default_value", frame=start_frame)
    socket.default_value = end_value
    socket.keyframe_insert("default_value", frame=end_frame)

    anim = tree.animation_data
    if anim and anim.action:
        for curve in _iter_action_fcurves(anim):
            for keyframe in curve.keyframe_points:
                keyframe.interpolation = interpolation


def _iter_action_fcurves(anim):
    """F-curves of an action, across Blender's legacy and slotted Action APIs."""
    action = anim.action
    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield from legacy
        return
    slot = getattr(anim, "action_slot", None)
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != "KEYFRAME":
                continue
            bags = [strip.channelbag(slot)] if slot is not None else list(strip.channelbags)
            for bag in bags:
                if bag is not None:
                    yield from bag.fcurves


def ripples(tree, mapping, frames, *, strength=0.5, scale=28.0, speed=6.0, detail=6.0):
    """Animated water-surface agitation, for puddles under rain.

    4D noise: the fourth coordinate `W` is swept over the frame range, so the pattern evolves
    instead of merely scrolling. Scrolling noise reads as a texture sliding across the ground;
    evolving noise reads as a disturbed liquid surface.

    Motion is what distinguishes *raining* from merely *damp* — a still wet surface looks like
    it rained an hour ago.
    """
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = detail
    noise.inputs["Roughness"].default_value = 0.5
    tree.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    if frames:
        _animate_socket(tree, noise.inputs["W"], 0.0, speed, frames)

    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = 0.02
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    return bump


def droplets(tree, mapping, *, scale=90.0, coverage=0.4, strength=0.6):
    """Water beads clinging to a vertical surface.

    Voronoi `F1` distance is near zero at each cell centre and rises outward, so a tight colour
    ramp on it isolates small round islands — one bead per cell. Ramping rather than using the
    raw distance is what makes them read as discrete droplets instead of a cellular pattern.

    Returns `(bump_node, mask_output)`. The mask also drives roughness: a droplet is a smooth
    water lens on a rougher surface, and that roughness contrast is more of the effect than the
    relief is.
    """
    voronoi = tree.nodes.new("ShaderNodeTexVoronoi")
    voronoi.feature = "F1"
    voronoi.inputs["Scale"].default_value = scale
    tree.links.new(mapping.outputs["Vector"], voronoi.inputs["Vector"])

    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[1].position = max(0.02, 0.10 + coverage * 0.18)
    # Reversed: 1 at the cell centre (bead), falling to 0 at the edges (bare surface).
    ramp.color_ramp.elements[0].color = (1.0, 1.0, 1.0, 1.0)
    ramp.color_ramp.elements[1].color = (0.0, 0.0, 0.0, 1.0)
    tree.links.new(voronoi.outputs["Distance"], ramp.inputs["Fac"])

    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = 0.012
    tree.links.new(ramp.outputs["Color"], bump.inputs["Height"])
    return bump, ramp.outputs["Color"]


def _wetness_mask(tree, mapping, scale=1.6, coverage=0.5):
    """A blotchy 0..1 mask for where water sits.

    Uniform wetness looks like a plastic coating. Real wet ground is patchy — puddles in the low
    spots, damp everywhere else — and that variation is most of what sells it.
    """
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.5
    tree.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])

    ramp = tree.nodes.new("ShaderNodeValToRGB")
    # A tight ramp gives defined puddle edges rather than a soft gradient.
    ramp.color_ramp.elements[0].position = max(0.0, 0.62 - coverage * 0.45)
    ramp.color_ramp.elements[1].position = min(1.0, 0.80 - coverage * 0.35)
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    return ramp


def _apply_wetness(tree, bsdf, mapping, base_color_output, roughness_output, wetness,
                   puddle_scale=1.6, wet_roughness=0.12, mask=None):
    """Darken and smooth a surface where it is wet.

    Two physical facts, and both matter:

    * Wet surfaces are **darker**. Water fills surface pores so less light scatters back out.
    * Wet surfaces are **smoother**. A water film flattens micro-roughness into a near-mirror,
      which is what produces the long specular streaks that read instantly as "wet".

    The second is why wet works so well for a dark subject: matte black returns almost no
    directional light and reads as a flat void, but wet black has strong specular highlights
    that describe the form. Wetness is what makes a near-black material photographable.
    """
    links = tree.links
    if mask is None:
        mask = _wetness_mask(tree, mapping, scale=puddle_scale, coverage=wetness)

    darken = tree.nodes.new("ShaderNodeMixRGB")
    darken.blend_type = "MULTIPLY"
    darken.inputs["Fac"].default_value = wetness
    darken.inputs["Color2"].default_value = (0.35, 0.35, 0.38, 1.0)
    links.new(base_color_output, darken.inputs["Color1"])

    wet_colour = tree.nodes.new("ShaderNodeMixRGB")
    links.new(mask.outputs["Color"], wet_colour.inputs["Fac"])
    links.new(base_color_output, wet_colour.inputs["Color1"])
    links.new(darken.outputs["Color"], wet_colour.inputs["Color2"])
    links.new(wet_colour.outputs["Color"], bsdf.inputs["Base Color"])

    smooth = tree.nodes.new("ShaderNodeMixRGB")
    # NOT a true mirror. Roughness this low on a surface carrying high-frequency normal detail
    # causes specular aliasing: each pixel's reflection direction swings wildly, so sub-pixel
    # highlights pop in and out between frames and the water reads as jittery. A small amount of
    # roughness spreads each highlight over more than a pixel, which is what stabilises it.
    smooth.inputs["Color2"].default_value = (wet_roughness,) * 3 + (1.0,)
    links.new(mask.outputs["Color"], smooth.inputs["Fac"])
    if roughness_output is not None:
        links.new(roughness_output, smooth.inputs["Color1"])
    else:
        smooth.inputs["Color1"].default_value = (0.6, 0.6, 0.6, 1.0)
    links.new(smooth.outputs["Color"], bsdf.inputs["Roughness"])
    return mask


def worn(name, *, base_color=(0.045, 0.045, 0.05, 1.0), roughness=0.38, metallic=0.55,
         wear=0.5, wetness=0.0, droplet_amount=0.0, scale=14.0):
    """A worn, dark, non-plastic surface — blackened metal rather than paint.

    Deliberately **not** pure matte black. A perfectly matte black surface returns almost no
    directional light, so it has no highlights to describe its shape and photographs as a
    silhouette. Slight metallic plus varied roughness gives fresnel rim light and broken
    specular highlights, which is what makes a dark object read as a real one.

    `wear` drives roughness variation: worn surfaces are polished in some places and dulled in
    others, and it is that inconsistency — not the base colour — that separates a real object
    from a CG one.
    """
    material = bpy.data.materials.new(name)
    tree = _clear(material)
    links = tree.links

    out = tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Metallic"].default_value = metallic

    coord = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    grain = tree.nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 5.0
    grain.inputs["Detail"].default_value = 10.0
    grain.inputs["Roughness"].default_value = 0.55
    links.new(mapping.outputs["Vector"], grain.inputs["Vector"])

    spread = max(0.02, wear * 0.3)
    rough_map = tree.nodes.new("ShaderNodeMapRange")
    rough_map.inputs["To Min"].default_value = max(0.02, roughness - spread)
    rough_map.inputs["To Max"].default_value = min(1.0, roughness + spread)
    links.new(grain.outputs["Fac"], rough_map.inputs["Value"])

    # Fine surface relief — micro-scratches and casting texture, not visible bumps.
    bump_node = tree.nodes.new("ShaderNodeBump")
    bump_node.inputs["Strength"].default_value = 0.12 * wear
    bump_node.inputs["Distance"].default_value = 0.005
    links.new(grain.outputs["Fac"], bump_node.inputs["Height"])
    normal_output = bump_node.outputs["Normal"]

    roughness_output = rough_map.outputs["Result"]
    if droplet_amount > 0:
        drop_bump, drop_mask = droplets(tree, mapping, coverage=droplet_amount,
                                        strength=0.6 * droplet_amount)
        links.new(normal_output, drop_bump.inputs["Normal"])
        normal_output = drop_bump.outputs["Normal"]

        # A bead is a smooth lens of water on a rougher surface. That roughness contrast does
        # more to sell the droplets than their relief does — it is what gives each one a tiny
        # specular glint the surrounding material lacks.
        drop_rough = tree.nodes.new("ShaderNodeMixRGB")
        drop_rough.inputs["Color2"].default_value = (0.05, 0.05, 0.05, 1.0)
        links.new(drop_mask, drop_rough.inputs["Fac"])
        links.new(roughness_output, drop_rough.inputs["Color1"])
        roughness_output = drop_rough.outputs["Color"]

    links.new(normal_output, bsdf.inputs["Normal"])

    if wetness > 0:
        colour = tree.nodes.new("ShaderNodeRGB")
        colour.outputs[0].default_value = base_color
        _apply_wetness(tree, bsdf, mapping, colour.outputs[0], roughness_output,
                       wetness, puddle_scale=3.0)
    else:
        links.new(roughness_output, bsdf.inputs["Roughness"])

    return material


BUILDERS = {"stone": stone, "worn": worn}


def build(kind, name=None, *, texture_set=None, **kwargs):
    """Build a material, preferring a real texture set when one has been resolved."""
    if texture_set:
        return pbr(name or kind, texture_set,
                   scale=kwargs.get("scale", 4.0), bump=kwargs.get("bump", 1.0),
                   wetness=kwargs.get("wetness", 0.0),
                   ripple_strength=kwargs.get("ripple_strength", 0.0),
                   frames=kwargs.get("frames"),
                   wet_roughness=kwargs.get("wet_roughness", 0.12),
                   ripple_scale=kwargs.get("ripple_scale", 28.0),
                   ripple_speed=kwargs.get("ripple_speed", 6.0),
                   ripple_detail=kwargs.get("ripple_detail", 6.0),
                   wet_flatten=kwargs.get("wet_flatten", 0.25))
    builder = BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown material {kind!r} (known: {', '.join(sorted(BUILDERS))})")
    for key in ("bump", "ripple_strength", "frames", "texture_set", "wet_roughness",
                "ripple_scale", "ripple_speed", "ripple_detail", "wet_flatten"):
        kwargs.pop(key, None)
    return builder(name or kind, **kwargs)
