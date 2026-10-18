import tempfile
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from unittest.mock import patch
from types import SimpleNamespace
from flask import Flask

from qq_bot.manga_cache import MangaCache
from qq_bot.archives import build_archive, download_capacity
from qq_bot.web_cache import install_web_cache
from qq_bot.qq_errors import QQAPIError


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = MangaCache(self.root, limit=100)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, album_id, size):
        path = self.root / album_id
        path.mkdir()
        (path / 'image.jpg').write_bytes(b'x'*size)
        self.cache.register(album_id)

    def test_lru_touch_and_combined_accounting(self):
        self.add('1',40)
        self.add('2',40)
        self.cache.touch('1')
        (self.cache.archives/'3.zip').write_bytes(b'x'*40)
        self.cache.register('3')
        self.cache.trim(protect='3')
        self.assertTrue((self.root/'1').exists())
        self.assertFalse((self.root/'2').exists())
        self.assertEqual(self.cache.stats()['bytes'],80)

    def test_active_reader_is_not_evicted(self):
        self.add('1',60)
        self.add('2',60)
        with self.cache.pin('1'):
            with self.assertRaises(RuntimeError):
                self.cache.trim(protect='2')
            self.assertTrue((self.root/'1').exists())
        self.cache.trim(protect='2')
        self.assertFalse((self.root/'1').exists())

    def test_outside_path_rejected(self):
        with self.assertRaises(ValueError):
            self.cache.register('1',['../outside'])

    def test_concurrent_same_album_downloads_once(self):
        calls=[]
        def construct(config):
            base=Path(config['dir_rule']['base_dir'])
            def download(album_id):
                calls.append(album_id)
                time.sleep(.05)
                folder=base/album_id/'1'
                folder.mkdir(parents=True)
                (folder/'001.jpg').write_bytes(b'synthetic-image')
            return SimpleNamespace(new_jm_client=lambda:SimpleNamespace(get_album_detail=lambda _: [1]), download_album=download)
        with patch('jmcomic.JmOption.construct',side_effect=construct):
            with ThreadPoolExecutor(max_workers=6) as pool:
                paths=list(pool.map(lambda _:build_archive('123',self.cache.archives),range(6)))
        self.assertEqual(calls,['123'])
        self.assertEqual(len(set(paths)),1)
        self.assertTrue((self.root/'123'/'1'/'001.jpg').exists())
        with zipfile.ZipFile(paths[0]) as archive:
            self.assertIsNone(archive.testzip())

    def test_global_capacity_two(self):
        counts={'active':0,'max':0}
        lock=Lock()
        def task(_):
            with download_capacity(self.cache.archives):
                with lock:
                    counts['active']+=1
                    counts['max']=max(counts['max'],counts['active'])
                time.sleep(.08)
                with lock: counts['active']-=1
        with patch.dict('os.environ',{'JM_ARCHIVE_CONCURRENCY':'2'}):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(task,range(6)))
        self.assertEqual(counts['max'],2)

    def test_web_and_bot_share_archive_without_network(self):
        with zipfile.ZipFile(self.cache.archives/'123.zip','w') as archive:
            archive.writestr('123/1/001.jpg',b'synthetic-image')
        app=Flask(__name__)
        app.add_url_rule('/images/<album_id>/<chapter>/<path:filename>', 'serve_image',lambda **_:None)
        app.add_url_rule('/api/download/<album_id>', 'api_download',lambda **_:None,methods=['POST'])
        statuses={}
        def chapters(album_id,*args):
            return [{'key':'1','name':'1','images':list((self.root/album_id/'1').glob('*.jpg'))}]
        namespace={'app':app,'DOWNLOAD_DIR':self.root,'get_album_chapters':chapters,
                   'get_album_detail_info':lambda _:('test',[]),
                   'get_download_status':lambda a:statuses.get(a,{}),
                   'set_download_status':lambda a,s:statuses.update({a:s})}
        install_web_cache(namespace)
        with patch('jmcomic.JmOption.construct',side_effect=AssertionError('must use cache')):
            result=namespace['download_jm_album']('123')
            self.assertTrue(result['success'])
            self.assertTrue(result['cached'])
        response=app.test_client().get('/images/123/1/001.jpg')
        self.assertEqual(response.data,b'synthetic-image')
        response.close()
        self.assertEqual(MangaCache(self.root).stats()['entries'],1)

    def test_api_error_details_without_credentials(self):
        exc=QQAPIError('/v2/groups/PRIVATEGROUP/messages',400,
                       {'code':40093001,'message':'upload failed token=secret-value'},
                       {'X-Tps-trace-ID':'trace-123'},('secret-value',))
        message=str(exc)
        self.assertIn('40093001',message)
        self.assertIn('trace-123',message)
        self.assertNotIn('PRIVATEGROUP',message)
        self.assertNotIn('secret-value',message)


if __name__=='__main__':
    unittest.main()
