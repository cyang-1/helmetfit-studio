"""Cut closed print pieces and add registration pegs where the seam is thick enough."""
import math

import bpy
from mathutils import Vector

from fit_mesh import repair_boolean_result
from geometry_mesh import bake, boolean, bounds, dimensions, duplicate, hits, mesh_health, ray_tree, remove, slice_box, write_stl

# Reserve bed/brim margin and a vertical allowance. Slicer still validates the actual placement.
LIMIT = (238., 238., 249.)


def seam_sites(obj, axis, mid):
    tree = ray_tree(obj)
    low, high = bounds(obj)
    a, b = [i for i in range(3) if i != axis]
    origin = Vector([(x+y)/2 for x, y in zip(low, high)])
    origin[axis] = mid
    sites = []
    for idx in range(20):
        angle = idx * math.tau / 20
        direction = Vector((0., 0., 0.))
        direction[a], direction[b] = math.cos(angle), math.sin(angle)
        start = origin - 1000*direction
        crossing = hits(tree, start, direction, 2000)
        for near, far in zip(crossing[::2], crossing[1::2]):
            if far-near < 8:
                continue
            point = start + direction * ((near+far)/2)
            if any((point-other).length < 35 for other in sites):
                continue
            clear = True
            for inset in (-3.5, 0., 3.5):
                probe = point.copy()
                probe[axis] += inset
                touch, normal, _, distance = tree.find_nearest(probe)
                if touch is None or distance < 3.4 or (probe-touch).dot(normal) > .01:
                    clear = False
                    break
            if clear:
                sites.append(point)
                break
        if len(sites) == 2:
            break
    return sites


def cylinder(axis, point, radius, lo, hi):
    position = point.copy()
    position[axis] += (lo+hi)/2
    orientation = [(0, math.pi/2, 0), (math.pi/2, 0, 0), (0, 0, 0)][axis]
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=radius, depth=hi-lo,
                                        location=position, rotation=orientation)
    obj = bpy.context.object
    bake(obj)
    return obj


def make_pieces(obj, directory, split_axis):
    files, seams = [], []

    def partition(part, label, depth):
        size = dimensions(part)
        oversize = [i for i in range(3) if size[i] > LIMIT[i]]
        forced = depth == 0 and split_axis in ('x','y','z')
        if not forced and not oversize:
            filename = f'part-{label}.stl'
            nm, volume = mesh_health(part)
            if nm:
                repair_boolean_result(part)
                nm, volume = mesh_health(part)
            if nm or volume < 5:
                raise ValueError('分件产生未闭合或过小的零件，请改变切分方向')
            write_stl(part, directory / filename)
            files.append(filename)
            return
        if depth >= 5:
            raise ValueError('模型需要过多切分，请缩小外部装饰或调整方向')
        axis = 'xyz'.index(split_axis) if forced else max(oversize, key=lambda i:size[i]/LIMIT[i])
        low, high = bounds(part)
        mid = (low[axis] + high[axis]) / 2 + .013
        locations = seam_sites(part, axis, mid)
        left, right = duplicate(part, '左件'), duplicate(part, '右件')
        slice_box(left, axis, low[axis]-4, mid)
        slice_box(right, axis, mid, high[axis]+4)
        for site in locations:
            peg = cylinder(axis, site, 1.55, -.65, 3.)
            hole = cylinder(axis, site, 1.80, -.6, 3.4)
            boolean(left, peg, 'UNION')
            boolean(right, hole, 'DIFFERENCE')
            remove(peg)
            remove(hole)
        seams.append(dict(piece=label, axis='XYZ'[axis], plane_mm=round(mid, 2),
                          pegs=len(locations), positions_mm=[[round(v, 2) for v in p] for p in locations]))
        for child, suffix in ((left, 'A'), (right, 'B')):
            partition(child, label + suffix, depth+1)
            remove(child)

    partition(obj, '1', 0)
    return files, seams
