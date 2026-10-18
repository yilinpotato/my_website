"""完整漫画 ZIP 缓存；与网页的部分章节预览分开保存。"""
import re
import os
import time
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from threading import Lock
from contextlib import contextmanager
from filelock import FileLock, Timeout
try:
    from .manga_cache import MangaCache
except ImportError:
    from manga_cache import MangaCache

_lock = Lock()


@contextmanager
def archive_lock(root, album_id):
    # 同一本只下载一次；跨线程、跨 gunicorn worker 均互斥。
    with FileLock(root / f".{album_id}.lock", timeout=300):
        yield


@contextmanager
def download_capacity(root):
    count = max(1, min(4, int(os.getenv("JM_ARCHIVE_CONCURRENCY", "2"))))
    deadline = time.monotonic() + 300
    while True:
        for index in range(count):
            slot = FileLock(root / f".slot-{index}.lock")
            try:
                slot.acquire(timeout=0)
            except Timeout:
                continue
            try:
                yield
            finally:
                slot.release()
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("下载容量已满，等待超过 300 秒，请重试")
        time.sleep(0.1)


def parse_album_id(content):
    content = re.sub(r"<@!?\d+>", "", content).strip()
    match = re.fullmatch(r"(?:(?:/jm|jm|漫画)\s*)?([0-9]{1,12})", content, re.I)
    return match.group(1) if match else None


def build_archive(album_id, root):
    if not re.fullmatch(r"[0-9]{1,12}", album_id):
        raise ValueError("漫画号格式错误")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f"{album_id}.zip"
    cache = MangaCache(root.parent)
    with archive_lock(root, album_id):
        if archive.is_file():
            try:
                materialize_images(album_id, root, cache)
            except Exception:
                cache.discard_locked(album_id)
                raise
            cache.touch(album_id)
            return archive
        import jmcomic

        with download_capacity(root), tempfile.TemporaryDirectory(prefix=f"{album_id}-", dir=root) as tmp:
            option = jmcomic.JmOption.construct({
                "dir_rule": {"base_dir": tmp, "rule": "Bd_Aid_Pindex"},
                "download": {"ignore_photo_error": False,
                             "threading": {"photo": 1, "image": 2}},
                "client": {"retry_times": 2,
                           "postman": {"meta_data": {"timeout": 30}}},
            })
            album = option.new_jm_client().get_album_detail(album_id)
            if len(album) > 10:
                raise ValueError("章节超过 10 章，暂不支持整本打包")
            option.download_album(album_id)
            images = sorted(p for p in Path(tmp).rglob("*")
                            if p.is_file() and p.suffix.lower() in
                            {".jpg", ".jpeg", ".png", ".webp", ".gif"})
            if not images:
                raise ValueError("未下载到图片，请检查漫画号")
            staged = Path(tmp) / "archive.zip"
            with zipfile.ZipFile(staged, "w", zipfile.ZIP_STORED) as output:
                for path in images:
                    output.write(path, path.relative_to(tmp))
            size = staged.stat().st_size + sum(p.stat().st_size for p in images)
            if size > cache.limit:
                raise ValueError('单本漫画图片和 ZIP 超过缓存容量上限')
            staged.replace(archive)
            metadata = {'complete': True, 'title': getattr(album, 'title', f'JM{album_id}'),
                        'chapters': len(album)}
            (root / f'{album_id}.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
            try:
                materialize_images(album_id, root, cache)
                cache.register(album_id)
                cache.trim(protect=album_id)
            except Exception:
                cache.discard_locked(album_id)
                raise
    return archive


def materialize_images(album_id, root, cache=None):
    """从完整 ZIP 恢复网页图片；调用者持有同本锁。"""
    cache = cache or MangaCache(Path(root).parent)
    target = cache.root / album_id
    if (target / '.complete').exists():
        return
    archive = Path(root) / f'{album_id}.zip'
    with zipfile.ZipFile(archive) as source, tempfile.TemporaryDirectory(dir=root) as tmp:
        members = [item for item in source.infolist() if not item.is_dir()]
        if archive.stat().st_size + sum(item.file_size for item in members) > cache.limit:
            raise ValueError('单本漫画图片和 ZIP 超过缓存容量上限')
        for item in members:
            relative = Path(item.filename)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != album_id:
                raise ValueError('ZIP 内部路径无效')
            dest = Path(tmp) / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as inp, dest.open('wb') as out:
                shutil.copyfileobj(inp, out)
        extracted = Path(tmp) / album_id
        if not extracted.is_dir():
            raise ValueError('ZIP 中没有漫画图片')
        (extracted / '.complete').write_text('complete', encoding='ascii')
        if target.exists():
            cache.safe_path(album_id)
            shutil.rmtree(target)
        extracted.replace(target)
    cache.register(album_id)
    cache.trim(protect=album_id)
