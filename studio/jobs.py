import hashlib
import json
import math
import shutil
import threading
import uuid
import zipfile
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .geometry_head import measurements, suggested_pose
from .lux import read_journal
from .report import project_report
from .runtime import ACTIVE, Cancelled, geometry
from .slicer import slice_plate
from .store import Store


def fit_params(data):
    if not isinstance(data, dict):
        raise ValueError('尺寸参数格式不正确。')
    limits = {
        'circumference': (58, 40, 75), 'width': (None, 90, 230), 'length': (None, 125, 290),
        'height': (None, 130, 330), 'clearance': (4, 0.5, 25), 'scale': (1, 0.1, 6),
        'rx': (0, -180, 180), 'ry': (0, -180, 180), 'rz': (0, -180, 180),
        'dx': (0, -300, 300), 'dy': (0, -300, 300), 'dz': (0, -300, 300),
        'eye_width': (100, 40, 175), 'eye_height': (26, 6, 80),
        'eye_z': (0.55, 0.25, 0.85), 'ring_z': (0.74, 0.15, 0.91),
    }
    values = {}
    for key, (default, low, high) in limits.items():
        raw = data.get(key, default)
        if raw in ('', None) and default is None:
            continue
        try:
            number = float(raw)
        except (ValueError, TypeError):
            raise ValueError(f'{key} 需要填写数字。') from None
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'{key} 需在 {low:g}–{high:g} 之间。')
        values[key] = number
    values['eye_window'] = data.get('eye_window', True)
    if not isinstance(values['eye_window'], bool):
        raise ValueError('眼窗参数格式错误。')
    values['rebuild_outer'] = data.get('rebuild_outer', False)
    if not isinstance(values['rebuild_outer'], bool):
        raise ValueError('外壳重建参数格式错误。')
    values['split_axis'] = data.get('split_axis', 'auto')
    if values['split_axis'] not in ('auto', 'x', 'y', 'z'):
        raise ValueError('分件方向无效。')
    return values


class Busy(ValueError):
    pass


def cloud_source(item):
    return item['source_type'] in ('images', 'text')


def wear_structure(item, requested=None):
    value = requested if requested is not None else item.get('wear_structure', 'full_shell')
    if value not in ('cap_mask', 'full_shell'):
        raise ValueError('请选择帽子＋面具或全包头盔。')
    return value


def apply_structure(params, structure, *, initialize=False):
    values = {**params, 'structure': structure}
    if structure == 'cap_mask' and initialize:
        values['eye_window'] = False
        if params.get('eye_z') == 0.55:
            values['eye_z'] = 0.59
    return values


def request_token(data):
    token = data.get('request_id')
    if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', token):
        raise ValueError('提交编号无效，请刷新后再试。')
    return token


def accepted_surface(item):
    return item.get('source_report', {}).get('quality', {}).get('status') in ('passed', 'repaired')


def source_assets(item):
    return {k: v for k, v in item['assets'].items() if k in ('model', 'glb', 'original_model')}


def archive_current(item):
    history = list(item.get('history', []))
    if item['revision'] and (item['assets'].get('fitted') or item['assets'].get('plates')):
        record = {key: item[key] for key in ('revision', 'params', 'assets', 'report')}
        if not history or history[-1]['revision'] != item['revision']:
            history.append(record)
    return history


class Jobs:
    def __init__(self, root, *, executor=None):
        self.store = Store(root)
        self.lock = threading.RLock()
        self.preview_lock = threading.Lock()
        self.lane = executor or ThreadPoolExecutor(1, thread_name_prefix='helmetfit')
        self.running = {}
        for pid in self.store.pending_deletions():
            self._purge_deleted(pid)
        for item in reversed(self.store.all()):
            if item['stage'] in ACTIVE:
                journal = self._cloud_record(item['id'])
                if item['stage'] == 'generating' and journal.get('state') == 'submitting' and not (
                        journal.get('taskId') or item.get('remote_id')):
                    self.store.update(item['id'], stage='failed', uncertain=True,
                                      message='提交过程中断，请先核对 Aholo 任务历史，避免重复扣费。')
                else:
                    self._schedule(item['id'])

    def folder(self, pid):
        return self.store.root / pid

    def _deletion_target(self, pid):
        if not isinstance(pid, str) or not re.fullmatch(r'[0-9a-f]{32}', pid):
            raise ValueError('作品编号无效。')
        folder = self.folder(pid)
        if folder.resolve() != folder or folder.parent != self.store.root:
            raise ValueError('作品目录无效。')
        return folder

    def _purge_deleted(self, pid):
        folder = self._deletion_target(pid)
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError:
            # Windows may still have an active file download. Retry on restart.
            return False
        self.store.finish_deletion(pid)
        return True

    def delete(self, pid):
        self._deletion_target(pid)
        with self.preview_lock, self.lock:
            try:
                item = self.store.get(pid)
            except KeyError:
                item = None
            if pid in self.running or (item and item['stage'] in ACTIVE):
                raise Busy('任务正在处理，请等待结束或先取消任务。')
            self.store.delete(pid)
            return {'deleted': pid, 'cleanup_pending': not self._purge_deleted(pid)}

    def _cloud_record(self, pid):
        return read_journal(self.folder(pid) / 'cloud/lux-task.json')

    def url(self, pid, path):
        return '/artifacts/' + pid + '/' + Path(path).relative_to(self.folder(pid)).as_posix()

    def create(self, folder, request_id, name, inputs, source_type, *, input_mode=None, view_labels=None,
               input_names=None, prompt_spec=None, parent=None, model_structure='full_shell'):
        pid = folder.name
        empty = dict(id=pid, name=name, operation='source', stage='queued', message='等待处理',
                     source_ready=False, source_type=source_type, inputs=inputs, revision=0,
                     input_mode=input_mode, view_labels=view_labels, input_names=input_names, surface_version=0,
                     prompt_spec=prompt_spec, preview_approved=source_type == 'model',
                     wear_structure=(prompt_spec or {}).get('resolved_structure', model_structure),
                     generation_group=pid, generation_number=1,
                     assets={'plates': []}, report={}, params=fit_params({}), history=[])
        with self.lock:
            if parent:
                group = parent.get('generation_group', parent['id'])
                siblings = (p for p in self.store.all() if p.get('generation_group', p['id']) == group)
                empty.update(parent_id=parent['id'], generation_group=group,
                             generation_number=1 + max(p.get('generation_number', 1) for p in siblings))
                if source_type == 'images':
                    empty['wear_structure'] = wear_structure(parent)
            try:
                rec, fresh = self.store.create(empty, request_id)
            except Exception:
                shutil.rmtree(folder)
                raise
            if fresh:
                self._schedule(pid)
            else:
                shutil.rmtree(folder)
            return rec

    def create_text(self, data):
        from .prompts import make_spec
        spec = make_spec(data.get('theme'), data.get('preset', 'character'), data.get('structure', 'auto'))
        token = request_token(data)
        name = data.get('name') or spec['theme']
        if not isinstance(name, str):
            raise ValueError('作品名称无效。')
        folder = self.folder(uuid.uuid4().hex)
        folder.mkdir()
        return self.create(folder, 'text:' + token, name.strip()[:100] or spec['theme'][:100], [],
                           'text', input_mode='text', prompt_spec=spec)

    def _regenerate(self, item, key, spec):
        if not cloud_source(item):
            raise ValueError('导入的本地模型无需云端重新生成。')
        if item.get('uncertain'):
            raise ValueError('请先核对上一笔云任务，再决定是否新建作品。')
        folder = self.folder(uuid.uuid4().hex)
        folder.mkdir()
        try:
            for name in item['inputs']:
                shutil.copyfile(self.folder(item['id']) / name, folder / name)
        except OSError:
            shutil.rmtree(folder)
            raise
        return self.create(folder, key, item['name'], item['inputs'],
                           item['source_type'], input_mode=item.get('input_mode'), view_labels=item.get('view_labels'),
                           input_names=item.get('input_names'), prompt_spec=spec, parent=item)

    def _schedule(self, pid):
        with self.lock:
            stop = threading.Event()
            self.running[pid] = stop
            self.lane.submit(self._work, pid, stop)

    def action(self, pid, action, data):
        with self.lock:
            item = self.store.get(pid)
            if action == 'cancel':
                if item['stage'] in ACTIVE and pid in self.running:
                    self.running[pid].set()
                    return self.store.update(pid, message='正在停止本地任务…')
                return item
            if action == 'approve' and item.get('preview_approved') and cloud_source(item):
                return item
            if action == 'regenerate':
                key = f'regenerate:{pid}:{request_token(data)}'
                spec = item.get('prompt_spec')
                if 'structure' in data:
                    if item['source_type'] != 'text' or not spec:
                        raise ValueError('只有文字生成能修改生成结构。')
                    if data['structure'] != spec.get('structure', 'full_shell'):
                        from .prompts import make_spec
                        spec = make_spec(spec['theme'], spec['preset'], data['structure'])
                        key += ':' + spec['structure']
                previous = self.store.by_request(key)
                if previous:
                    return previous
            if item['stage'] in ACTIVE:
                raise Busy('当前任务尚在处理。')
            if action == 'regenerate':
                return self._regenerate(item, key, spec)
            if action == 'approve':
                if item['stage'] != 'preview' or not item.get('assets', {}).get('glb'):
                    raise ValueError('请先等待模型预览完成。')
                item = self.store.update(pid, preview_approved=True, operation='repair', repair_method='auto',
                                         pending_structure=wear_structure(item, data.get('structure')),
                                         pending_surface=1, stage='queued', message='外观已确认，等待检查制造网格')
            elif action in ('fit', 'repair') and item.get('preview_approved') is False:
                raise ValueError('请先在三维视窗确认生成的外观。')
            elif action == 'fit':
                if not item['source_ready']:
                    raise ValueError('请先完成外形生成。')
                if not accepted_surface(item):
                    raise ValueError('请先在「生成模型」检查并修复网格，再开始调整佩戴。')
                structure = wear_structure(item)
                defaults = {'eye_window': False, 'eye_z': 0.59} if structure == 'cap_mask' else {}
                params = apply_structure(fit_params({**defaults, **data}), structure)
                history = archive_current(item)
                base = source_assets(item)
                item = self.store.update(pid, operation='fit', revision=item['revision'] + 1,
                                         params=params, history=history, assets={**base, 'plates': []},
                                         report={}, stage='queued', message='等待制作适配版本')
            elif action == 'repair':
                if not item.get('source_ready') or not (self.folder(pid) / 'source/model.stl').is_file():
                    raise ValueError('请先生成或导入模型。')
                method = data.get('method', 'auto')
                if method not in ('auto', 'outer'):
                    raise ValueError('未知修复方式。')
                item = self.store.update(pid, operation='repair', repair_method=method,
                                         pending_structure=wear_structure(item, data.get('structure')),
                                         pending_surface=item.get('surface_version', 0) + 1,
                                         stage='queued', message='等待网格检查与修复')
            elif action == 'retry':
                if item['stage'] not in ('failed', 'cancelled'):
                    raise ValueError('当前项目无需重试。')
                if item.get('operation') == 'fit' and not accepted_surface(item):
                    raise ValueError('这个旧版本尚未检查网格。请先在「生成模型」检查并修复。')
                journal = self._cloud_record(pid)
                if item.get('uncertain') or (journal.get('state') == 'submitting' and not journal.get('taskId')):
                    remote = str(data.get('remote_id', ''))
                    if not re.fullmatch(r'[0-9]{1,30}', remote):
                        raise ValueError('请在 Aholo 核对任务历史并填入原任务号。若没有远端任务，可新建作品。')
                    self.store.update(pid, remote_id=remote)
                item = self.store.update(pid, stage='queued', uncertain=False, message='等待重试')
            else:
                raise KeyError('未知操作')
            self._schedule(pid)
            return item

    def _work(self, pid, stop):
        def step(stage, message, **kw):
            if stop.is_set():
                raise Cancelled()
            return self.store.update(pid, stage=stage, message=message, **kw)
        try:
            item = self.store.get(pid)
            if item['operation'] == 'source':
                self._source(item, step, stop)
            elif item['operation'] == 'repair':
                self._preflight(item, step, stop)
            else:
                self._fit(item, step, stop)
        except Cancelled:
            self.store.update(pid, stage='cancelled', message='本地处理已停止，可继续当前任务。')
        except Exception as exc:
            journal = self._cloud_record(pid)
            uncertain = journal.get('state') == 'submitting' and not journal.get('taskId')
            self.store.update(pid, stage='failed', uncertain=uncertain, message=str(exc)[:650] or '处理失败。')
        finally:
            with self.lock:
                if self.running.get(pid) is stop:
                    self.running.pop(pid)

    def _source(self, item, step, stop):
        pid = item['id']
        folder = self.folder(pid)
        inputs = [folder / name for name in item['inputs']]
        if cloud_source(item):
            from .lux import generate, generate_text
            def status(message, remote_id=None):
                if remote_id is not None:
                    self.store.update(pid, remote_id=str(remote_id), uncertain=False)
                step('generating', message)
            step('generating', '正在连接 Aholo Lux3D')
            if item['source_type'] == 'text':
                glb = generate_text(item['prompt_spec'], folder / 'cloud', status, **self.generation_options(item))
            else:
                glb = generate(inputs, folder / 'cloud', status, **self.generation_options(item))
            source = Path(glb)
        else:
            source = inputs[0]
        step('preparing', '正在统一模型单位与坐标')
        dest = folder / 'source'
        report = self.geometry('prepare', dest, source, {**item['params'], 'structure': wear_structure(item)},
                          cancelled=stop.is_set)
        report['pose_structure'] = wear_structure(item)
        assets = {'model': self.url(pid, dest / 'model.stl'),
                  'glb': self.url(pid, source if cloud_source(item) else dest / 'display.glb'),
                  'plates': []}
        defaults = apply_structure(fit_params({**item['params'], **report.get('defaults', {})}),
                                   wear_structure(item), initialize=True)
        if item.get('preview_approved') is False:
            step('preview', '外形已生成，请旋转查看。满意后确认外观，也可以重新生成。',
                 source_ready=True, assets=assets, source_report=report, report=report, params=defaults)
            return
        item = step('repairing', '正在检查孔洞、破面与自交', source_ready=True,
                    operation='repair', pending_surface=1, repair_method='auto',
                    assets=assets, source_report=report, report=report, params=defaults)
        self._preflight(item, step, stop)

    def _preflight(self, item, step, stop):
        pid = item['id']
        root = self.folder(pid)
        version = item.get('pending_surface', 1)
        folder = root / f'surface-{version:03d}'
        step('repairing', '正在检查并处理制造网格')
        structure = wear_structure(item, item.get('pending_structure'))
        diagnostic = self.geometry('preflight', folder, root / 'source/model.stl',
                              {'generated': cloud_source(item),
                               'structure': structure,
                               'repair': item.get('repair_method', 'auto')}, stop.is_set)
        quality = diagnostic['quality']
        accepted = quality['status'] in ('repaired', 'passed')
        if accepted and not (folder / 'model.stl').is_file():
            raise ValueError('模型检查缺少制造网格，请重试。')
        source_report = {**item['source_report'], 'quality': quality}
        assets = source_assets(item)
        assets['original_model'] = self.url(pid, root / 'source/model.stl')
        assets['model'] = self.url(pid, folder / 'model.stl') if accepted else assets['original_model']
        params = apply_structure(item['params'], structure, initialize=item['params'].get('structure') != structure)
        if source_report.get('pose_structure', 'full_shell') != structure:
            previous = source_report.get('defaults', {})
            recommended = suggested_pose(source_report['dimensions'], measurements(params), structure)
            for key, value in recommended.items():
                if params.get(key) == previous.get(key):
                    params[key] = value
            source_report.update(defaults=recommended, pose_structure=structure)
        # The checked surface is now the fit input. A prior operation on the
        # former raw mesh must not be replayed against it automatically.
        params['rebuild_outer'] = False
        step('ready' if accepted else 'review', quality['message'], source_ready=True,
             wear_structure=structure,
             surface_version=version, source_report=source_report, report=source_report, params=params,
             assets={**assets, 'plates': []}, history=archive_current(item))

    def _fit(self, item, step, stop):
        pid = item['id']
        dest = self.folder(pid) / ('fit-%03d' % item['revision'])
        assets = source_assets(item)
        assets['plates'] = []
        step('fitting', '正在生成内腔、眼窗和试戴截面', assets=assets)
        # A pre-upgrade job may already be in flight. Resume its original input.
        surface = f"surface-{item['surface_version']:03d}" if item.get('surface_version') else 'source'
        report = self.geometry('fit', dest, self.folder(pid) / surface / 'model.stl', item['params'], stop.is_set)
        report['source_quality'] = item.get('source_report', {}).get('quality')
        for key in ('head', 'cutter', 'fitted', 'ring'):
            if (dest / (key + '.stl')).is_file():
                assets[key] = self.url(pid, dest / (key + '.stl'))
        trial_name = '开放试戴带' if report.get('ring_kind') == 'open_band' else '试戴环'
        step('slicing', '正在验证' + trial_name, report=report, assets=assets)
        if report.get('ring_valid') and (dest / 'ring.stl').is_file():
            ring, stats = self.slice_plate(dest / 'ring.stl', dest / 'trial', False, stop.is_set)
            assets['ring3mf'] = self.url(pid, ring)
            report['ring_slice'] = stats
        if report.get('blocking'):
            step('failed', '请调整模型：' + '；'.join(map(str, report['blocking'])), assets=assets, report=report)
            return
        parts = report.get('parts', [])
        if not parts:
            raise ValueError('未生成有效的整盔零件。')
        plates = []
        self.store.update(pid, report=report, assets=assets)
        for index, filename in enumerate(parts, 1):
            if Path(filename).name != filename:
                raise ValueError('零件路径无效。')
            step('slicing', f'正在切片第 {index}/{len(parts)} 盘')
            plate, stats = self.slice_plate(dest / filename, dest / ('p%02d' % index), True, stop.is_set)
            plates.append(dict(name=f'第 {index} 盘 · {Path(filename).stem}', url=self.url(pid, plate), **stats))
        assets['plates'] = plates
        package = dest / 'HelmetFit-P1S.zip'
        with zipfile.ZipFile(package, 'w', allowZip64=True) as out:
            manifest = []
            for i, plate in enumerate(plates, 1):
                name = f'P1S-plate-{i:02d}.3mf'
                out.write(dest / ('p%02d' % i) / 'plate.3mf', name)
                manifest.append({**{k: v for k, v in plate.items() if k != 'url'}, 'file': name})
            if assets.get('ring3mf'):
                trial_file = 'P1S-trial-band.3mf' if report.get('ring_kind') == 'open_band' else 'P1S-trial-ring.3mf'
                out.write(dest / 'trial/plate.3mf', trial_file)
            out.writestr('project.json', json.dumps(dict(params=item['params'], report=report, plates=manifest),
                                                     ensure_ascii=False, indent=2))
            out.writestr('装配说明.txt', f'先打印{trial_name}确认松紧，再按盘号逐盘打印。对照工作台整体预览试拼后粘合。\n' +
                        '\n'.join(report.get('assembly', [])) + '\n配置：P1S / 0.4 mm / PLA / 纹理 PEI 板。')
            out.writestr('制作记录.html', project_report({**item, 'report': report, 'assets': assets,
                                                   'message': f'P1S 切片通过，共 {len(plates)} 盘。'}))
        assets['package'] = self.url(pid, package)
        step('done', f'P1S 切片通过，共 {len(plates)} 盘。', report=report, assets=assets)

    def cutter(self, pid, data):
        item = self.store.get(pid)
        if item.get('preview_approved') is False:
            raise ValueError('确认外观后即可调整佩戴。')
        if not item['source_ready']:
            raise ValueError('请先完成外形准备。')
        params = fit_params(data)
        shape = {k: v for k, v in params.items() if k in ('width', 'length', 'height', 'clearance', 'circumference', 'dz')}
        digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:20]
        folder = self.folder(pid) / 'preview' / digest
        with self.preview_lock:
            self.store.get(pid)  # A queued preview must not recreate a deleted work directory.
            if not (folder / 'cutter.stl').is_file() or not (folder / 'head.stl').is_file():
                self.geometry('cutter', folder, params=params)
        dim = item['source_report']['dimensions']
        return dict(head=self.url(pid, folder / 'head.stl'), cutter=self.url(pid, folder / 'cutter.stl'),
                    suggested_pose=suggested_pose(dim, measurements(params), wear_structure(item)),
                    saved_suggested_pose=suggested_pose(dim, measurements(item['params']), wear_structure(item)))

    def generation_options(self, item):
        return {'resume_id': item.get('remote_id')}

    def geometry(self, *args, **kwargs):
        return geometry(*args, **kwargs)

    def slice_plate(self, *args, **kwargs):
        return slice_plate(*args, **kwargs)
