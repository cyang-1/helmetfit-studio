"""Generate the two original reference images for the studio's built-in example.

Run with Blender 4.5 in background mode. No external artwork or meshes are used.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1] / "static" / "lux-example"
ROOT.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def material(name, color, metallic, roughness):
    value = bpy.data.materials.new(name)
    value.diffuse_color = (*color, 1)
    value.use_nodes = True
    shader = value.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    return value


shell = material("Enamel", (0.62, 0.68, 0.64), 0.52, 0.3)
trim = material("Dark rim", (0.035, 0.048, 0.051), 0.48, 0.27)
glass = material("Amber faceplate", (0.38, 0.095, 0.011), 0.8, 0.18)
orange = material("Signal orange", (0.98, 0.22, 0.04), 0.44, 0.3)


def shape(theta, phi, offset=0):
    return ((1.0 + offset) * math.sin(theta) * math.sin(phi),
            -(1.16 + offset) * math.cos(theta) * math.sin(phi),
            (1.35 + offset) * math.cos(phi))


def surface(name, umin, umax, vmin, vmax, depth, mat, close=False):
    nu, nv = 80, 26
    verts, faces = [], []
    for j in range(nv + 1):
        for i in range(nu + 1):
            u = umin + (umax - umin) * i / nu
            v = vmin(u) + (vmax(u) - vmin(u)) * j / nv
            verts.append(shape(u, v, depth))
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            faces.append((a, a + nu + 1, a + nu + 2, a + 1))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.materials.append(mat)
    for f in mesh.polygons:
        f.use_smooth = True
    wall = obj.modifiers.new("Casting wall", "SOLIDIFY")
    wall.thickness = 0.045 if close else 0.012
    return obj


def mark(name, coords, radius, mat):
    line = bpy.data.curves.new(name, "CURVE")
    line.dimensions = "3D"
    line.resolution_u = 8
    line.bevel_depth = radius
    line.bevel_resolution = 3
    path = line.splines.new("POLY")
    path.points.add(len(coords) - 1)
    for p, co in zip(path.points, coords):
        p.co = (*co, 1)
    obj = bpy.data.objects.new(name, line)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)


def bottom(u):
    return 2.36 + 0.16 * math.cos(2*u)


surface("Helmet casting", -math.pi, math.pi, lambda u: 0.008, bottom, 0, shell, True)
angles = [i * math.tau / 120 for i in range(121)]
mark("Lower edge", [shape(u, bottom(u), 0.012) for u in angles], 0.034, trim)
for width, delta, mat in ((1.48, 0.06, trim), (1.4, 0, glass)):
    surface("Panoramic lens", -width, width,
            lambda u, d=delta: 1.08 + 0.11 * abs(u) - d,
            lambda u, d=delta: 1.75 - 0.16 * abs(u) + d,
            0.035 if mat == trim else 0.062, mat)
for sign in (-1, 1):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=0.28, depth=0.092,
        location=(sign * 0.986, 0.01, -0.05), rotation=(0,math.pi/2,0))
    bpy.context.object.data.materials.append(trim)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=0.19, depth=0.098,
        location=(sign * 1.015, 0.01, -0.05), rotation=(0,math.pi/2,0))
    bpy.context.object.data.materials.append(orange)
    for k in range(3):
        mark("Cheek vent", [shape(sign*(0.46+0.105*k+t*0.2),1.87+t*0.17,0.025)
                            for t in (0,0.3,0.65,1)], 0.021, trim)
    mark("Crown piping", [shape(sign*0.38,0.18+i*0.029,0.015) for i in range(28)],0.014,orange)


def aim(obj, target=(0,0,0)):
    obj.rotation_euler = (Vector(target)-obj.location).to_track_quat("-Z","Y").to_euler()


for pos, energy, size in (((3,-4,5),550,5),((-3,-3,2.6),650,4),((1,4,3.7),800,4)):
    bpy.ops.object.light_add(type="AREA",location=pos)
    light = bpy.context.object
    light.data.energy, light.data.shape, light.data.size = energy,"DISK",size
    aim(light)
world = bpy.data.worlds.new("Studio")
world.use_nodes=True
world.node_tree.nodes["Background"].inputs[0].default_value=(0.77,0.80,0.78,1)
scene.world=world
bpy.ops.object.camera_add()
scene.camera = bpy.context.object
scene.camera.data.type="ORTHO"
scene.camera.data.ortho_scale=3.16
scene.render.engine="CYCLES"
scene.cycles.samples=12
scene.cycles.use_denoising=True
scene.render.resolution_x = scene.render.resolution_y = 576
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format="PNG"
angles = {"front":(2.75,-6,1.45), "side":(-5.4,-1.6,0.72),
          "angle-front":(0,-6,.14), "angle-left":(6,0,.14)}
selected = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else angles
for name in selected:
    view = angles[name]
    scene.camera.location=view
    aim(scene.camera,(0,0,0.14))
    scene.render.filepath=str(ROOT / (name+".png"))
    bpy.ops.render.render(write_still=True)
