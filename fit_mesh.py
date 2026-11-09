"""Run with Blender: blender -b --python fit_mesh.py -- config.json."""
import bpy
import bmesh
import json
import math
import sys
import traceback
from pathlib import Path
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def mesh_stats(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    low = [min(v[i] for v in corners) for i in range(3)]
    high = [max(v[i] for v in corners) for i in range(3)]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(e.is_boundary for e in bm.edges)
    nonmanifold = sum(not e.is_manifold for e in bm.edges)
    bm.free()
    return {'dimensions': [round(high[i] - low[i], 2) for i in range(3)],
            'bounds': [low, high], 'vertices': len(obj.data.vertices),
            'faces': len(obj.data.polygons), 'boundary_edges': boundary,
            'nonmanifold_edges': nonmanifold}


def mesh_volume(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    volume = abs(bm.calc_volume(signed=True))
    bm.free()
    return volume


def head_coverage(helmet, head):
    """Sample shell coverage from the head center and the visible face surface."""
    faces = [tuple(face.vertices) for face in helmet.data.polygons]
    points = [tuple(vertex.co) for vertex in helmet.data.vertices]
    tree = BVHTree.FromPolygons(points, faces)
    bounds = mesh_stats(head)['bounds']
    center_x = (bounds[0][0] + bounds[1][0]) / 2
    center_y = (bounds[0][1] + bounds[1][1]) / 2
    height = bounds[1][2] - bounds[0][2]
    missing = []
    for fraction in (0.55, 0.75):
        origin = Vector((center_x, center_y, bounds[0][2] + height * fraction))
        for label, direction in (
            ('左', (-1, 0, 0)), ('右', (1, 0, 0)),
            ('前', (0, -1, 0)), ('后', (0, 1, 0))
        ):
            if tree.ray_cast(origin, Vector(direction), 500)[0] is None:
                missing.append(f'{label}侧 {round(fraction * 100)}% 高度')
    sample_count = 8
    half_width = (bounds[1][0] - bounds[0][0]) * 0.2
    for fraction in (0.5, 0.65, 0.8):
        target_z = bounds[0][2] + height * fraction
        face_points = [vertex.co for vertex in head.data.vertices
                       if abs(vertex.co.x - center_x) < half_width
                       and abs(vertex.co.z - target_z) < 4]
        if not face_points:
            continue
        front = min(face_points, key=lambda p: p.y)
        sample_count += 1
        origin = Vector((front.x, front.y - 1, front.z))
        if tree.ray_cast(origin, Vector((0, -1, 0)), 120)[0] is None:
            missing.append(f'面部 {round(fraction * 100)}% 高度')
    # The crown can break through a closed-looking shell between side rays.
    width = bounds[1][0] - bounds[0][0]
    length = bounds[1][1] - bounds[0][1]
    for x_fraction, x_label in ((-0.18, '左'), (0, '中'), (0.18, '右')):
        for y_fraction, y_label in ((-0.18, '前'), (0, '中'), (0.18, '后')):
            sample_count += 1
            origin = Vector((center_x + width * x_fraction,
                             center_y + length * y_fraction,
                             bounds[0][2] + height * 0.85))
            if tree.ray_cast(origin, Vector((0, 0, 1)), height)[0] is None:
                missing.append(f'头顶{x_label}{y_label}')
    return {'sample_count': sample_count,
            'covered_count': sample_count - len(missing), 'uncovered': missing}


def join_meshes():
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not meshes:
        raise ValueError('文件中没有网格对象')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def repair_small_defects(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.001)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def repair_boolean_result(obj):
    """Repair local Boolean artifacts, then use a bounded voxel fallback."""
    before = mesh_stats(obj)
    if before['nonmanifold_edges'] == 0:
        return {'method': 'none', 'before': 0, 'after': 0}
    original_volume = mesh_volume(obj)
    original_dimensions = before['dimensions']
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.02)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.02)
    boundary = [edge for edge in bm.edges if edge.is_boundary]
    if boundary:
        bmesh.ops.holes_fill(bm, edges=boundary)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    local = mesh_stats(obj)
    if local['nonmanifold_edges'] == 0:
        volume_change = abs(mesh_volume(obj) - original_volume) / max(original_volume, 1)
        dimension_change = max(abs(a - b) for a, b in zip(local['dimensions'], original_dimensions))
        if volume_change > 0.1 or dimension_change > 4:
            raise ValueError('自动修复改变模型过多，已阻止导出；请调整布尔位置或修复源模型')
        return {'method': 'local_cleanup', 'before': before['nonmanifold_edges'],
                'after': 0, 'volume_change_percent': round(volume_change * 100, 2)}

    obj.data.remesh_voxel_size = 1.0
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.voxel_remesh()
    repaired = mesh_stats(obj)
    if repaired['nonmanifold_edges']:
        raise ValueError(f'自动修复后仍有 {repaired["nonmanifold_edges"]} 条非流形边，已阻止导出')
    volume_change = abs(mesh_volume(obj) - original_volume) / max(original_volume, 1)
    dimension_change = max(abs(a - b) for a, b in zip(repaired['dimensions'], original_dimensions))
    if volume_change > 0.1 or dimension_change > 4:
        raise ValueError('自动修复改变模型过多，已阻止导出；请调整布尔位置或修复源模型')
    return {'method': 'voxel_remesh_1mm', 'before': before['nonmanifold_edges'],
            'after': 0, 'volume_change_percent': round(volume_change * 100, 2)}


def smooth_ear_clearance(cutter, width, height):
    """Fair the ear pockets while keeping their lateral clearance."""
    half_width = width / 2
    group = cutter.vertex_groups.new(name='耳部曲面过渡')
    original = [vertex.co.copy() for vertex in cutter.data.vertices]
    weights = []
    for vertex in cutter.data.vertices:
        p = vertex.co
        side = max(0.0, min(1.0, (abs(p.x) / half_width - 0.55) / 0.22))
        lower = max(0.0, min(1.0, (p.z / height - 0.22) / 0.14))
        upper = max(0.0, min(1.0, (0.82 - p.z / height) / 0.14))
        weight = side * lower * upper
        weights.append(weight)
        if weight > 0.001:
            group.add([vertex.index], weight, 'REPLACE')
    if not any(weight > 0.001 for weight in weights):
        return {'method': 'none', 'reason': '耳部区域未找到'}
    modifier = cutter.modifiers.new('耳部曲面平滑', 'SMOOTH')
    modifier.vertex_group = group.name
    modifier.factor = 1.0
    modifier.iterations = 60
    bpy.context.view_layer.objects.active = cutter
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    # A Laplacian smoother shrinks protrusions. Restore the largest lateral
    # loss per side across the blended patch so ears retain their space.
    losses = {-1: 0.0, 1: 0.0}
    for before, vertex, weight in zip(original, cutter.data.vertices, weights):
        if weight > 0.5:
            side = 1 if before.x >= 0 else -1
            losses[side] = max(losses[side], (abs(before.x) - abs(vertex.co.x)) / weight)
    for before, vertex, weight in zip(original, cutter.data.vertices, weights):
        if weight > 0.001:
            side = 1 if before.x >= 0 else -1
            vertex.co.x += side * losses[side] * weight
    cutter.data.update()
    return {'method': 'ear_surface_fairing', 'iterations': 60,
            'lateral_compensation_mm': {str(side): round(value, 2) for side, value in losses.items()}}


def round_ear_clearance(cutter, width, height):
    """Union broad ellipsoidal ear pockets over detailed ear anatomy."""
    pockets = []
    for side in (-1, 1):
        band = [vertex.co for vertex in cutter.data.vertices
                if height * 0.3 < vertex.co.z < height * 0.76]
        peak = max((side * p.x for p in band), default=0)
        tips = [p for p in band if side * p.x > peak - 5]
        if len(tips) < 12:
            continue
        y_low, y_high = min(p.y for p in tips), max(p.y for p in tips)
        z_low, z_high = min(p.z for p in tips), max(p.z for p in tips)
        if y_high - y_low > 45 or z_high - z_low > 70:
            continue
        center_y = (y_low + y_high) / 2
        center_z = (z_low + z_high) / 2
        radius_y = max(20, (y_high - y_low) / 2 + 10)
        radius_z = max(30, (z_high - z_low) / 2 + 10)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
            location=(side * (peak - 16), center_y, center_z))
        pocket = bpy.context.object
        pocket.name = f'耳部连续曲面_{side}'
        pocket.scale = (22, radius_y, radius_z)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        modifier = cutter.modifiers.new('耳部内腔融合', 'BOOLEAN')
        modifier.operation = 'UNION'
        modifier.solver = 'EXACT'
        modifier.object = pocket
        bpy.context.view_layer.objects.active = cutter
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(pocket, do_unlink=True)
        pockets.append(side)
    if pockets:
        cutter.data.remesh_voxel_size = 1.2
        bpy.ops.object.select_all(action='DESELECT')
        cutter.select_set(True)
        bpy.context.view_layer.objects.active = cutter
        bpy.ops.object.voxel_remesh()
    smoothing = smooth_ear_clearance(cutter, width, height) if pockets else {'method': 'none'}
    return {'method': 'rounded_ear_pockets' if pockets else 'none',
            'sides': pockets, 'smoothing': smoothing}


def make_head_cutter(config):
    """Build the exact head and Boolean cutter used by both preview and fit."""
    bpy.ops.wm.stl_import(filepath=str(config['head_source']))
    head = bpy.context.object
    if head.type != 'MESH' or not head.data.polygons:
        raise ValueError('参考头模没有有效网格')
    head.data.remesh_voxel_size = 1.6
    bpy.context.view_layer.objects.active = head
    bpy.ops.object.voxel_remesh()
    head_bounds = mesh_stats(head)['bounds']
    head_size = [head_bounds[1][i] - head_bounds[0][i] for i in range(3)]
    if min(head_size) <= 0:
        raise ValueError('头模尺寸无效')
    ratios = (config['width'] / head_size[0], config['length'] / head_size[1],
              config['height'] / head_size[2])
    head_x = (head_bounds[0][0] + head_bounds[1][0]) / 2
    head_y = (head_bounds[0][1] + head_bounds[1][1]) / 2
    head_floor = head_bounds[0][2]
    for vertex in head.data.vertices:
        p = vertex.co
        vertex.co = ((p.x - head_x) * ratios[0],
                     (p.y - head_y) * ratios[1],
                     (p.z - head_floor) * ratios[2])
    head.data.update()
    if config.get('head_preview'):
        bpy.ops.object.select_all(action='DESELECT')
        head.select_set(True)
        bpy.context.view_layer.objects.active = head
        bpy.ops.wm.stl_export(filepath=config['head_preview'], export_selected_objects=True)

    # The cutting head reaches below the helmet rim, leaving an entry opening.
    cutter = head.copy()
    cutter.data = head.data.copy()
    bpy.context.collection.objects.link(cutter)
    bpy.context.view_layer.objects.active = cutter
    bpy.ops.object.select_all(action='DESELECT')
    cutter.select_set(True)
    clearance = config['clearance']
    head_height = config['height']
    expand_x = (config['width'] + 2 * clearance) / config['width']
    expand_y = (config['length'] + 2 * clearance) / config['length']
    expand_z = (head_height + 2 * clearance) / head_height
    opening_band = min(18, head_height * 0.1)
    for vertex in cutter.data.vertices:
        p = vertex.co
        p.x *= expand_x
        p.y *= expand_y
        p.z = (p.z - head_height / 2) * expand_z + head_height / 2
        if vertex.co.z < opening_band:
            vertex.co.z -= 30 * (1 - max(0, vertex.co.z) / opening_band)
    cutter.data.update()
    cutter.data.remesh_voxel_size = 1.6
    bpy.ops.object.voxel_remesh()
    ear_surface = round_ear_clearance(cutter, config['width'], head_height)
    if len(cutter.data.polygons) > 30_000:
        simplify = cutter.modifiers.new('布尔刀具网格优化', 'DECIMATE')
        simplify.ratio = 30_000 / len(cutter.data.polygons)
        bpy.ops.object.modifier_apply(modifier=simplify.name)
    if config.get('cutter_preview'):
        bpy.ops.object.select_all(action='DESELECT')
        cutter.select_set(True)
        bpy.context.view_layer.objects.active = cutter
        bpy.ops.wm.stl_export(filepath=config['cutter_preview'], export_selected_objects=True)
    return head, cutter, ear_surface


def boolean_fit(helmet, config, initial, oriented, up_axis):
    """Apply the user's preview pose, subtract the clearance head, then export."""
    scale = config.get('preview_scale', 1.0)
    clearance = config['clearance']
    bounds = mesh_stats(helmet)['bounds']
    center_x = (bounds[0][0] + bounds[1][0]) / 2
    center_y = (bounds[0][1] + bounds[1][1]) / 2
    floor_z = bounds[0][2]
    for vertex in helmet.data.vertices:
        p = vertex.co
        vertex.co = ((p.x - center_x) * scale + config.get('offset_x', 0),
                     (p.y - center_y) * scale + config.get('offset_y', 0),
                     (p.z - floor_z) * scale + config.get('offset_z', 0))
    helmet.data.update()
    if len(helmet.data.polygons) > 500_000:
        simplify = helmet.modifiers.new('布尔前网格优化', 'DECIMATE')
        simplify.ratio = 500_000 / len(helmet.data.polygons)
        bpy.context.view_layer.objects.active = helmet
        bpy.ops.object.modifier_apply(modifier=simplify.name)

    head, cutter, ear_surface = make_head_cutter(config)
    cutter_stats = mesh_stats(cutter)
    before_volume = mesh_volume(helmet)
    # Imported helmets may contain separate overlapping solids. Apply the
    # difference to each closed part; a single Boolean on their joined mesh
    # can treat the overlap as an inverted region and erase the whole helmet.
    bpy.ops.object.select_all(action='DESELECT')
    helmet.select_set(True)
    bpy.context.view_layer.objects.active = helmet
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='LOOSE')
    bpy.ops.object.mode_set(mode='OBJECT')
    parts = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    for part in parts:
        modifier = part.modifiers.new('头模布尔相减', 'BOOLEAN')
        modifier.operation = 'DIFFERENCE'
        modifier.solver = 'EXACT'
        modifier.object = cutter
        bpy.context.view_layer.objects.active = part
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.select_all(action='DESELECT')
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    helmet = bpy.context.view_layer.objects.active
    bpy.data.objects.remove(cutter, do_unlink=True)
    repair = repair_boolean_result(helmet)
    final = mesh_stats(helmet)
    remaining_volume = mesh_volume(helmet)
    removed_volume = before_volume - remaining_volume
    if removed_volume < 1:
        raise ValueError('头模没有切入头盔，未形成佩戴内腔；请调整头盔大小或方向')
    if not final['faces'] or final['faces'] < min(oriented['faces'], 50_000) * 0.05:
        removed_percent = max(0, min(100, removed_volume / max(before_volume, 1) * 100))
        raise ValueError(f'当前姿态下，含 {clearance:g} mm 余量的头模切除了约 {removed_percent:.0f}% 的头盔，外壳几乎消失。请查看淡橙色余量外壳，调整头盔缩放或位置。')
    coverage = head_coverage(helmet, head)
    bpy.ops.object.select_all(action='DESELECT')
    helmet.select_set(True)
    bpy.context.view_layer.objects.active = helmet
    bpy.ops.wm.stl_export(filepath=config['output'], export_selected_objects=True)
    if config.get('preview'):
        bpy.ops.wm.stl_export(filepath=config['preview'], export_selected_objects=True)
    warnings = [f'已按当前预览姿态用扩大 {clearance:g} mm 的头模做布尔差集；打印前仍需实物试戴。']
    if repair['method'] != 'none':
        warnings.append(f'布尔后 {repair["before"]} 条非流形边已自动修复；请复核修复区域的细节。')
    if coverage['uncovered']:
        warnings.insert(0, '头模可能外露：' + '、'.join(coverage['uncovered']) + '。请在预览中调整位移、旋转或缩放。')
    status = '头模布尔相减完成' if not coverage['uncovered'] else '布尔完成，但头模覆盖不足'
    return {'input': initial, 'oriented': oriented, 'output': final, 'up_axis': up_axis,
            'scale': scale,
            'offset': {axis: config.get('offset_' + axis, 0) for axis in ('x', 'y', 'z')},
            'removed_volume_mm3': round(removed_volume, 1),
            'repair': repair,
            'cutter': cutter_stats,
            'ear_surface': ear_surface,
            'coverage': coverage,
            'head': {k: config[k] for k in ('width', 'length', 'height', 'clearance')},
            'warnings': warnings, 'status': status}


def main(config):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if config.get('mode') == 'cutter_preview':
        _, cutter, ear_surface = make_head_cutter(config)
        return {'status': '佩戴余量曲面已更新', 'cutter': mesh_stats(cutter),
                'ear_surface': ear_surface}
    source = Path(config['source'])
    if source.suffix.lower() == '.obj':
        # Three.js OBJLoader keeps file coordinates unchanged. Blender's OBJ
        # defaults rotate Y-up OBJ data into Z-up, so request identity axes.
        bpy.ops.wm.obj_import(filepath=str(source), forward_axis='Y', up_axis='Z')
    else:
        bpy.ops.wm.stl_import(filepath=str(source))
    helmet = join_meshes()
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    initial = mesh_stats(helmet)
    up_axis = config.get('up_axis', 'auto')
    if up_axis not in ('auto', 'Y', 'Z'):
        raise ValueError('朝上轴无效')
    if up_axis == 'auto':
        up_axis = 'Z'
    helmet.rotation_euler = (
        math.radians(config.get('rotation_x', 0) + (90 if up_axis == 'Y' else 0)),
        math.radians(config.get('rotation_y', 0)),
        math.radians(config.get('rotation_z', 0)))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    repair_small_defects(helmet)
    oriented = mesh_stats(helmet)
    dims = oriented['dimensions']
    if min(dims) <= 0:
        raise ValueError('网格尺寸无效')
    if len(helmet.data.polygons) > 1_500_000:
        raise ValueError('网格超过 150 万面，请先简化模型')
    if oriented['nonmanifold_edges']:
        helmet.data.remesh_voxel_size = 0.8
        bpy.context.view_layer.objects.active = helmet
        bpy.ops.object.voxel_remesh()
        if mesh_stats(helmet)['nonmanifold_edges']:
            raise ValueError('原始模型网格修复失败')
    return boolean_fit(helmet, config, initial, oriented, up_axis)


if __name__ == '__main__':
    path = Path(sys.argv[sys.argv.index('--') + 1])
    config = json.loads(path.read_text(encoding='utf-8'))
    try:
        report = main(config)
    except Exception as exc:
        report = {'error': str(exc), 'traceback': traceback.format_exc()}
    Path(config['report']).write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    print('FIT_REPORT', json.dumps(report, ensure_ascii=False))
