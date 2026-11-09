"""Run with Blender --background --factory-startup --python geometry.py -- config.json."""
import json
import math
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from fit_mesh import repair_boolean_result, repair_small_defects
from geometry_head import head_profiles, measurements, number, suggested_pose, surface_data
from geometry_mesh import (bake, boolean, bounds, cube, dimensions, duplicate, hits, import_mesh, intersections,
                           mesh_from, mesh_health, ray_tree, recenter, relax_folded_cells, remove, select, slice_box, write_stl)
from geometry_parts import LIMIT, make_pieces
from geometry_preflight import preflight
from geometry_cap import cap_checks, paired_ports
from geometry_stock import outer_blank


def prepare(source, directory, params):
    obj = import_mesh(source)
    dims = measurements(params)
    low, high = bounds(obj)
    original = [b-a for a, b in zip(low, high)]
    if min(original) <= 0:
        raise ValueError('输入几何为平面或为空，需要具有体积的头盔模型')
    is_glb = Path(source).suffix.lower() == '.glb'
    # Generative GLBs are often in arbitrary normalized units. Choose a nominal
    # adult outer size explicitly and let fit parameters determine the final pose.
    factor = (245 / original[2]) if is_glb else 1.
    recenter(obj, scale=factor)
    normalized = dimensions(obj)
    if min(normalized) < .1 or max(normalized) > 3000:
        raise ValueError('模型比例异常。请以毫米提交单件头盔或移除背景和地面')
    write_stl(obj, directory/'model.stl')
    select(obj)
    # glTF reports physical metres; STL positions throughout this worker are mm.
    obj.scale = (.001, .001, .001)
    bpy.ops.export_scene.gltf(filepath=str(directory/'display.glb'), export_format='GLB',
                              use_selection=True, export_animations=False)
    return dict(dimensions=normalized, defaults=suggested_pose(normalized, dims, params.get('structure','full_shell')), warnings=[],
                normalized_scale=factor, faces=len(obj.data.polygons))


def forms(directory, params, bottom=-35):
    dims = measurements(params)
    head_rows, cutter_rows = head_profiles(dims)
    cutter_rows.insert(0, (min(bottom, -35.), cutter_rows[0][1]))
    head = mesh_from('测量头模', *surface_data(head_rows))
    cutter = mesh_from('直线戴入包络', *surface_data(cutter_rows))
    write_stl(head, directory/'head.stl')
    write_stl(cutter, directory/'cutter.stl')
    return head, cutter, dims


def warning_dimensions(dims):
    delta = abs(dims['requested_mm'] - dims['brow_perimeter_mm'])
    if delta > 15:
        return [f'头围与按实测宽长计算的眉线周长相差 {delta:.0f} mm。当前采用实测宽长，建议复量并试环。']
    return []


def prepare_stock(helmet, params):
    helmet.rotation_euler = tuple(math.radians(number(params, f'r{a}', 0, -180, 180)) for a in 'xyz')
    bake(helmet)
    recenter(helmet, number(params, 'scale', 1, .1, 6),
             tuple(number(params, f'd{a}', 0, -300, 300) for a in 'xyz'))
    if max(dimensions(helmet)) > 1200:
        raise ValueError('头盔尺寸超过 1200 mm，检查缩放与单位后继续')
    repair_small_defects(helmet)
    nm, _ = mesh_health(helmet)
    if not nm:
        return []
    before = dimensions(helmet)
    select(helmet)
    helmet.data.remesh_voxel_size = max(.8, max(before)/550)
    bpy.ops.object.voxel_remesh()
    nm, volume = mesh_health(helmet)
    after = dimensions(helmet)
    if nm or volume < 10 or max(abs(a-b) for a,b in zip(before, after)) > 4:
        raise ValueError('源网格存在开边，修复后外形不稳定。请上传封闭模型或重新生成外形')
    return ['已重建源网格以修复开边，细小装饰请对照原图检查。']


def vertex_islands(bm):
    remaining = set(bm.verts)
    while remaining:
        group = [remaining.pop()]
        for vertex in group:
            for edge in vertex.link_edges:
                other = edge.other_vert(vertex)
                if other in remaining:
                    remaining.remove(other)
                    group.append(other)
        yield group


def connected_count(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    count = sum(1 for _ in vertex_islands(bm))
    bm.free()
    return count


def trim_boolean_specks(obj):
    """Remove detached sub-100 mm³ machining slivers, preserving actual ornaments."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    groups = list(vertex_islands(bm))
    if len(groups) < 2:
        bm.free()
        return 1, 0
    groups.sort(key=len, reverse=True)
    deleted = []
    count = 0
    for group in groups[1:]:
        if len(group) > 5000:
            continue
        lo = [min(v.co[i] for v in group) for i in range(3)]
        hi = [max(v.co[i] for v in group) for i in range(3)]
        extent = [b-a for a,b in zip(lo,hi)]
        if min(extent) > 1.6 or max(extent) > 35:
            continue
        faces = {f for v in group for f in v.link_faces}
        volume = 0.
        for face in faces:
            vertices = list(face.verts)
            a = vertices[0].co
            for v1,v2 in zip(vertices[1:],vertices[2:]):
                volume += a.dot(v1.co.cross(v2.co)) / 6
        if abs(volume) < 100:
            deleted.extend(group)
            count += 1
    if deleted:
        bmesh.ops.delete(bm, geom=deleted, context='VERTS')
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return len(groups)-count, count


def inspect_shell(helmet, dims, eye_z, eye_width, clearance):
    tree = ray_tree(helmet)
    height = dims['height']
    # Optical corridor intersects a real geometry opening at both eyes.
    eyes = [not hits(tree, (side*min(30., eye_width*.30), 0, eye_z), (0,-1,0))
            for side in (-1, 1)]
    wall_samples, missing_wall = [], []
    for z in (.45, .62, .76, .86):
        for i in range(16):
            angle = math.tau * i / 16
            direction = (math.cos(angle), math.sin(angle), 0.)
            crossings = hits(tree, (0,0,z*height), direction)
            if len(crossings) >= 2:
                wall_samples.append(crossings[1]-crossings[0])
            elif i in (0, 4, 8) and z >= .62:
                missing_wall.append((z,i))
    crown = hits(tree, (0.,0.,height*.85), (0,0,1))
    if len(crown) >= 2:
        wall_samples.append(crown[1]-crown[0])
    # Downward rays from inside the head form must not strike the finished shell.
    head_rows, _ = head_profiles(dims)
    brow = min(head_rows, key=lambda each:abs(each[0]-.64*height))[1]
    insertion_collisions = 0
    for radius_fraction in (.5,.93, .995):
        for i in range(0,len(brow),4):
            angle = math.tau*i/len(brow)
            r = brow[i]*radius_fraction
            if hits(tree, (r*math.cos(angle),r*math.sin(angle),height*.64), (0,0,-1)):
                insertion_collisions += 1
    minimum = min(wall_samples, default=0)
    return dict(crown_closed=len(crown)>=2, eye_open=all(eyes), eye_rays=eyes,
                self_intersection_pairs=len(intersections(helmet, tree)),
                insertion_collision_samples=insertion_collisions, insertion_samples=72,
                sampled_wall_min_mm=round(minimum,2), wall_samples=len(wall_samples),
                missing_structural_rays=len(missing_wall), requested_clearance_mm=clearance)


def make_ring(helmet, directory, height, open_allowed=False, head_width=0):
    ring = duplicate(helmet, '试戴环')
    try:
        slice_box(ring, 2, height-3., height+3.)
        nm, volume = mesh_health(ring)
        if nm or volume < 10 or connected_count(ring) != 1:
            return False, '试戴截面不连续或从眼窗穿过，请调整试环高度。'
        if any(a>b for a,b in zip(dimensions(ring), LIMIT)):
            return False, '试戴环超出 P1S 留边范围。请调整截面高度或外部装饰。'
        tree = ray_tree(ring)
        covered = [len(hits(tree, (0,0,height), (math.cos(i*math.tau/48),math.sin(i*math.tau/48),0))) >= 2
                   for i in range(48)]
        if not all(covered):
            # A faithful open band must be a single printable piece, reach
            # both sides of the wearer, and sample at least a half-turn.
            low, high = bounds(ring)
            if (not open_allowed or sum(covered) < 24
                    or low[0] > -.35*head_width or high[0] < .35*head_width):
                return False, '当前截面未形成连续且覆盖两侧的试戴件，请上下调整截取位置。'
            write_stl(ring, directory/'ring.stl')
            return True, 'open_band'
        write_stl(ring, directory/'ring.stl')
        return True, ''
    finally:
        remove(ring)


def fit(source, directory, params):
    helmet = import_mesh(source)
    structure=params.get('structure','full_shell')
    if structure not in ('full_shell','cap_mask'):
        raise ValueError('未知佩戴结构')
    open_cap=structure=='cap_mask'
    if params.get('rebuild_outer') and not open_cap:
        blank = outer_blank(helmet)
        remove(helmet)
        helmet = blank
    warnings = prepare_stock(helmet, params)
    if params.get('rebuild_outer') and not open_cap:
        warnings.append('已按可见外表面重建制造模型，凹槽及原有开孔可能变化，请核对成品预览。')
    # Keep identical preview and boolean cutter for the same parameters.
    _, cutter, dims = forms(directory, params, bottom=min(-35, float(params.get('dz',0) or 0)-5))
    warnings.extend(warning_dimensions(dims))
    boolean(helmet, cutter, 'DIFFERENCE')
    if not len(helmet.data.polygons):
        raise ValueError('外形被头模完全切除，请增大外形或调整位置')
    eye_z = number(params, 'eye_z', .59 if open_cap else .55, .25, .85) * dims['height']
    eye_width = number(params, 'eye_width', 100, 40, 175)
    eye_height = number(params, 'eye_height', 26, 6, 80)
    eye_ports = None
    if open_cap:
        eye_ports = paired_ports(helmet, eye_z, eye_width, eye_height, params.get('eye_window', False))
        if eye_ports['cut']:
            warnings.append('仅为被遮挡的眼位开了独立椭圆视孔，请核对眼线与鼻梁。')
    elif params.get('eye_window', True):
        low, _ = bounds(helmet)
        tool = cube((-eye_width/2, min(low[1]-3,-1), eye_z-eye_height/2),
                    (eye_width/2, 0, eye_z+eye_height/2))
        boolean(helmet, tool, 'DIFFERENCE')
        remove(tool)
    if not len(helmet.data.polygons):
        raise ValueError('切除后没有剩余外壳，请调整缩放与眼窗')
    repair = repair_boolean_result(helmet)
    if repair['method'] != 'none':
        warnings.append('布尔结果经过局部修复，请核对预览。')
    components, removed_specks = trim_boolean_specks(helmet)
    if removed_specks:
        warnings.append(f'已移除 {removed_specks} 处布尔产生的分离薄屑（每处体积小于 100 mm³）；请对照原图复核细节。')
    nm, volume = mesh_health(helmet)
    if nm or volume < 100:
        raise ValueError('成品网格不闭合或剩余材料过少，请调整头盔')
    checks = inspect_shell(helmet, dims, eye_z, eye_width, dims['clearance'])
    if checks['self_intersection_pairs']:
        # Folded internal surfaces can be manifold and still unprintable.
        # Reconstruct once and re-run all fit checks on the actual new mesh.
        original_size = dimensions(helmet)
        original_volume = volume
        select(helmet)
        helmet.data.remesh_voxel_size = max(.65, max(original_size)/480)
        bpy.ops.object.voxel_remesh()
        relax_folded_cells(helmet)
        components, _ = trim_boolean_specks(helmet)
        nm, volume = mesh_health(helmet)
        if (nm or abs(volume-original_volume) > original_volume*.05 or
                max(abs(a-b) for a,b in zip(original_size, dimensions(helmet))) > 2):
            raise ValueError('网格自交且自动修复超出外形容差，请修改外形或调整姿态')
        warnings.append('已重建自交区域，请对照原图复核细节；内腔与壁厚已在修复后重新检查。')
        checks = inspect_shell(helmet, dims, eye_z, eye_width, dims['clearance'])
    checks.update(nonmanifold_edges=nm, volume_cm3=round(volume/1000,1), dimensions_mm=dimensions(helmet))
    checks['structure']=structure
    if open_cap:
        checks.update(cap_checks(helmet,dims,eye_z))
        checks['single_connected_solid'] = components == 1
        checks['eye_ports'] = eye_ports
    blocking = []
    if checks['self_intersection_pairs']:
        blocking.append('网格仍然存在自交，请修复输入网格或重新生成外形。')
    if open_cap:
        if not checks['crown_closed']:
            blocking.append('帽冠未覆盖头顶，请调整帽体高度或尺度。')
        if not checks['mask_front_covered'] or not checks['mask_bridged']:
            blocking.append('面罩或连接帽冠的承力区不足，请向前移动外形，检查鼻梁及两侧连接。')
        if not checks['rear_open']:
            blocking.append('面罩后下方或耳后仍有连续材料。请复核帽沿以下的背片和两侧开口。')
    elif not checks['crown_closed'] or checks['missing_structural_rays'] >= 4:
        blocking.append('头顶或侧后方覆盖不足，请检查朝向、抬高或放大外形。')
    if not checks['eye_open']:
        blocking.append('原有眼孔与双眼位置未对齐，请调整眼线高度或模型姿态。' if open_cap else
                        '双眼位置有材料遮挡，请启用眼窗切除并核对正面。')
    if checks['insertion_collision_samples']:
        blocking.append('入口有干涉，请调整位置或留量。')
    if checks['sampled_wall_min_mm'] < 1.6:
        blocking.append('承力区抽样厚度低于 1.6 mm，请放大外形或减少内腔余量。')
    elif checks['sampled_wall_min_mm'] < 2.4:
        warnings.append('部分抽样壁厚低于 2.4 mm，建议增厚并复核切片路径。')
    if components != 1:
        blocking.append('切后有独立悬空部件。请调整外形位置或重新生成相连的装饰。')
    write_stl(helmet, directory/'fitted.stl')
    ring_z = number(params,'ring_z',.74,.15,.91)*dims['height']
    ring_valid, ring_message = make_ring(helmet, directory, ring_z, open_cap, dims['width'])
    if open_cap and not ring_valid:
        for frac in (.84,.80,.88,.68):
            candidate=frac*dims['height']
            if abs(candidate-ring_z)<1:
                continue
            ring_valid, ring_message=make_ring(helmet,directory,candidate,True,dims['width'])
            if ring_valid:
                ring_z=candidate
                warnings.append(f'所选面罩截面不连续，试戴件已改取帽冠 {frac:.0%} 高度。')
                break
    ring_kind='open_band' if ring_valid and ring_message=='open_band' else 'closed_ring' if ring_valid else None
    if ring_kind=='open_band':
        ring_message=''
        warnings.append('开放试戴带保留真实后口，用于检查两侧宽度与局部松紧；整件摘戴仍需实物验证。')
    if ring_message:
        tree = ray_tree(helmet)
        for suggested in sorted((i/100 for i in range(25,91,3)), key=lambda v:abs(v-ring_z/dims['height'])):
            candidate = suggested*dims['height']
            if all(len(hits(tree,(0,0,z),(math.cos(i*math.tau/48), math.sin(i*math.tau/48),0)))>=2
                   for z in (candidate-3,candidate+3) for i in range(48)):
                ring_message += f' 建议尝试 {suggested:.0%} 高度。'
                break
        warnings.append(ring_message)
    parts, seams = [], []
    if not blocking:
        axis = params.get('split_axis','auto')
        if axis not in ('auto','x','y','z'):
            raise ValueError('切分方向无效')
        try:
            parts, seams = make_pieces(helmet, directory, axis)
        except ValueError as exc:
            blocking.append(str(exc))
        if any(not seam['pegs'] for seam in seams):
            warnings.append('部分切面较薄，未布置定位销。按分件编号对齐接缝并胶合。')
    assembly = []
    for seam in reversed(seams):
        prefix = seam['piece']
        plane = seam['axis']
        details = (f"{seam['pegs']} 组定位销插入对应孔；直径预留 0.50 mm（单边 0.25 mm），先干装确认"
                   if seam['pegs'] else '此处无定位销；先用纸胶带固定并试合')
        assembly.append(f'合并 {prefix}A 和 {prefix}B（{plane}={seam["plane_mm"]:g} mm 切面）。{details}，'
                        f'确认接缝齐平后胶合，组合件记为 {prefix}。')
    return dict(parts=parts, seams=seams, assembly=assembly, warnings=warnings, checks=checks, blocking=blocking,
                dimensions=dimensions(helmet), head=dims, ring_valid=ring_valid, ring_kind=ring_kind,
                ring_z_mm=round(ring_z,2))


def run(config):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    directory = Path(config['directory']).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    params = config.get('params',{})
    if config['mode'] == 'prepare':
        return prepare(config['source'], directory, params)
    if config['mode'] == 'preflight':
        return preflight(config['source'],directory,params)
    if config['mode'] == 'cutter':
        _, _, dims = forms(directory, params, bottom=min(-35, float(params.get('dz',0) or 0)-5))
        return dict(head=dims, warnings=warning_dimensions(dims))
    if config['mode'] == 'fit':
        return fit(config['source'], directory, params)
    raise ValueError('未知几何任务')


if __name__ == '__main__':
    config = json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8-sig'))
    output = Path(config['directory'])
    output.mkdir(parents=True, exist_ok=True)
    try:
        report = run(config)
    except Exception as exc:
        traceback.print_exc()
        report = {'error': str(exc) or type(exc).__name__}
    tmp = output/'geometry-report.tmp'
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(output/'report.json')
    if report.get('error'):
        sys.exit(1)
