"""Camera placement and lighting rigs. Runs inside Blender.

Spherical coordinates are the shared language here, and Phase 2's `camera.orbit` is just this
placement swept over time:

    azimuth   0째 = -Y, the front of a normalized asset (see normalize.FRONT_AXIS)
              increasing = counter-clockwise viewed from +Z
    elevation 0째 = level with the target, +90째 = directly overhead
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from blended_backend.normalize import world_bounds


def spherical(target, distance, azimuth_deg, elevation_deg):
    """World position at a spherical offset from `target`."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    horizontal = distance * math.cos(el)
    return Vector(
        (
            target.x + horizontal * math.sin(az),
            target.y - horizontal * math.cos(az),
            target.z + distance * math.sin(el),
        )
    )


def aim_at(obj, target):
    """Point an object's -Z axis at a world position. Works for both cameras and lights."""
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def fit_distance(camera_obj, objs, margin=1.15):
    """Distance at which `objs` fill the frame with `margin` headroom.

    Uses the camera's own `angle_x`/`angle_y` so sensor fit and aspect ratio are respected
    rather than reimplemented. Half the depth is added so a rotated object cannot clip the
    near plane as it turns.
    """
    lo, hi = world_bounds(objs)
    size = hi - lo
    cam = camera_obj.data
    half_w, half_h, half_d = size.x / 2.0, size.z / 2.0, size.y / 2.0

    needed_x = half_w / math.tan(cam.angle_x / 2.0)
    needed_y = half_h / math.tan(cam.angle_y / 2.0)
    return max(needed_x, needed_y) * margin + half_d


def add_camera(objs, *, lens=50.0, azimuth=0.0, elevation=0.0, margin=1.15, distance=None,
               clip_start=0.01, clip_end=1000.0, name="camera"):
    """Create a camera framing `objs`, and make it the scene camera."""
    scene = bpy.context.scene
    data = bpy.data.cameras.new(name)
    data.lens = lens
    # Blender's 0.1 default near clip is bigger than this whole subject. A camera that starts
    # beside the logo sits ~0.1 units from it, so the default silently clips away the very
    # geometry the shot is about.
    data.clip_start = clip_start
    data.clip_end = clip_end
    cam = bpy.data.objects.new(name, data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    lo, hi = world_bounds(objs)
    target = (lo + hi) / 2.0
    if distance is None:
        distance = fit_distance(cam, objs, margin)
    cam.location = spherical(target, distance, azimuth, elevation)
    aim_at(cam, target)
    return cam, target, distance


def add_key_light(target, *, energy=200.0, azimuth=-35.0, elevation=35.0, distance=4.0,
                  size=3.0, name="key_light", color=(1.0, 1.0, 1.0)):
    """A single area light aimed at the target.

    The goal's "lightsource shining on it" and "dim to bright" both act on this one object —
    Phase 2's `light.ramp` keyframes `energy` here.
    """
    data = bpy.data.lights.new(name, type="AREA")
    data.energy = energy
    data.size = size
    data.color = color
    light = bpy.data.objects.new(name, data)
    light.location = spherical(Vector(target), distance, azimuth, elevation)
    aim_at(light, target)
    bpy.context.scene.collection.objects.link(light)
    return light


def add_rim_light(target, *, energy=80.0, azimuth=150.0, elevation=20.0, distance=4.0,
                  size=2.0, name="rim_light"):
    """Back light that separates the subject from a dark background."""
    return add_key_light(
        target, energy=energy, azimuth=azimuth, elevation=elevation,
        distance=distance, size=size, name=name,
    )


#: The sun's actual angular diameter seen from Earth. Blender's default, and the reason a SUN
#: lamp needs no faking — it is already a physically correct directional source.
SUN_ANGLE_DEGREES = 0.526


def enable_depth_of_field(camera, objs, *, f_stop=4.0, focus_offset=0.0, name="focus_target"):
    """Focus the camera on the subject, tracked by an empty rather than a fixed distance.

    A literal focus *distance* would need re-keyframing every time the camera moves — and this
    camera travels from 0.4 to 2.7 units out. An empty parented to nothing but sitting at the
    subject centre keeps focus locked on the logo for the whole move, for free.
    """
    lo, hi = world_bounds(objs)
    centre = (lo + hi) / 2.0

    focus = bpy.data.objects.new(name, None)
    focus.empty_display_type = "SPHERE"
    focus.empty_display_size = 0.05
    focus.location = (centre.x, centre.y + focus_offset, centre.z)
    bpy.context.scene.collection.objects.link(focus)

    camera.data.dof.use_dof = True
    camera.data.dof.focus_object = focus
    camera.data.dof.aperture_fstop = f_stop
    return focus


def enable_motion_blur(shutter=0.5):
    scene = bpy.context.scene
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = shutter


def add_post_effects(*, bloom=0.0, bloom_threshold=1.0):
    """Compositor pass: bloom around bright highlights.

    Bloom is a real optical effect — light scattering inside the lens around bright sources — so
    wet specular highlights blooming slightly reads as photographed rather than rendered.
    EEVEE's legacy Bloom toggle was removed in 4.2 and Cycles never had one; the compositor's
    Glare node is the route for both.

    Two Blender 5.x API changes are baked in here:

    * Compositing is a **node group** on `scene.compositing_node_group`. `scene.node_tree` no
      longer exists, and `CompositorNodeComposite` is gone — the group's own input and output
      sockets are the render result and the final image.
    * Glare's settings moved from node properties onto **input sockets**, so `glare_type`,
      `threshold` and `mix` as attributes all raise.
    """
    scene = bpy.context.scene
    if bloom <= 0:
        return None

    # KNOWN BROKEN on Blender 5.2 in background mode. Assigning `compositing_node_group`
    # short-circuits the render: the frame comes back pure white in ~130ms, i.e. the render
    # never runs. Verified with the group wired both ways and with `use_nodes` on and off.
    #
    # Raising rather than returning quietly, because the failure mode is a silently blown-out
    # frame — the same class of bug as the magenta textures, and not one to ship twice.
    raise NotImplementedError(
        "post.bloom is not supported: the Blender 5.2 compositor node group short-circuits "
        "background renders (pure white output). Apply bloom in post, or in the GUI."
    )

    group = bpy.data.node_groups.new("post", "CompositorNodeTree")
    group.interface.new_socket("Image", in_out="INPUT", socket_type="NodeSocketColor")
    group.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")

    group_in = group.nodes.new("NodeGroupInput")
    group_out = group.nodes.new("NodeGroupOutput")
    glare = group.nodes.new("CompositorNodeGlare")
    glare.inputs["Type"].default_value = "Bloom"
    glare.inputs["Quality"].default_value = "High"
    glare.inputs["Threshold"].default_value = bloom_threshold
    glare.inputs["Strength"].default_value = bloom
    glare.inputs["Size"].default_value = 0.6

    group.links.new(group_in.outputs[0], glare.inputs["Image"])
    group.links.new(glare.outputs["Image"], group_out.inputs[0])
    scene.compositing_node_group = group
    # Deprecated in 5.x and slated for removal in 6.0, but still the switch that actually gates
    # whether compositing runs. Without it the group is assigned and silently ignored.
    scene.use_nodes = True
    return group


def add_sun(target, *, energy=3.0, azimuth=-35.0, elevation=25.0, angle=SUN_ANGLE_DEGREES,
            color=(1.0, 0.96, 0.90), name="sun"):
    """A real sun: a directional light with the sun's angular diameter.

    Position is irrelevant to a SUN lamp — only rotation matters, because the rays are parallel.
    It is still placed out along the direction vector so the object sits somewhere sensible when
    the `.blend` is opened, but `distance` is deliberately not a parameter: accepting one would
    imply an effect it cannot have.

    `angle` drives shadow softness. At the true 0.526° shadows are crisp with a faint penumbra,
    which is what makes light shafts read as sunlight rather than as a spotlight.
    """
    data = bpy.data.lights.new(name, type="SUN")
    data.energy = energy
    data.angle = math.radians(angle)
    data.color = color
    sun = bpy.data.objects.new(name, data)
    sun.location = spherical(Vector(target), 10.0, azimuth, elevation)
    aim_at(sun, target)
    bpy.context.scene.collection.objects.link(sun)
    return sun


def add_spot(target, *, energy=800.0, azimuth=-50.0, elevation=38.0, distance=5.0,
             spot_size=45.0, spot_blend=0.25, radius=0.1, color=(1.0, 0.96, 0.90),
             name="lamp"):
    """A lamp: an intense spot throwing a bounded cone from one position.

    Unlike a sun, a spot lights a *pool* rather than everything — which is what "a lamp shining
    on it from the corner" means, and it falls off with distance so the surround stays dark.

    Its cone is also the reason a spot produces far better visible beams than a sun does. A sun
    fills the whole volume uniformly, so fog just reads as haze; a spot's cone has edges, and
    edges are what the eye reads as a shaft of light.

    `radius` is the emitter size and drives shadow softness: near 0 gives hard, dramatic shadows,
    larger values soften them.
    """
    data = bpy.data.lights.new(name, type="SPOT")
    data.energy = energy
    data.spot_size = math.radians(spot_size)
    data.spot_blend = spot_blend
    data.shadow_soft_size = radius
    data.color = color
    lamp = bpy.data.objects.new(name, data)
    lamp.location = spherical(Vector(target), distance, azimuth, elevation)
    aim_at(lamp, target)
    bpy.context.scene.collection.objects.link(lamp)
    return lamp


def add_floor(objs, *, size=40.0, material=None, offset=0.0, name="floor"):
    """A ground plane sitting at the base of the subject.

    Placed at the subject's lowest point so it appears to rest on the ground. The subject spans
    roughly z ∈ [-0.12, 0.12] once normalized, so a plane at the origin would slice straight
    through it — hence measuring rather than assuming z=0.
    """
    lo, hi = world_bounds(objs)
    centre = (lo + hi) / 2.0

    mesh = bpy.data.meshes.new(name)
    half = size / 2.0
    corners = [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)]
    mesh.from_pydata(corners, [], [(0, 1, 2, 3)])
    mesh.update()

    # UVs in world units (1 UV = 1 Blender unit) rather than the usual 0..1 across the face.
    # A 0..1 layout would stretch one texture tile across the entire 60-unit plane; this way
    # the material's `scale` means "tiles per unit" and stays meaningful at any floor size.
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        x, y, _ = corners[loop.vertex_index]
        uv_layer.data[loop.index].uv = (x, y)

    floor = bpy.data.objects.new(name, mesh)
    floor.location = (centre.x, centre.y, lo.z - offset)
    bpy.context.scene.collection.objects.link(floor)
    if material is not None:
        floor.data.materials.append(material)
    return floor


def add_atmosphere(objs, *, density=0.12, size=None, anisotropy=0.4, samples=96,
                   color=(1.0, 1.0, 1.0), name="atmosphere"):
    """A bounded box of scattering medium — the air the sunlight becomes visible in.

    **A bounded volume object, deliberately not the world Volume socket.** Connecting Volume
    Scatter to the world output renders the frame completely black in EEVEE 5.2, at any density
    and any light energy — verified down to density 0.001. A world volume also has no bounds, so
    every volumetric sample is spread across the whole `volumetric_start..end` range regardless of
    where the subject actually is, which wastes nearly all of them on empty space.

    A box sized to the action concentrates samples where they matter and renders correctly.

    `use_volumetric_shadows` is the setting that turns haze into shafts: without it the medium
    glows uniformly, with it the subject occludes the volume and light rays appear through the
    gaps between letters.
    """
    scene = bpy.context.scene
    eevee = scene.eevee
    eevee.use_volumetric_shadows = True
    eevee.volumetric_samples = samples
    eevee.volumetric_shadow_samples = 32

    lo, hi = world_bounds(objs)
    centre = (lo + hi) / 2.0
    if size is None:
        # Generous: has to contain the subject, the floor it shadows onto, and the camera as it
        # pulls away — being inside the fog is correct, being outside it makes the effect vanish.
        size = max(hi.x - lo.x, hi.z - lo.z) * 9.0

    eevee.use_volume_custom_range = True
    eevee.volumetric_start = 0.05
    eevee.volumetric_end = size * 2.0

    bpy.ops.mesh.primitive_cube_add(size=size, location=(centre.x, centre.y, centre.z + size / 4))
    box = bpy.context.active_object
    box.name = name
    box.data.name = name
    box.visible_shadow = False  # the medium must not occlude the light creating the shafts

    material = bpy.data.materials.new(f"{name}_mat")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    principled = tree.nodes.new("ShaderNodeVolumePrincipled")
    principled.inputs["Density"].default_value = density
    principled.inputs["Color"].default_value = (*color, 1.0)
    # Forward scattering: light bends slightly toward its original direction, which is what makes
    # shafts brighten as the camera comes round toward the sun.
    principled.inputs["Anisotropy"].default_value = anisotropy
    tree.links.new(principled.outputs["Volume"], output.inputs["Volume"])
    box.data.materials.append(material)
    return box


def set_environment_hdri(path, *, strength=1.0, visible_to_camera=False,
                         background=(0.0, 0.0, 0.0, 1.0), rotation=0.0):
    """Light and reflect from an HDRI while keeping the visible background black.

    This is the single biggest photorealism lever after the renderer itself. With one lamp and a
    black world, everything not directly lit is *pure black* and every reflective surface mirrors
    nothing — which is exactly what reads as "computer graphics". A real environment fills the
    shadows and, critically, gives wet or glossy surfaces something to reflect.

    The trick is a Light Path node. `Is Camera Ray` is 1 for rays straight from the camera and 0
    for reflection, shadow and diffuse-bounce rays, so mixing on it lets the camera see a black
    background while every *other* ray still sees the HDRI. The mood stays dark; the physics
    stop being a void.
    """
    scene = bpy.context.scene
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    links = tree.links

    output = tree.nodes.new("ShaderNodeOutputWorld")
    mix = tree.nodes.new("ShaderNodeMixShader")
    light_path = tree.nodes.new("ShaderNodeLightPath")

    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = bpy.data.images.load(path, check_existing=True)
    env_background = tree.nodes.new("ShaderNodeBackground")
    env_background.inputs["Strength"].default_value = strength

    if rotation:
        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation))
        coord = tree.nodes.new("ShaderNodeTexCoord")
        links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], environment.inputs["Vector"])

    plain = tree.nodes.new("ShaderNodeBackground")
    plain.inputs["Color"].default_value = background
    plain.inputs["Strength"].default_value = 1.0

    links.new(environment.outputs["Color"], env_background.inputs["Color"])
    # Fac 0 -> first shader (HDRI, for reflections and bounce); Fac 1 -> second (flat backdrop).
    links.new(light_path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(env_background.outputs["Background"], mix.inputs[1])
    links.new(plain.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])

    if visible_to_camera:
        links.new(env_background.outputs["Background"], output.inputs["Surface"])

    scene.world = world
    return world


def set_world(color=(0.02, 0.02, 0.025, 1.0), strength=1.0, name="world"):
    world = bpy.data.worlds.new(name)
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = color
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.world = world
    return world
