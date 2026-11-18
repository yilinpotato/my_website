import tempfile
import unittest
from unittest.mock import Mock,patch

from qq_bot.qq_memes import QQMemeStore,qq_image_url


class QQMemeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=QQMemeStore(self.temp.name)
        self.label=Mock(return_value={'is_meme':True,'safe':True,'description':'猫猫无语，翻白眼','tags':['猫','无语']})

    def tearDown(self):
        self.temp.cleanup()

    def add(self,url='https://gchat.qpic.cn/test-cat',source='qq_group',group='1'):
        with patch('qq_bot.qq_memes.download_qq_image',return_value=(b'original-qq-gif',b'jpeg-preview')):
            return self.store.ingest(url,source,group,'actor',self.label)

    def test_no_stock_or_arbitrary_download_sources(self):
        self.assertTrue(qq_image_url('https://multimedia.nt.qq.com.cn/download?fileid=abc'))
        for url in ['https://qpic.cn.evil.example/a','https://127.0.0.1/a','file:///tmp/a','https://user:pass@gchat.qpic.cn/a']:
            self.assertFalse(qq_image_url(url))

    def test_visual_label_cache_and_dedup(self):
        self.assertTrue(self.add())
        self.assertFalse(self.add())
        self.label.assert_called_once()
        self.assertEqual(len(self.store.candidates('1')),1)
        self.assertEqual(self.store.candidates('2'),[])

    def test_selector_cannot_invent_or_cross_group(self):
        self.add()
        choose=Mock(return_value={'match':True,'id':'invented'})
        self.assertIsNone(self.store.choose('猫猫无语','actor','1',choose))
        choose.return_value={'match':True,'id':self.store.candidates('1')[0]['id']}
        self.assertIsNone(self.store.choose('猫猫无语','actor','2',choose))
        self.assertEqual(self.store.choose('猫猫无语','actor','1',choose),b'original-qq-gif')
        choose.return_value={'match':False,'id':self.store.candidates('1')[0]['id']}
        self.assertIsNone(self.store.choose('狗','actor','1',choose))

    def test_favorites_available_but_nonmemes_rejected(self):
        self.add(source='qq_favorite',group='')
        self.assertEqual(len(self.store.candidates('anygroup')),1)
        self.label.return_value={'is_meme':False,'safe':True,'description':'日常照片'}
        self.assertFalse(self.add('https://gchat.qpic.cn/ordinary-photo'))
        self.assertEqual(len(self.store.candidates('1')),1)

    def test_rotating_rkey_does_not_reindex_same_picture(self):
        a='https://multimedia.nt.qq.com.cn/download?fileid=abc&rkey=old'
        b='https://multimedia.nt.qq.com.cn/download?rkey=new&fileid=abc'
        c='https://multimedia.nt.qq.com.cn/download?fileid=other&rkey=new'
        self.assertEqual(self.store.url_key(a),self.store.url_key(b))
        self.assertNotEqual(self.store.url_key(a),self.store.url_key(c))

    def test_explicit_material_sources_and_popularity(self):
        self.add(group='source')
        self.assertEqual(self.store.candidates('reply'),[])
        self.assertEqual(len(self.store.candidates('reply',['source'])),1)
        self.add('https://gchat.qpic.cn/popular',group='source')
        self.store.note_usage('https://gchat.qpic.cn/popular',4)
        self.assertEqual(self.store.candidates('reply',['source'])[0]['popularity'],4)


if __name__=='__main__':unittest.main()
