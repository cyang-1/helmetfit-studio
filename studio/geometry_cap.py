"""Fabrication checks for a crown and face mask with an intentional rear opening."""

import math
import tempfile
from pathlib import Path

import bpy
import numpy as np

from geometry_mesh import (
    boolean,
    bounds,
    dimensions,
    hits,
    import_mesh,
    intersections,
    mesh_from,
    ray_tree,
    relax_folded_cells,
    remove,
    select,
    write_stl,
)


def rebuild_open_solid(obj, directory):
    """Reconstruct the signed solid locally in a voxel grid, including its inner surface.

    Both sides of the mask, deliberate apertures, and the rear entry survive this
    representation. No missing angular sample is extrapolated across a gap.
    """
    largest = max(dimensions(obj))
    step = max(largest / 320, min(1.15, max(0.4, largest / 200)))
    select(obj)
    obj.data.remesh_voxel_size = step
    bpy.ops.object.voxel_remesh()
    if len(obj.data.polygons) > 900_000:
        raise ValueError("保形修复产生过多网格，请简化模型并保留面罩和开口。")
    # Dual-contour cells sometimes fold at pointed ornament edges. Smooth in
    # measured increments, stopping as soon as the mesh is free of crossings.
    for count in (1, 2, 4, 5):
        if not intersections(obj, ray_tree(obj)):
            break
        select(obj)
        smooth = obj.modifiers.new("折叠网格修整", "SMOOTH")
        smooth.factor = 0.38
        smooth.iterations = count
        bpy.ops.object.modifier_apply(modifier=smooth.name)
        pairs = intersections(obj, ray_tree(obj))
        if 0 < len(pairs) < 12:
            relax_folded_cells(obj)
    # Assess the actual printable triangles, including any new edge introduced
    # by triangulating a non-planar quad during STL serialization.
    with tempfile.TemporaryDirectory(dir=directory, prefix="cap-check-") as tmp:
        candidate = Path(tmp) / "candidate.stl"
        write_stl(obj, candidate)
        return import_mesh(candidate), step


def compare_openings(before, after, regions):
    """Check each clear aperture interior independently, including the lower back."""
    missing = ~np.isfinite(before)
    stable = missing.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        stable &= np.roll(missing, (dy, dx), (0, 1))
    stable[:2] = stable[-2:] = False
    measured = []
    for group in regions(stable):
        if len(group) < 12:
            continue
        y, x = zip(*group)
        measured.append(float(np.mean(np.isfinite(after[np.array(y), np.array(x)]))))
    return len(measured), round(max(measured, default=0.0), 5)


def surface_errors(source, result):
    """Bidirectional nearest-surface distances include the inner mask and its rim."""
    errors = []
    for obj, target in ((source, result), (result, source)):
        tree = ray_tree(target)
        verts = obj.data.vertices
        stride = max(1, len(verts) // 12000)
        distances = [tree.find_nearest(verts[i].co)[3] for i in range(0, len(verts), stride)]
        errors.append(np.percentile(distances, [95, 99]))
    return [round(float(v), 3) for v in np.max(errors, axis=0)]


def paired_ports(helmet, eye_z, eye_width, eye_height, enabled):
    """Cut at each occluded pupil only and stop at the head centre plane."""
    tree = ray_tree(helmet)
    center_x = min(30.0, eye_width * 0.30)
    width = min(30.0, max(10.0, eye_width * 0.27))
    height = min(18.0, max(6.0, eye_height * 0.62))
    low, _ = bounds(helmet)
    sides = []
    for sign, side in ((-1, "left"), (1, "right")):
        x = sign * center_x
        if not enabled or not hits(tree, (x, 0, eye_z), (0, -1, 0)):
            continue
        vertices = []
        pieces = 56
        for y in (min(low[1] - 3, -2.0), 0.0):
            for i in range(pieces):
                angle = math.tau * i / pieces
                vertices.append(
                    (x + math.cos(angle) * width * 0.5, y, eye_z + math.sin(angle) * height * 0.5)
                )
        faces = [tuple(range(pieces)), tuple(reversed(range(pieces, pieces * 2)))]
        for i in range(pieces):
            j = (i + 1) % pieces
            faces.append((i, i + pieces, j + pieces, j))
        cutter = mesh_from("独立眼孔", vertices, faces)
        boolean(helmet, cutter, "DIFFERENCE")
        remove(cutter)
        sides.append(side)
    return dict(
        cut=sides,
        width_mm=round(width, 2),
        height_mm=round(height, 2),
        pupil_distance_mm=round(2 * center_x, 2),
    )


def rear_profile(helmet, height, eye_z, tree=None):
    tree = tree or ray_tree(helmet)

    def occupied(z, angle):
        angle = math.radians(angle)
        return bool(hits(tree, (0, 0, z), (math.sin(angle), -math.cos(angle), 0)))

    levels = [eye_z + fraction * height for fraction in (-0.14, -0.08, -0.02, 0.04)]
    rear = [[occupied(z, a) for a in (145, 165, 180, 195, 215)] for z in levels]
    ears = [[occupied(z, a) for a in (115, 130, 230, 245)] for z in levels]
    open_rear = sum(not any(row[1:4]) for row in rear)
    open_sides = sum(not all(row[:2]) and not all(row[2:]) for row in ears)
    return dict(
        ear_height_rear_clear_levels=open_rear,
        ear_height_sides_clear_levels=open_sides,
        ear_height_rear_obstruction_fraction=round(sum(map(sum, rear)) / 20, 2),
        ear_height_open=open_rear >= 3 and open_sides >= 2,
    )


def cap_checks(helmet, dims, eye_z):
    tree = ray_tree(helmet)
    h = dims["height"]
    width, length = dims["width"], dims["length"]

    def horizontal(z, degrees):
        angle = math.radians(degrees)
        return hits(tree, (0, 0, z), (math.sin(angle), -math.cos(angle), 0))

    def thickness(crossings):
        return crossings[1] - crossings[0] if len(crossings) >= 2 else None

    crown = []
    for x, y in ((0, 0), (-0.13, 0), (0.13, 0), (0, -0.12), (0, 0.12)):
        crown.append(thickness(hits(tree, (width * x, length * y, 0.82 * h), (0, 0, 1))))
    # Check material in the cheek/chin panel below the original eye openings.
    mask = [thickness(horizontal(f * h, angle)) for f in (0.36, 0.42) for angle in (-18, 0, 18)]
    # The crown and mask must share one manifold part (also checked after the
    # Boolean). Verify a chain of material at the nose bridge or BOTH temples.
    pathways = []
    for angle in (0, -45, 45):
        sections = [
            thickness(horizontal(eye_z + dz * h, angle)) for dz in (-0.09, -0.045, 0, 0.045, 0.09)
        ]
        pathways.append(sections)
    bridges = [all(v is not None and v >= 1.6 for v in path) for path in pathways]
    # An empty neck below the helmet counts only if the mask still exists at
    # that same height. Adjacent directions must clear a back sector, not a pinhole.
    rear_levels = 0
    for fraction in (0.20, 0.26, 0.32, 0.38, 0.44, 0.50):
        if not any(len(horizontal(fraction * h, a)) >= 2 for a in (-30, 0, 30)):
            continue
        clear = [not horizontal(fraction * h, a) for a in range(135, 226, 15)]
        rear_levels += any(all(clear[i : i + 3]) for i in range(len(clear) - 2))
    walls = [v for v in crown + mask if v is not None]
    for path, good in zip(pathways, bridges):
        if good:
            walls.extend(path)
    profile = rear_profile(helmet, h, eye_z, tree)
    return dict(
        crown_closed=crown[0] is not None and sum(v is not None for v in crown) >= 3,
        mask_front_covered=sum(v is not None for v in mask) >= 4,
        mask_bridged=bridges[0] or (bridges[1] and bridges[2]),
        rear_open=rear_levels >= 2 and profile["ear_height_open"],
        rear_open_levels=rear_levels,
        **profile,
        sampled_wall_min_mm=round(min(walls, default=0), 2),
        wall_samples=len(walls),
    )
