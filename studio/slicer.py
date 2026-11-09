"""Create and verify one re-openable Bambu Studio project per physical plate."""
import hashlib
import json
import math
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

from .runtime import run_process


def installation():
    path = Path(os.getenv('BAMBU_STUDIO_EXE', str(Path(os.getenv('ProgramFiles', 'C:/Program Files')) /
                    'Bambu Studio/bambu-studio.exe')))
    if not path.is_file():
        raise ValueError('未找到 Bambu Studio。请安装带 P1S 预设的 Bambu Studio 或设置 BAMBU_STUDIO_EXE。')
    return path


def resolve_profile(folder, name, chain=()):
    if name in chain or Path(name).name != name:
        raise ValueError('切片预设引用异常，请检查 Bambu Studio 安装。')
    path = folder / (name + '.json')
    if not path.is_file():
        raise ValueError('Bambu Studio 缺少预设：' + name)
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    settings = {}
    parents = ([raw['inherits']] if raw.get('inherits') else []) + raw.get('include', [])
    for parent in parents:
        settings.update(resolve_profile(folder, parent, (*chain, name)))
    settings.update({k: v for k, v in raw.items() if k not in ('inherits', 'include')})
    return settings


def print_profiles(executable, target, supports):
    directory = Path(os.environ.get('BAMBU_BBL_PROFILES', executable.parent / 'resources/profiles/BBL'))
    machine = resolve_profile(directory / 'machine', 'Bambu Lab P1S 0.4 nozzle')
    process = resolve_profile(directory / 'process', machine['default_print_profile'])
    material = resolve_profile(directory / 'filament', machine['default_filament_profile'][0])
    process.update(enable_support='1' if supports else '0')
    if supports:
        process.update(support_type='tree(auto)', support_on_build_plate_only='1', support_threshold_angle='30')
    profiles = [machine, process, material]
    paths = []
    for kind, config in zip(('machine', 'process', 'filament'), profiles):
        path = target / (kind + '.json')
        path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        paths.append(path)
    return paths, profiles


def verify_build_mesh(package):
    """Follow 3MF build/component references and stream-check the actual meshes."""
    documents = {}

    def load(member):
        if member in documents:
            return documents[member]
        objects, build = {}, []
        current = None
        def start(tag, attrs):
            nonlocal current
            tag = tag.rsplit(':', 1)[-1]
            if tag == 'model' and attrs.get('unit', 'millimeter') != 'millimeter':
                raise ValueError('制造工程单位应为毫米。')
            if tag == 'object':
                current = dict(vertices=0, faces=0, refs=[])
                objects[attrs['id']] = current
            elif tag == 'item':
                build.append((member, attrs['objectid']))
            elif tag == 'component' and current is not None:
                path = attrs.get('p:path', member).lstrip('/')
                current['refs'].append((path, attrs['objectid']))
            elif tag == 'vertex' and current is not None:
                if not all(math.isfinite(float(attrs[k])) for k in ('x', 'y', 'z')):
                    raise ValueError('工程网格坐标异常。')
                current['vertices'] += 1
            elif tag == 'triangle' and current is not None:
                ids = {int(attrs[k]) for k in ('v1', 'v2', 'v3')}
                if len(ids) < 3 or min(ids) < 0 or max(ids) >= current['vertices']:
                    raise ValueError('工程网格三角面无效。')
                current['faces'] += 1
        def end(tag):
            nonlocal current
            if tag.rsplit(':', 1)[-1] == 'object':
                current = None
        parser = expat.ParserCreate()
        parser.StartElementHandler, parser.EndElementHandler = start, end
        with package.open(member) as stream:
            parser.ParseFile(stream)
        documents[member] = (objects, build)
        return objects, build

    def visit(ref, seen):
        if ref in seen:
            raise ValueError('工程模型引用存在循环。')
        objects, _ = load(ref[0])
        obj = objects.get(ref[1])
        if not obj or not obj['faces'] and not obj['refs']:
            raise ValueError('切片工程缺少可编辑几何。')
        for child in obj['refs']:
            visit(child, seen | {ref})
    _, build = load('3D/3dmodel.model')
    if not build:
        raise ValueError('切片工程没有任何摆盘对象。')
    for ref in build:
        visit(ref, set())


def inspect_package(path, profiles):
    machine, process, material = profiles
    with zipfile.ZipFile(path) as package:
        settings = json.loads(package.read('Metadata/project_settings.config'))
        for key in ('printer_model', 'printable_area', 'bed_exclude_area', 'machine_start_gcode',
                    'machine_end_gcode', 'printable_height', 'nozzle_diameter'):
            if settings.get(key) != machine[key]:
                raise ValueError('工程未正确应用 P1S 预设：' + key)
        if (settings.get('curr_bed_type') != 'Textured PEI Plate'
                or float(settings.get('layer_height', 0)) != float(process['layer_height'])
                or settings.get('enable_support') != process['enable_support']):
            raise ValueError('工程未正确回读打印板、层高或支撑配置。')
        if (settings.get('filament_type', [None])[0] != 'PLA' or
                float(settings['filament_density'][0]) != float(material['filament_density'][0])):
            raise ValueError('工程的 PLA 预设未生效。')
        verify_build_mesh(package)
        plate = ET.fromstring(package.read('Metadata/slice_info.config')).find('.//plate')
        if plate is None or not plate.findall('object') or any(
                item.attrib.get('skipped') == 'true' for item in plate.findall('object')):
            raise ValueError('切片器跳过了零件，请调整摆放。')
        meta = {i.get('key'): i.get('value') for i in plate.findall('metadata')}
        meters = sum(float(i.get('used_m') or 0) for i in plate.findall('filament'))
        grams = sum(float(i.get('used_g') or 0) for i in plate.findall('filament'))
        if meta.get('outside') == 'true' or meters <= 0:
            raise ValueError('零件超出打印空间，或没有生成有效耗材料段。')
        with package.open('Metadata/plate_1.gcode') as stream:
            header = stream.peek(1024)[:1024].decode('utf-8', 'replace')
            in_wall = False
            extruded = False
            for line in stream:
                if line.startswith(b'; FEATURE:'):
                    in_wall = b'wall' in line.lower()
                if in_wall and line.startswith((b'G1 ', b'G2 ', b'G3 ')) and (
                        b' X' in line or b' Y' in line) and re.search(rb' E(?:[1-9]|0*\.0*[1-9])', line):
                    extruded = True
                    break
            while stream.read(1024 * 1024):
                pass  # Reaching EOF checks the compressed toolpath's CRC as well.
        if not extruded:
            raise ValueError('刀路中未找到模型壁的实际挤出动作。')
        layers = re.search(r'total layer number:\s*(\d+)', header)
        if not layers or int(layers[1]) <= 0:
            raise ValueError('缺少有效的逐层刀路。')
        return dict(layers=int(layers[1]), filament_m=meters, grams=grams,
                    seconds=int(float(meta.get('prediction') or 0)), printer=settings['printer_model'],
                    layer_height=float(settings['layer_height']), supports=meta.get('support_used') == 'true',
                    profile_hash=hashlib.sha256(json.dumps(profiles, sort_keys=True).encode()).hexdigest())


def slice_plate(source, folder, supports=True, cancelled=lambda: False):
    folder.mkdir(parents=True, exist_ok=True)
    executable = installation()
    paths, profiles = print_profiles(executable, folder, supports)
    output = folder / 'plate.3mf'
    output.unlink(missing_ok=True)
    (folder / 'result.json').unlink(missing_ok=True)
    args = [executable, '--load-settings', f'{paths[0]};{paths[1]}', '--load-filaments', paths[2],
            '--curr-bed-type', 'Textured PEI Plate', '--arrange', '1', '--slice', '1',
            '--export-3mf', output.name, source]
    code = run_process(args, folder, cancelled, 1800)
    result = folder / 'result.json'
    detail = json.loads(result.read_text(encoding='utf-8-sig')) if result.exists() else {}
    if code or detail.get('return_code') != 0 or not output.is_file():
        raise ValueError('P1S 切片失败：' + str(detail.get('error_string', code)) +
                         '。请检查零件姿态、打印空间与支撑。')
    return output, inspect_package(output, profiles)
