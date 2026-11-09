"""Blender mesh primitives for the stand-alone fabrication worker."""
import json
import struct
import tempfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


def select(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def remove(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def duplicate(obj, name):
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = name
    bpy.context.collection.objects.link(copy)
    return copy


def mesh_from(name, verts, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def bounds(obj):
    bpy.context.view_layer.update()
    coords = [obj.matrix_world @ Vector(p) for p in obj.bound_box]
    return ([min(p[i] for p in coords) for i in range(3)],
            [max(p[i] for p in coords) for i in range(3)])


def dimensions(obj):
    low, high = bounds(obj)
    return [round(b-a, 3) for a, b in zip(low, high)]


def bake(obj):
    select(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def recenter(obj, scale=1, offsets=(0, 0, 0)):
    low, high = bounds(obj)
    delta = Vector((-(low[0]+high[0])/2, -(low[1]+high[1])/2, -low[2]))
    obj.data.transform(Matrix.Translation(Vector(offsets)) @ Matrix.Scale(scale, 4) @ Matrix.Translation(delta))
    obj.data.update()


def validate_glb(path):
    def inspect_resource(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == 'uri' and (not isinstance(value, str) or not value.startswith('data:')):
                    raise ValueError('请使用内嵌纹理及网格的 GLB')
                inspect_resource(value)
        elif isinstance(node, list):
            for child in node:
                inspect_resource(child)
    with path.open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError('GLB 文件不完整')
        magic, version, size, n, kind = struct.unpack('<4s4I', header)
        if (magic != b'glTF' or version != 2 or kind != 0x4e4f534a
                or size != path.stat().st_size or n > min(size-20, 32_000_000)):
            raise ValueError('GLB 文件头无效')
        inspect_resource(json.loads(stream.read(n)))


def import_mesh(path):
    path = Path(path)
    if path.suffix.lower() == '.glb':
        validate_glb(path)
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif path.suffix.lower() == '.obj':
        # The fabrication path only needs geometry. An uploaded OBJ may refer
        # to absolute, network, or parent paths in mtllib/call instructions.
        with tempfile.TemporaryDirectory(prefix='helmet-obj-') as temporary:
            clean = Path(temporary)/'mesh.obj'
            allowed = {b'v', b'vn', b'vt', b'f', b'o', b'g', b's'}
            with path.open('rb') as source, clean.open('wb') as out:
                for line in source:
                    token = line.lstrip().split(None, 1)
                    if token and token[0] in allowed:
                        out.write(line)
            bpy.ops.wm.obj_import(filepath=str(clean), forward_axis='Y', up_axis='Z')
    elif path.suffix.lower() == '.stl':
        bpy.ops.wm.stl_import(filepath=str(path))
    else:
        raise ValueError('几何处理支持 GLB、STL 和 OBJ')
    meshes = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not meshes:
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not meshes or sum(len(o.data.polygons) for o in meshes) > 2_000_000:
        raise ValueError('模型为空或超过 200 万面，请先简化模型')
    bpy.context.view_layer.update()
    # Bake every node before joining; shared mesh instances and parent
    # transforms, including negative determinant mirrors, retain world pose.
    transforms = [(o, o.matrix_world.copy()) for o in meshes]
    for obj, matrix in transforms:
        obj.data = obj.data.copy()
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
        obj.data.transform(matrix)
        if matrix.to_3x3().determinant() < 0:
            obj.data.flip_normals()
    select(meshes[0])
    for obj in meshes:
        obj.select_set(True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    return meshes[0]


def write_stl(obj, path):
    select(obj)
    bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True)


def cube(low, high):
    center = [(a+b)/2 for a, b in zip(low, high)]
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.dimensions = [b-a for a, b in zip(low, high)]
    bake(obj)
    return obj


def boolean(obj, tool, operation):
    select(obj)
    modifier = obj.modifiers.new('Fabrication', 'BOOLEAN')
    modifier.solver = 'EXACT'
    modifier.operation = operation
    modifier.object = tool
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.update()


def slice_box(obj, axis, start, end):
    low, high = bounds(obj)
    lo, hi = [x-3 for x in low], [x+3 for x in high]
    lo[axis], hi[axis] = start, end
    tool = cube(lo, hi)
    boolean(obj, tool, 'INTERSECT')
    remove(tool)


def mesh_health(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    nonmanifold = sum(not e.is_manifold for e in bm.edges)
    volume = abs(bm.calc_volume(signed=True))
    bm.free()
    return nonmanifold, volume


def ray_tree(obj):
    mesh = obj.data
    return BVHTree.FromPolygons([v.co for v in mesh.vertices], [p.vertices[:] for p in mesh.polygons])


def intersections(obj, tree):
    polygons = obj.data.polygons
    # Blender's BVH performs triangle intersection tests, omitting simple shared
    # boundaries. Exclude faces meeting at a vertex to ignore zero-area contact.
    return [(a,b) for a,b in tree.overlap(tree) if a < b and
            not set(polygons[a].vertices).intersection(polygons[b].vertices)]


def relax_folded_cells(obj):
    """Untangle local submillimetre voxel folds, with a fixed displacement budget."""
    for _ in range(4):
        pairs = intersections(obj, ray_tree(obj))
        if not pairs:
            return
        ids = {v for pair in pairs for f in pair for v in obj.data.polygons[f].vertices}
        if len(ids) > len(obj.data.vertices)*.07:
            return
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        moves = []
        for i in ids:
            v = bm.verts[i]
            neighbors = [edge.other_vert(v).co for edge in v.link_edges]
            if neighbors:
                delta = (sum(neighbors, Vector())/len(neighbors) - v.co)*.5
                if delta.length > .18:
                    delta.length = .18
                moves.append((v,delta))
        for v,delta in moves:
            v.co += delta
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()


def hits(tree, origin, direction, limit=1200):
    origin, direction = Vector(origin), Vector(direction).normalized()
    distances = []
    offset = 0.
    for _ in range(80):
        if offset >= limit:
            break
        point, _, _, distance = tree.ray_cast(origin + direction*offset, direction, limit-offset)
        if point is None:
            break
        offset += distance
        distances.append(offset)
        offset += .015
    return distances
