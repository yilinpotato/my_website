"""把既有漫画查看器接到统一缓存，保留页面与密码保护。"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from pathlib import Path
from flask import jsonify, request, send_from_directory
from werkzeug.wsgi import ClosingIterator
try:
    from .archives import build_archive
    from .manga_cache import MangaCache
except ImportError:
    from archives import build_archive
    from manga_cache import MangaCache


def install_web_cache(namespace):
    app = namespace['app']
    root = Path(namespace['DOWNLOAD_DIR'])
    cache = MangaCache(root)
    cache.import_existing()
    old_detail = namespace['get_album_detail_info']
    old_status = namespace['get_download_status']
    set_status = namespace['set_download_status']
    workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='web-comic')
    slots = BoundedSemaphore(16)

    def valid(album_id):
        if not re.fullmatch(r'[0-9]{1,12}', str(album_id)):
            raise ValueError('漫画号格式错误')

    def chapters(album_id, title_hint=None, time_hint=None):
        valid(album_id)
        if (cache.archives / f'{album_id}.zip').exists() and not (root / album_id / '.complete').exists():
            build_archive(album_id, cache.archives)
        with cache.pin(album_id):
            album_root=root / album_id
            if not album_root.is_dir():
                return []
            extensions={'.jpg','.jpeg','.png','.webp','.gif'}
            def images(directory):
                return sorted((p for p in directory.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in extensions),
                              key=lambda p:(len(p.stem),p.stem))
            folders=sorted((p for p in album_root.iterdir() if p.is_dir() and not p.is_symlink()), key=lambda p:(len(p.name),p.name))
            result=[{'name':p.name,'key':p.name,'images':images(p)} for p in folders]
            result=[ch for ch in result if ch['images']]
            if result:
                return result
            found=images(album_root)
            return [{'name':'第1章','key':'__root__','images':found}] if found else []

    def detail(album_id):
        valid(album_id)
        metadata = cache.archives / f'{album_id}.json'
        with cache.pin(album_id):
            if metadata.exists():
                data = json.loads(metadata.read_text(encoding='utf-8'))
                return data.get('title', f'JM{album_id}'), [(str(i), str(i), str(i)) for i in range(1, data.get('chapters', 1)+1)]
        if (root / album_id / '.complete').exists():
            found = chapters(album_id)
            return f'JM{album_id}', [(ch['key'],ch['name'],ch['name']) for ch in found]
        return old_detail(album_id)

    def download(album_id, limit=5, offset=0, update_status=False):
        try:
            valid(album_id)
            cached = (cache.archives / f'{album_id}.zip').exists()
            set_status(album_id, {'state':'downloading','progress':10,'message':'读取统一缓存 / 准备整本漫画'})
            build_archive(album_id, cache.archives)
            found = chapters(album_id)
            images = [str(p) for ch in found for p in ch['images']]
            title, episodes = detail(album_id)
            set_status(album_id, {'state':'done','progress':100,'message':'已缓存，可直接访问'})
            return {'success':True,'cached':cached,'images':images[:3], 'all_images':images,
                    'title':title,'album_id':album_id,'total_chapters':len(episodes)}
        except Exception as exc:
            set_status(album_id, {'state':'error','progress':0,'message':str(exc)})
            return {'success':False,'error':str(exc)}

    def status(album_id):
        valid(album_id)
        if (root / album_id / '.complete').is_file():
            return {'state':'done','progress':100,'message':'已缓存，可直接访问'}
        # Read shared state before a worker's potentially stale in-memory copy.
        try:
            value = json.loads((root / '.status' / f'{album_id}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = old_status(album_id)
        if value.get('state') == 'done' and not (root / album_id).exists():
            return {'state':'idle','progress':0,'message':'缓存已淘汰，请重新下载'}
        return value

    def start_download(album_id):
        try:
            valid(album_id)
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 400
        if (root / album_id / '.complete').exists():
            cache.touch(album_id)
            return jsonify(success=True,state='done',progress=100,message='命中统一缓存')
        if not slots.acquire(blocking=False):
            return jsonify(success=False,error='下载队列已满，请稍后重试'),503
        set_status(album_id, {'state':'queued','progress':5,'message':'进入共享下载队列'})
        def task():
            try:
                download(album_id)
            finally:
                slots.release()
        workers.submit(task)
        return jsonify(success=True, **status(album_id))

    def image(album_id, filename, chapter=None):
        try:
            valid(album_id)
        except ValueError:
            return '漫画号无效',400
        lease = cache.pin(album_id)
        lease.__enter__()
        try:
            directory = root / album_id
            if chapter and chapter != '__root__':
                directory /= chapter
            if not directory.resolve().is_relative_to((root / album_id).resolve()):
                lease.__exit__(None,None,None)
                return '路径无效',400
            mime = {'.webp':'image/webp', '.gif':'image/gif', '.jpg':'image/jpeg',
                    '.jpeg':'image/jpeg', '.png':'image/png'}.get(Path(filename).suffix.lower())
            response = send_from_directory(directory,filename,mimetype=mime)
            response.response = ClosingIterator(response.response, lambda: lease.__exit__(None,None,None))
            return response
        except BaseException:
            lease.__exit__(None,None,None)
            raise

    namespace['get_album_chapters'] = chapters
    namespace['get_album_detail_info'] = detail
    namespace['download_album_chapters'] = download
    namespace['download_jm_album'] = lambda album_id, max_images=3: download(album_id)
    namespace['get_download_status'] = status
    app.view_functions['api_download'] = start_download
    app.view_functions['api_download_more'] = start_download
    app.view_functions['serve_image'] = image
    app.view_functions['serve_image_legacy'] = image

    @app.get('/api/cache')
    def manga_cache_stats():
        return jsonify(cache.stats())

    @app.get('/api/diagnostics/<request_id>')
    def manga_request_diagnostics(request_id):
        if not re.fullmatch(r'[a-f0-9]{12}',request_id):
            return jsonify(error='请求编号无效'),400
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(cache.archives/'jobs.sqlite3')) as db:
            row=db.execute('SELECT event,stage,detail,updated FROM diagnostics WHERE request_id=?',(request_id,)).fetchone()
        if row is None:
            return jsonify(error='未找到该请求'),404
        return jsonify(request_id=request_id,event=row[0],stage=row[1],detail=row[2],updated=row[3])
    return cache
