"""网页/机器人共用的磁盘 LRU。图片与 ZIP 合计限额，SQLite 跨进程访问记录。"""
import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from filelock import FileLock, Timeout

DEFAULT_LIMIT = 10 * 1024 ** 3


class MangaCache:
    def __init__(self, root, limit=None):
        self.root = Path(root).resolve()
        self.archives = self.root / '.archives'
        self.archives.mkdir(parents=True, exist_ok=True)
        self.limit = int(limit if limit is not None else os.getenv('JM_CACHE_BYTES', DEFAULT_LIMIT))
        self.db = self.archives / 'cache.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, paths TEXT, size INTEGER, accessed REAL)')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.db, timeout=15)) as db, db:
            yield db

    def lock(self, album_id):
        return FileLock(self.archives / f'.{album_id}.lock', timeout=300)

    def touch(self, album_id):
        with self.connect() as db:
            db.execute('UPDATE cache SET accessed=? WHERE id=?', (time.time(), album_id))

    @contextmanager
    def pin(self, album_id):
        if os.name == 'posix':
            import fcntl
            with (self.archives / f'.{album_id}.lock').open('a+b') as handle:
                fcntl.flock(handle, fcntl.LOCK_SH)
                try:
                    self.touch(album_id)
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        else:
            with self.lock(album_id):
                self.touch(album_id)
                yield

    def safe_path(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or path.resolve() == self.root:
            raise ValueError('缓存路径越界')
        return path

    def register(self, album_id, paths=None):
        if paths is None:
            paths = [album_id, f'.archives/{album_id}.zip', f'.archives/{album_id}.json']
            with self.connect() as db:
                row = db.execute('SELECT paths FROM cache WHERE id=?',(album_id,)).fetchone()
            if row:
                paths = list(dict.fromkeys(paths + json.loads(row[0])))
        existing, size = [], 0
        for relative in paths:
            path = self.safe_path(relative)
            if not path.exists():
                continue
            existing.append(relative)
            size += path.stat().st_size if path.is_file() else sum(
                p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)',
                       (album_id, json.dumps(existing), size, time.time()))
        return size

    def discard_locked(self, album_id):
        """发布失败回滚；调用者持有同本排他锁。"""
        with self.connect() as db:
            row = db.execute('SELECT paths FROM cache WHERE id=?',(album_id,)).fetchone()
            paths = json.loads(row[0]) if row else [album_id,f'.archives/{album_id}.zip',f'.archives/{album_id}.json']
            for relative in paths:
                path = self.safe_path(relative)
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            db.execute('DELETE FROM cache WHERE id=?',(album_id,))

    def import_existing(self):
        """只索引下载根目录内已有内容，不把旧网页的部分缓存标记为完整。"""
        with FileLock(self.archives / '.catalog.lock'):
            with self.connect() as db:
                known = {r[0] for r in db.execute('SELECT id FROM cache')}
            groups = {}
            for path in self.root.iterdir():
                if not path.is_dir() or path.name.startswith('.') or path.is_symlink():
                    continue
                match = re.match(r'^([0-9]{1,12})(?:_|$)', path.name)
                key = match.group(1) if match else 'legacy-' + __import__('hashlib').sha256(path.name.encode()).hexdigest()[:16]
                groups.setdefault(key, []).append(path.name)
            for path in self.archives.glob('*.zip'):
                if re.fullmatch(r'[0-9]{1,12}', path.stem):
                    groups.setdefault(path.stem, []).append(str(path.relative_to(self.root)))
            for key, paths in groups.items():
                if key not in known:
                    self.register(key, paths)
        self.trim()

    def trim(self, protect=None):
        with FileLock(self.archives / '.catalog.lock'):
            with self.connect() as db:
                rows = list(db.execute('SELECT id,paths,size FROM cache ORDER BY accessed'))
                total = sum(row[2] for row in rows)
                for album_id, paths_json, size in rows:
                    if total <= self.limit:
                        break
                    if album_id == protect:
                        continue
                    lock = self.lock(album_id)
                    try:
                        lock.acquire(timeout=0)
                    except Timeout:
                        continue
                    try:
                        for relative in json.loads(paths_json):
                            path = self.safe_path(relative)
                            if path.is_dir():
                                shutil.rmtree(path)
                            elif path.exists():
                                path.unlink()
                        db.execute('DELETE FROM cache WHERE id=?', (album_id,))
                        total -= size
                    finally:
                        lock.release()
            if total > self.limit:
                raise RuntimeError('缓存容量不足：正在使用的内容不能淘汰，请稍后重试')
        return total

    def stats(self):
        with self.connect() as db:
            count, total = db.execute('SELECT count(*),coalesce(sum(size),0) FROM cache').fetchone()
        return {'entries': count, 'bytes': total, 'limit_bytes': self.limit}
