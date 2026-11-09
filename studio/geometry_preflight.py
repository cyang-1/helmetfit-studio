"""Inspect the actual normalized fabrication mesh before any helmet fit operation."""

import math
import warnings
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from geometry_mesh import (
    bounds,
    dimensions,
    duplicate,
    import_mesh,
    intersections,
    mesh_health,
    ray_tree,
    remove,
    select,
    write_stl,
)
from geometry_stock import outer_blank, sample_surface
from geometry_cap import compare_openings, rear_profile, rebuild_open_solid, surface_errors


def groups(items, adjacent):
    remaining = set(items)
    while remaining:
        component = [remaining.pop()]
        for item in component:
            for other in adjacent(item):
                if other in remaining:
                    remaining.remove(other)
                    component.append(other)
        yield component


def boundaries(mesh):
    edges = [e for e in mesh.edges if e.is_boundary]
    return list(groups(edges, lambda e: (v for p in e.verts for v in p.link_edges if v.is_boundary)))


def islands(mesh):
    return groups(mesh.verts, lambda v: (e.other_vert(v) for e in v.link_edges))


def extent(vertices):
    xyz = np.array([v.co[:] for v in vertices])
    return float(np.linalg.norm(xyz.max(axis=0) - xyz.min(axis=0)))


def inspect(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    edges = boundaries(bm)
    rim_count = 0
    for component in edges:
        verts = {v for e in component for v in e.verts}
        if all(sum(e.is_boundary for e in v.link_edges) == 2 for v in verts):
            rim_count += 1
    metrics = dict(
        vertices=len(bm.verts),
        faces=len(bm.faces),
        components=sum(1 for _ in islands(bm)),
        boundary_loops=rim_count,
        boundary_edges=sum(map(len, edges)),
        nonmanifold_edges=sum(not e.is_manifold for e in bm.edges),
        volume_cm3=round(abs(bm.calc_volume(signed=True)) / 1000, 3),
        dimensions_mm=dimensions(obj),
    )
    bm.free()
    metrics["intersections"] = len(intersections(obj, ray_tree(obj)))
    return metrics


def local_topology_repair(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.005)
    bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=0.002)
    pieces = sorted(islands(bm), key=len, reverse=True)
    specks = [v for piece in pieces[1:] if len(piece) <= 100 and extent(piece) < 2 for v in piece]
    if specks:
        bmesh.ops.delete(bm, geom=specks, context="VERTS")
    filled = 0
    for edges in boundaries(bm):
        verts = {v for e in edges for v in e.verts}
        if (
            3 <= len(verts) <= 40
            and extent(verts) <= 2
            and all(sum(e.is_boundary for e in v.link_edges) == 2 for v in verts)
        ):
            filled += bool(bmesh.ops.holes_fill(bm, edges=edges).get("faces"))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return dict(filled_tiny_loops=filled, removed_speck_vertices=len(specks))


def angular_components(mask):
    height, width = mask.shape

    def adjacent(point):
        y, x = point
        return (
            (y + j, (x + i) % width)
            for i, j in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1))
            if 0 <= y + j < height
        )

    return groups(zip(*np.where(mask)), adjacent)


def neighbourhood(mask, radius=1):
    result = mask.copy()
    for j in range(-radius, radius + 1):
        for i in range(-radius, radius + 1):
            result |= np.roll(mask, (j, i), (0, 1))
    return result


def pit_targets(scan):
    """Identify compact depressions surrounded by one smooth, measured outer panel.

    A symmetric stencil rejects slopes. A robust quadratic fitted to a collar
    rejects panel seams. Long slots, the neck, and the polar singularities are excluded.
    Holes without a ray hit are never bridged by the automatic repair.
    """
    (center, _), directions, depth = scan
    height, width = depth.shape
    stencils = ((0, 5), (5, 0), (3, 4), (-3, 4), (4, 3), (-4, 3), (2, 5), (-2, 5))
    baselines = [
        (np.roll(depth, (dy, dx), (0, 1)) + np.roll(depth, (-dy, -dx), (0, 1))) * 0.5
        for dy, dx in stencils
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        difference = np.nanpercentile(np.stack(baselines), 30, axis=0) - depth
    top = (directions[:, :, 2] < 0.985) & (directions[:, :, 2] > -0.12)
    seed = ((difference > 1.10) | ~np.isfinite(depth)) & top
    # Join neighbouring pinholes and include pit walls in one contiguous patch.
    merged = neighbourhood(seed, 1) & top
    target = depth.copy()
    changed = np.zeros(depth.shape, dtype=bool)
    features = []
    for group in angular_components(merged):
        if len(group) > 1150:
            continue
        yy, xx = map(np.array, zip(*group))
        x0 = int(xx[0])
        xx = (xx - x0 + width // 2) % width - width // 2
        y0 = int(yy.min())
        row = int(np.median(yy))
        radial = np.nanmedian(depth[row])
        step_y = math.pi * radial / (height - 1)
        step_x = math.tau * radial * math.sin(row * math.pi / (height - 1)) / width
        span_x = (float(np.ptp(xx)) + 1) * step_x
        span_y = (float(np.ptp(yy)) + 1) * step_y
        if max(span_x, span_y) > 40 or max(span_x, span_y) > 4.2 * max(min(span_x, span_y), 1):
            continue
        pad = 7
        yv = np.arange(max(0, y0 - pad), min(height, int(max(yy)) + pad + 1))
        xv = np.arange(min(xx) - pad, max(xx) + pad + 1)
        xgrid, ygrid = np.meshgrid(xv, yv)
        xs = (x0 + xgrid) % width
        z = depth[ygrid, xs]
        local = np.zeros(z.shape, dtype=bool)
        local[yy - yv[0], xx - xv[0]] = True
        support = ~neighbourhood(local, 2) & np.isfinite(z) & ~seed[ygrid, xs]
        if support.sum() < 30:
            continue
        X = (xgrid - np.mean(xx)) / 10
        Y = (ygrid - np.mean(yy)) / 10
        A = np.stack((np.ones_like(X), X, Y, X * X, X * Y, Y * Y), axis=-1)
        keep = support
        for _ in range(3):
            if keep.sum() < 20:
                break
            coef = np.linalg.lstsq(A[keep], z[keep], rcond=None)[0]
            predicted = A @ coef
            residual = np.abs(predicted - z)
            keep = support & (residual < max(0.35, 3 * np.nanmedian(residual[keep])))
        if np.percentile(residual[support], 80) > 1.05:
            continue
        domain = neighbourhood(local, 1)
        depression = predicted - z
        patch = domain & ((depression > 0.32) | ~np.isfinite(z))
        if not np.any(patch):
            continue
        margins = depression[patch & np.isfinite(z)]
        missing = int(np.sum(patch & ~np.isfinite(z)))
        peak = float(max(margins, default=0))
        if not missing and peak < 1.30:
            continue
        gy, gx = ygrid[patch], xs[patch]
        partial = predicted[patch]
        old = target[gy, gx]
        target[gy, gx] = np.where(np.isfinite(old), np.maximum(old, partial), partial)
        changed[gy, gx] = True
        y = int(np.mean(yy))
        x = (x0 + int(np.mean(xx))) % width
        anchor = np.array(center) + directions[y, x] * float(np.median(partial))
        features.append(
            dict(
                center_mm=np.round(anchor, 2).tolist(),
                cells=(gy, gx),
                diameter_mm=round(max(span_x, span_y), 1),
                depth_mm=round(peak, 2),
                missing_rays=missing,
                samples=len(gx),
            )
        )
    return target, changed, features


def lift_outer_vertices(obj, scan, target, changed):
    (center, _), _, depth = scan
    _, width = depth.shape
    count = 0
    for v in obj.data.vertices:
        offset = v.co - center
        r = offset.length
        if r < 1:
            continue
        y = int(round(math.acos(max(-1, min(1, offset.z / r))) * (len(depth) - 1) / math.pi))
        x = int(round(math.atan2(offset.y, offset.x) * width / math.tau)) % width
        if not changed[y, x] or not np.isfinite(depth[y, x]):
            continue
        delta = target[y, x] - r
        # Only the visible layer in a shallow depression may be moved automatically.
        if abs(r - depth[y, x]) <= 1.2 and 0.12 < delta <= 4:
            v.co = center + offset * (target[y, x] / r)
            count += 1
    obj.data.update()
    return count


def silhouette(obj):
    xyz = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", xyz)
    xyz = xyz.reshape(-1, 3)
    dirs = [
        np.array((x, y, z), dtype=float)
        for x in (-1, 0, 1)
        for y in (-1, 0, 1)
        for z in (-1, 0, 1)
        if (x, y, z) != (0, 0, 0)
    ]
    return np.array([np.max(xyz @ (d / np.linalg.norm(d))) for d in dirs])


def drift(source, scanned, other, checked, patch):
    a = scanned[2]
    b = checked[2]
    zone = (scanned[1][:, :, 2] > -0.4) & (scanned[1][:, :, 2] < 0.999)
    both = np.isfinite(a) & np.isfinite(b) & zone
    delta = np.abs(a[both] - b[both])
    outside = np.abs(a[both & ~patch] - b[both & ~patch])
    if not len(delta) or not len(outside):
        raise ValueError("可比较的外表面数据不足")
    return dict(
        p95_mm=round(float(np.percentile(delta, 95)), 3),
        max_mm=round(float(np.max(delta)), 3),
        outside_p99_mm=round(float(np.percentile(outside, 99)), 3),
        silhouette_mm=round(float(np.max(np.abs(silhouette(source) - silhouette(other)))), 3),
        upper_lost_fraction=round(float(np.mean(~np.isfinite(b[np.isfinite(a) & zone]))), 5),
        upper_filled_fraction=round(
            float(np.sum(np.isfinite(b) & ~np.isfinite(a) & zone) / np.sum(zone)), 5
        ),
    )


def clean(stats):
    return not stats["nonmanifold_edges"] and not stats["intersections"] and stats["volume_cm3"] > 0.01


def preflight(source, directory, params):
    generated = bool(params.get("generated"))
    structure = params.get("structure", "full_shell")
    if structure not in ("full_shell", "cap_mask"):
        raise ValueError("未知佩戴结构")
    open_cap = structure == "cap_mask"
    strategy = params.get("repair", "auto")
    if strategy not in ("auto", "outer"):
        raise ValueError("未知源模型修复方式")
    obj = import_mesh(source)
    before = inspect(obj)
    scan = sample_surface(obj)
    if open_cap:
        # Eye openings, nostrils and sculpted facial features are intentional.
        target, patch, features = scan[2], np.zeros(scan[2].shape, dtype=bool), []
    else:
        target, patch, features = pit_targets(scan)
    zero = dict(
        p95_mm=0.0,
        max_mm=0.0,
        outside_p99_mm=0.0,
        silhouette_mm=0.0,
        upper_lost_fraction=0.0,
        upper_filled_fraction=0.0,
    )
    cells = [f.pop("cells") for f in features]
    pits = dict(
        before_count=len(features),
        patched_count=0,
        points_mm=[f["center_mm"] for f in features],
        areas=features,
    )
    quality = dict(
        status="needs_review",
        method="检查原网格",
        message="",
        before=before,
        after=before,
        deviation=zero,
        pits=pits,
        structure=structure,
    )
    if open_cap:
        low, high = bounds(obj)
        height = high[2]-low[2]
        quality["source_rear_profile"] = rear_profile(obj,height,low[2]+.56*height)

    def finish(status, message, mesh=None):
        quality.update(status=status, message=message)
        output = directory / "model.stl"
        if mesh is None:
            output.unlink(missing_ok=True)
        else:
            write_stl(mesh, output)
        return {"quality": quality}

    if (strategy == "auto" or open_cap) and clean(before) and (not generated or not features):
        return finish("passed", "源网格检查通过，已保留原有轮廓和开口。", obj)
    needs_exterior = any(f["missing_rays"] >= 4 or f["depth_mm"] > 4 for f in features)
    if strategy == "auto" and generated and needs_exterior:
        return finish(
            "needs_review",
            "外壳上检测到较深的凹区或贯穿缺口。请对照原图确认开口用途；选择外壳重建可补平孤立凹区并重新制作眼窗。",
        )
    copy = duplicate(obj, "制造修复副本")
    methods = []
    if strategy == "outer" and not open_cap:
        remove(copy)
        try:
            copy = outer_blank(obj, (scan[0], scan[1], target))
        except ValueError as exc:
            return finish("needs_review", str(exc))
        methods.append("可见外壳重建")
        if features:
            methods.append("局部曲面补坑")
    else:
        if generated and features:
            if lift_outer_vertices(copy, scan, target, patch):
                methods.append("局部凹点回填")
        quality["local_repair"] = local_topology_repair(copy)
        methods.append("局部拓扑修复")
        interim = inspect(copy)
        if not clean(interim):
            # Never seal a wide naked boundary in automatic mode.
            bm = bmesh.new()
            bm.from_mesh(copy.data)
            wide = any(extent({v for e in loop for v in e.verts}) > 5 for loop in boundaries(bm))
            bm.free()
            if wide:
                quality.update(after=interim, method="、".join(methods))
                return finish(
                    "needs_review",
                    "网格存在较大的开放边界。请核对眼窗与入口；可手动重建制造外壳，或更换模型。",
                )
            if open_cap:
                methods.append("保留眼孔和后口的双面实体修复")
                try:
                    copy, cell = rebuild_open_solid(copy, directory)
                except ValueError as exc:
                    return finish("needs_review", str(exc))
                quality["cell_mm"] = round(cell, 3)
            else:
                select(copy)
                copy.data.remesh_voxel_size = max(0.72, max(dimensions(copy)) / 320)
                bpy.ops.object.voxel_remesh()
                methods.append("有界体素修复")
                if len(copy.data.polygons) > 900_000:
                    quality.update(method="、".join(methods))
                    return finish(
                        "needs_review", "修复产生过多网格，已停止。可换用较简洁的模型或选择重建外壳。"
                    )
    after = inspect(copy)
    result_scan = sample_surface(copy, frame=scan[0])
    # Evaluate independent directions between the reconstruction vertices.
    check_source = sample_surface(obj, frame=scan[0], stagger=True)
    check_output = sample_surface(copy, frame=scan[0], stagger=True)
    deviation = drift(obj, check_source, copy, check_output, patch)
    quality.update(after=after, method="、".join(methods), deviation=deviation)
    for region in cells:
        # Touching a triangulated vertex exactly can miss both incident faces.
        residual = target[region] - result_scan[2][region]
        finite = residual[np.isfinite(residual)]
        if len(finite) >= max(1, int(0.94 * len(residual))) and np.percentile(finite, 95) < 0.65:
            pits["patched_count"] += 1
    if not clean(after):
        return finish("needs_review", "修复后仍有非流形边或自交。请核对源模型并尝试重建外壳。")
    relative_volume = abs(after["volume_cm3"] - before["volume_cm3"]) / max(before["volume_cm3"], 1)
    if open_cap:
        aperture_count, occluded = compare_openings(check_source[2], check_output[2], angular_components)
        surface95, surface99 = surface_errors(obj,copy)
        quality["opening_check"] = dict(count=aperture_count, worst_occluded_fraction=occluded)
        deviation.update(two_sided_p95_mm=surface95, two_sided_p99_mm=surface99)
        if occluded > .02 or surface95 > 1.1 or surface99 > 2.8 or relative_volume > .05:
            return finish("needs_review", "修复改变了开口或面罩内外表面，请先在建模软件中修复实体。")
    if (strategy == "auto" or open_cap) and (
        deviation["p95_mm"] > 1
        or deviation["outside_p99_mm"] > 2
        or deviation["silhouette_mm"] > 2
        or relative_volume > 0.15
        or deviation["upper_lost_fraction"] > 0.003
        or deviation["upper_filled_fraction"] > 0.002
        or (generated and any(f["missing_rays"] >= 4 or f["depth_mm"] > 4 for f in features))
    ):
        return finish(
            "needs_review",
            "保守修复无法同时保留现有开口、轮廓和局部凹陷。请对比原貌后选择重建外壳或更换输入。",
        )
    if strategy == "outer" and not open_cap and (
        deviation["p95_mm"] > 2
        or deviation["outside_p99_mm"] > 4
        or deviation["silhouette_mm"] > 5
        or deviation["upper_lost_fraction"] > 0.005
    ):
        return finish(
            "needs_review", "重建偏离原有外表轮廓过多，已停止导出。请换用更完整的设计图或模型。"
        )
    return finish(
        "repaired",
        (
            "外壳已重建，原始内腔和开口将由后续定尺步骤重新制作。"
            if strategy == "outer" and not open_cap
            else "源网格已修复，偏差检查通过。"
        ),
        copy,
    )
