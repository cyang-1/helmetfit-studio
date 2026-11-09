"""Persistent jobs, with short transactions and exactly one record per upload."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / 'projects.sqlite3'
        with closing(self.connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, '
                       'request_id TEXT UNIQUE, updated REAL NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deleted_projects (id TEXT PRIMARY KEY, '
                       'request_id TEXT UNIQUE, cleanup_pending INTEGER NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.database, timeout=30)

    def create(self, record, request_id):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT body FROM projects WHERE request_id=?', (request_id,)).fetchone()
            if old:
                return json.loads(old[0]), False
            if db.execute('SELECT 1 FROM deleted_projects WHERE request_id=?', (request_id,)).fetchone():
                raise ValueError('这次提交的作品已删除。请重新提交新的生成请求。')
            record['updated'] = time.time()
            db.execute('INSERT INTO projects VALUES (?, ?, ?, ?)',
                       (record['id'], request_id, record['updated'], json.dumps(record)))
            return record, True

    def get(self, project_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT body FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise KeyError('项目不存在')
        return json.loads(row[0])

    def by_request(self, request_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT body FROM projects WHERE request_id=?', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM projects ORDER BY updated DESC')]

    def delete(self, pid):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request_id FROM projects WHERE id=?', (pid,)).fetchone()
            if row:
                db.execute('INSERT INTO deleted_projects VALUES (?, ?, 1)', (pid, row[0]))
                db.execute('DELETE FROM projects WHERE id=?', (pid,))
            elif not db.execute('SELECT 1 FROM deleted_projects WHERE id=?', (pid,)).fetchone():
                raise KeyError('作品不存在')

    def pending_deletions(self):
        with closing(self.connect()) as db:
            return [row[0] for row in db.execute('SELECT id FROM deleted_projects WHERE cleanup_pending=1')]

    def finish_deletion(self, pid):
        with closing(self.connect()) as db, db:
            db.execute('UPDATE deleted_projects SET cleanup_pending=0 WHERE id=?', (pid,))

    def update(self, project_id, **values):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM projects WHERE id=?', (project_id,)).fetchone()
            if not row:
                raise KeyError('项目不存在')
            item = json.loads(row[0])
            item.update(values, updated=time.time())
            db.execute('UPDATE projects SET body=?, updated=? WHERE id=?',
                       (json.dumps(item), item['updated'], project_id))
            return item
