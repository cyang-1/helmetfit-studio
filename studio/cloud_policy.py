"""Storage, spend and execution limits shared by a single hosted Studio process."""
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .jobs import Busy, Jobs, cloud_source

SESSION_ID = re.compile(r'[a-f0-9]{48}')
SESSION_SECONDS = 30 * 86400


class Limit(Busy):
    status_code = 429


class Unavailable(Busy):
    status_code = 503


def setting(name, default):
    value = int(os.environ.get('HELMETFIT_' + name, default))
    if value < 0:
        raise ValueError('HELMETFIT_' + name + ' 必须为非负整数')
    return value


class Ledger:
    """Count provider create attempts at point of submission, including ambiguous outcomes."""

    def __init__(self, root):
        self.path = root / 'cloud.sqlite3'
        self.global_daily = setting('DAILY_GENERATIONS', 12)
        self.session_daily = setting('SESSION_DAILY_GENERATIONS', 3)
        self.global_compute = setting('DAILY_COMPUTE_JOBS', 480)
        self.session_compute = setting('SESSION_DAILY_COMPUTE_JOBS', 80)
        with closing(self.connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO config VALUES (?,?)', ('signing-key', secrets.token_bytes(32)))
            self.secret = db.execute("SELECT value FROM config WHERE name='signing-key'").fetchone()[0]
            db.execute('CREATE TABLE IF NOT EXISTS usage (day TEXT, owner TEXT, kind TEXT, count INTEGER NOT NULL, '
                       'PRIMARY KEY (day, owner, kind))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def cookie(self, sid, expiry=None):
        payload = sid + '.' + str(expiry if expiry is not None else int(time.time()) + SESSION_SECONDS)
        signature = hmac.new(self.secret, payload.encode('ascii'), hashlib.sha256).hexdigest()
        return payload + '.' + signature

    def verify(self, value):
        if not re.fullmatch(r'[a-f0-9]{48}\.[0-9]{1,12}\.[a-f0-9]{64}', value):
            return None
        sid, expiry, _signature = value.split('.')
        if int(expiry) <= time.time():
            return None
        return sid if hmac.compare_digest(self.cookie(sid, int(expiry)), value) else None

    def quota(self, owner, kind, *, consume):
        day = datetime.now(timezone.utc).date().isoformat()
        paid = kind == 'provider'
        personal = self.session_daily if paid else self.session_compute
        site = self.global_daily if paid else self.global_compute
        with closing(self.connect()) as db, db:
            if consume:
                db.execute('BEGIN IMMEDIATE')
            used, total = db.execute(
                'SELECT COALESCE(SUM(CASE WHEN day=? AND owner=? THEN count ELSE 0 END),0), '
                'COALESCE(SUM(CASE WHEN day=? THEN count ELSE 0 END),0) '
                'FROM usage WHERE kind=?', (day, owner, day, kind)).fetchone()
            if used >= personal or total >= site:
                label = 'Lux3D 生成' if paid else '模型加工'
                raise Limit(f'本会话或本站今日的{label}额度已用完。额度于 UTC 零点重新计算。')
            if consume:
                db.execute('INSERT INTO usage VALUES (?,?,?,1) ON CONFLICT(day,owner,kind) '
                           'DO UPDATE SET count=count+1', (day, owner, kind))


class CloudJobs(Jobs):
    def __init__(self, workspaces, root):
        self.host = workspaces
        self.owner = root.name
        super().__init__(root, executor=workspaces.executor)

    def generation_options(self, item):
        return {**super().generation_options(item),
                'before_create': lambda: self.host.ledger.quota(self.owner, 'provider', consume=True)}

    def create(self, folder, request_id, name, inputs, source_type, **kwargs):
        # Use the same lock order as Jobs.action -> _regenerate -> create.
        with self.host.admission, self.lock:
            try:
                if not self.store.by_request(request_id):
                    if len(self.store.all()) >= self.host.max_projects:
                        raise Limit('本工作区作品数量已达上限，请先删除不再需要的作品。')
                    self.host.admit(self, check_paid=source_type in ('text', 'images'))
            except Exception:
                # Folder belongs to this not-yet-created upload.
                if folder.parent == self.store.root and re.fullmatch(r'[a-f0-9]{32}', folder.name):
                    shutil.rmtree(folder, ignore_errors=True)
                raise
            return super().create(folder, request_id, name, inputs, source_type, **kwargs)

    def action(self, pid, action, data):
        with self.host.admission, self.lock:
            item = self.store.get(pid)
            if action == 'retry' and data.get('remote_id'):
                known = self._cloud_record(pid).get('taskId') or item.get('remote_id')
                if not known or str(known) != str(data['remote_id']):
                    raise ValueError('此云工作区不能认领未经核实的远端任务，请联系部署者核对记录。')
            replay_approval = action == 'approve' and item.get('preview_approved') and cloud_source(item)
            if action not in ('cancel', 'regenerate') and not replay_approval:
                self.host.admit(self, check_paid=False)
            return super().action(pid, action, data)

    def geometry(self, mode, *args, **kwargs):
        if mode == 'cutter':
            if not self.host.compute.acquire(timeout=2):
                raise Unavailable('正在处理其他模型，请稍后再预览。')
            try:
                self.host.ledger.quota(self.owner, 'compute', consume=True)
                return super().geometry(mode, *args, **kwargs)
            finally:
                self.host.compute.release()
        with self.host.compute:
            return super().geometry(mode, *args, **kwargs)

    def slice_plate(self, *args, **kwargs):
        with self.host.compute:
            return super().slice_plate(*args, **kwargs)


class Workspaces:
    """A separate Store and artifact tree per session; one global worker for all queues."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lockfile = self._lock_process()
        self.executor = ThreadPoolExecutor(1, thread_name_prefix='helmetfit-hosted')
        self.admission = threading.RLock()
        self.compute = threading.Lock()
        self.uploads = threading.BoundedSemaphore(2)
        self.instances = OrderedDict()
        self.readers = Counter()
        try:
            self.ledger = Ledger(self.root)
            self.tenants = self.root / 'workspaces'
            self.tenants.mkdir(mode=0o700, exist_ok=True)
            self.max_projects = setting('SESSION_PROJECTS', 20)
            self.max_sessions = setting('MAX_WORKSPACES', 256)
            self.min_free = setting('MIN_FREE_MB', 1024) * 1024**2
            # Existing jobs must resume on startup even if a browser has since closed.
            for folder in self.tenants.iterdir():
                if SESSION_ID.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink():
                    self.instances[folder.name] = CloudJobs(self, folder)
                    self.trim(self.max_sessions)
        except BaseException:
            self.close()
            raise

    def _lock_process(self):
        stream = (self.root / 'cloud.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return stream
        except OSError:
            stream.close()
            raise RuntimeError('此数据目录已有云服务运行，请使用单副本部署。') from None

    def pin(self, sid):
        with self.admission:
            self.readers[sid] += 1

    def unpin(self, sid):
        with self.admission:
            self.readers[sid] -= 1
            if not self.readers[sid]:
                del self.readers[sid]

    def trim(self, maximum):
        # Stores open SQLite connections only for individual transactions. Dropping
        # an idle manager reclaims Python state without deleting a visitor's files.
        for sid in list(self.instances):
            if len(self.instances) <= maximum:
                break
            if not self.readers[sid] and not self.instances[sid].running:
                del self.instances[sid]

    def space(self, sid, create=False):
        if not SESSION_ID.fullmatch(sid):
            raise KeyError('工作区不存在')
        with self.admission:
            if sid not in self.instances:
                folder = self.tenants / sid
                if folder.is_symlink():
                    raise KeyError('工作区不存在')
                existing = folder.is_dir()
                if not existing and not create:
                    return None
                self.trim(self.max_sessions - 1)
                if len(self.instances) >= self.max_sessions:
                    raise Limit('同时使用工作区的访客较多，请稍后重试。')
                if not existing:
                    self.require_space()
                    self.ledger.quota(sid, 'compute', consume=True)
                self.instances[sid] = CloudJobs(self, folder)
            self.instances.move_to_end(sid)
            return self.instances[sid]

    def require_space(self, incoming=0):
        if shutil.disk_usage(self.root).free < self.min_free + incoming * 2:
            raise Unavailable('本站暂时没有足够空间处理新任务。可先下载并删除不再需要的作品。')

    def admit(self, jobs, *, check_paid):
        if len(jobs.running) >= 2 or sum(len(group.running) for group in self.instances.values()) >= 8:
            raise Limit('工作区或本站等待任务较多，请待当前任务结束后重试。')
        self.require_space()
        if check_paid:
            self.ledger.quota(jobs.owner, 'provider', consume=False)
        self.ledger.quota(jobs.owner, 'compute', consume=True)

    def close(self):
        self.executor.shutdown(wait=True)
        self.lockfile.close()
