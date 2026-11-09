"""Reconstruct a closed fabrication blank from the visible surface of a helmet."""

import math

import numpy as np
from mathutils import Vector

from geometry_mesh import bounds, mesh_from, ray_tree


def sample_surface(source, frame=None, around=384, bands=192, stagger=False):
    low, high = map(Vector, bounds(source))
    center, radius = frame or ((low + high) * 0.5, (high - low).length)
    tree = ray_tree(source)
    directions = np.empty((bands + 1, around, 3))
    distances = np.full((bands + 1, around), np.nan)
    for row in range(bands + 1):
        latitude = math.pi * (row + (0.37 if stagger and 0 < row < bands else 0)) / bands
        for col in range(around):
            angle = math.tau * (col + (0.43 if stagger else 0)) / around
            direction = Vector(
                (
                    math.sin(latitude) * math.cos(angle),
                    math.sin(latitude) * math.sin(angle),
                    math.cos(latitude),
                )
            )
            point, _, _, _ = tree.ray_cast(center + direction * radius, -direction, radius - 0.01)
            directions[row, col] = direction
            if point is not None:
                distances[row, col] = (point - center).length
    return (center, radius), directions, distances


def outer_blank(source, scan=None):
    """Seal the measured exterior; only use this method with explicit consent."""
    low, high = map(Vector, bounds(source))
    (center, radius), directions, distances = scan or sample_surface(source, around=256, bands=128)
    distances = distances.copy()
    bands, around = distances.shape[0] - 1, distances.shape[1]
    faces = []
    missing = ~np.isfinite(distances)
    upper = directions[:, :, 2] >= -0.4
    if np.count_nonzero(missing & upper) > 0.04 * np.count_nonzero(upper):
        raise ValueError("头顶或侧面缺失过多，无法重建完整外形。请补充参考视图。")
    # Interpolate narrow cracks from measured neighbours, wrapping at the seam.
    for _ in range(8):
        neighbours = np.stack(
            (
                np.roll(distances, 1, 1),
                np.roll(distances, -1, 1),
                distances[np.maximum(np.arange(bands + 1) - 1, 0)],
                distances[np.minimum(np.arange(bands + 1) + 1, bands)],
            )
        )
        count = np.isfinite(neighbours).sum(axis=0)
        fill = ~np.isfinite(distances) & (count > 0)
        if not np.any(fill):
            break
        distances[fill] = np.nansum(neighbours, axis=0)[fill] / count[fill]
    missing = ~np.isfinite(distances)
    if np.any(missing & upper):
        raise ValueError("头盔侧面开口过大，请换用其他视图，或保留原始几何。")
    # Cap an unobserved neck inside the source hull. A box cap can invent
    # corners wider than the input when viewed obliquely.
    if np.any(missing):
        coords = np.empty(len(source.data.vertices) * 3, dtype=np.float32)
        source.data.vertices.foreach_get("co", coords)
        xyz = coords.reshape(-1, 3) - np.asarray(center)
        normals = [
            (x, y, z)
            for x in (-1, 0, 1)
            for y in (-1, 0, 1)
            for z in (-1, 0, 1)
            if (x, y, z) != (0, 0, 0)
        ]
        for index in range(100):
            z = 1 - (2 * index + 1) / 100
            angle = math.pi * (3 - math.sqrt(5)) * index
            normals.append(
                (math.sqrt(1 - z * z) * math.cos(angle), math.sqrt(1 - z * z) * math.sin(angle), z)
            )
        rays = directions[missing]
        capped = np.full(len(rays), radius)
        for normal in normals:
            normal = np.asarray(normal)
            support = np.max(xyz @ normal)
            cosine = rays @ normal
            capped = np.minimum(capped, support / np.maximum(cosine, 1e-12))
        distances[missing] = capped
    # Interpolated samples follow a new direction. On the open underside this
    # can put a cap below the original rim, even when its neighbours were sound.
    half = np.asarray(high - low) * 0.5
    limit = np.min(half / np.maximum(np.abs(directions), 1e-12), axis=2)
    distances = np.minimum(distances, limit)
    coords = np.asarray(center) + directions * distances[:, :, None]
    vertices = np.vstack((coords[0, :1], coords[1:-1].reshape(-1, 3), coords[-1, :1])).tolist()

    def index(row, col):
        if row == 0:
            return 0
        if row == bands:
            return len(vertices) - 1
        return 1 + (row - 1) * around + col % around

    for col in range(around):
        faces.append((0, index(1, col), index(1, col + 1)))
        for row in range(1, bands - 1):
            faces.append(
                (index(row, col), index(row + 1, col), index(row + 1, col + 1), index(row, col + 1))
            )
        faces.append((index(bands - 1, col), index(bands, 0), index(bands - 1, col + 1)))
    return mesh_from("重建的打印外壳", vertices, faces)
