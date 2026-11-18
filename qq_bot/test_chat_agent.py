import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from qq_bot.chat_agent import ChatAgent, meme_image, safe_url, media_request, decode_plan
from qq_bot.onebot_bridge import Bridge, should_reply


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = ChatAgent(self.tmp.name, 'https://example.test/jm')

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_isolation_and_clear(self):
        with patch.object(self.agent, 'complete', return_value={'reply':'你好','action':'none'}) as complete:
            self.agent.answer('你好', 'group:a:user', 'u1')
            self.agent.answer('你记得什么', 'group:b:user', 'u2')
            self.assertEqual(len(complete.call_args.args[0]), 2)
            self.agent.answer('继续', 'group:a:user', 'u3')
            self.assertEqual(len(complete.call_args.args[0]), 4)
        self.agent.clear('group:a:user')
        with self.agent.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0], 2)

    def test_cooldown_prevents_model_call(self):
        with patch.object(self.agent, 'complete', return_value={'reply':'你好'}) as complete:
            self.agent.answer('hi', 'scope', 'user')
            result = self.agent.answer('again', 'scope', 'user')
            self.assertIn('8 秒', result['reply'])
            self.assertEqual(complete.call_count, 1)

    def test_search_then_answer_and_sources(self):
        sources = [{'title':'资料','url':'https://example.org/doc','summary':'真实数据'}]
        with patch('qq_bot.chat_agent.web_search', return_value=sources), patch.object(self.agent, 'complete', return_value={'reply':'查到了'}) as complete:
            result = self.agent.answer('搜索 今天的消息', 'scope', 'actor')
        self.assertEqual(complete.call_count, 1)
        token = result['reply'].rsplit('/',1)[-1]
        self.assertEqual(self.agent.load_sources(token), sources)
        self.assertEqual(self.agent.load_sources('../bad'), [])

    def test_tool_selection_and_image(self):
        with patch.object(self.agent, 'complete', return_value={'action':'image','query':'猫','reply':'给你'}), patch('qq_bot.chat_agent.image_search', return_value=(b'jpeg', {'title':'cat','url':'https://example.org'})):
            result = self.agent.answer('想看看猫', 'scope', 'u')
        self.assertEqual(result['image'], b'jpeg')

    def test_original_meme_and_url_validation(self):
        self.assertTrue(meme_image('sleep').startswith(b'\x89PNG'))
        self.assertFalse(safe_url('javascript:alert(1)'))
        self.assertFalse(safe_url('https://user:pass@example.org'))

    def test_explicit_meme_requires_qq_library_without_web_fallback(self):
        with patch.object(self.agent,'complete') as model, patch('qq_bot.chat_agent.image_search',side_effect=ValueError('none')):
            result=self.agent.answer('表情包 晚安','scope','user')
        model.assert_not_called()
        self.assertIsNone(result['image'])
        self.assertEqual(result['meme_query'],'晚安')

    def test_natural_meme_request_forces_image_without_model(self):
        with patch.object(self.agent,'complete') as model, patch('qq_bot.chat_agent.image_search',return_value=(b'jpeg',{})) as search:
            result=self.agent.answer('蓝色大肥鱼，发一张猫猫无语梗图','scope','user')
        model.assert_not_called()
        self.assertIsNone(result['image'])
        self.assertIn('猫猫无语',result['meme_query'])
        search.assert_not_called()

    def test_media_intent_does_not_trigger_on_other_peoples_pictures(self):
        for text in ['他发的梗图真好笑','大肥鱼，别发梗图，只回答','这张图片不错']:
            self.assertIsNone(media_request(text))
        for text in ['DeepSeek 来张震惊表情包','大肥鱼，给我发一张猫猫图片','搜图 可爱猫咪']:
            self.assertIsNotNone(media_request(text))

    def test_model_json_format_regression(self):
        self.assertEqual(decode_plan('{"reply":"第一行\n第二行"}')['reply'],'第一行\n第二行')
        self.assertTrue(decode_plan("{'match': True, 'id': 'abc'}")['match'])
        self.assertEqual(decode_plan('{"match":true,"id":"abc","extra": nope}')['id'],'abc')
        self.assertEqual(decode_plan('```json\n{"reply":"你好","action":"none"}\n```')['reply'],'你好')
        self.assertEqual(decode_plan('{"reply":"收到", "action": none}')['reply'],'收到')
        self.assertEqual(decode_plan('你好')['reply'],'你好')

    def test_direct_request_bypasses_actor_cooldown(self):
        with patch.object(self.agent,'complete',return_value={'reply':'回答'}):
            self.agent.answer('第一个问题','scope','user',priority=True)
            result=self.agent.answer('第二个问题','scope','user',priority=True)
        self.assertEqual(result['reply'],'回答')


class OneBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.tmp.name, 'https://example.test/jm', 'secret')
        self.event = {'post_type':'message','message_type':'group','self_id':100,'user_id':200,
            'group_id':300,'message_id':1,'sender':{'role':'admin'},
            'message':[{'type':'text','data':{'text':'启用哈基米'}}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_trigger_gate(self):
        self.assertFalse(should_reply(self.event, '这怎么做？'))
        self.assertFalse(should_reply(self.event, '今天吃了饭'))
        self.assertTrue(should_reply(dict(self.event,message=[{'type':'at','data':{'qq':'100'}}]), 'hello'))
        for name in ['蓝色大肥鱼','大肥鱼','DeepSeek','deepseek','DEEPSEEK']:
            self.assertTrue(should_reply(self.event,name+' 你怎么看？'))

    def test_casual_is_limited_but_named_requests_always_processed(self):
        async def run():
            import sqlite3
            from contextlib import closing
            from unittest.mock import Mock
            with closing(sqlite3.connect(self.bridge.db)) as db,db:
                db.execute('INSERT INTO groups VALUES (?,?,?)',('300',1,1))
            self.bridge.agent.answer=Mock(return_value={'reply':'回答'})
            self.bridge.send=AsyncMock()
            def event(mid,text):
                return dict(self.event,message_id=mid,message=[{'type':'text','data':{'text':text}}])
            with patch('qq_bot.onebot_bridge.random.random',return_value=0):
                await self.bridge.handle(event(1,'这个问题怎么解决？'))
                await self.bridge.handle(event(2,'另一个问题怎么解决？'))
                await self.bridge.handle(event(3,'大肥鱼 这个呢？'))
                await self.bridge.handle(event(4,'DeepSeek 还有这个？'))
            self.assertEqual(self.bridge.agent.answer.call_count,3)
            self.assertEqual(self.bridge.send.await_count,3)
            self.assertTrue(self.bridge.agent.answer.call_args.kwargs['priority'])
        asyncio.run(run())

    def test_enable_requires_admin_and_midnight_once(self):
        async def run():
            self.bridge.send = AsyncMock()
            event = dict(self.event, sender={'role':'member'})
            await self.bridge.handle(event)
            with self.bridge.agent.connect(): pass
            self.event['message_id'] = 2
            await self.bridge.handle(self.event)
            self.bridge.send.reset_mock()
            self.bridge.ws = SimpleNamespace(closed=False)
            now = datetime(2026,10,23,0,0,tzinfo=ZoneInfo('Asia/Shanghai'))
            await self.bridge.midnight(now)
            await self.bridge.midnight(now)
            self.bridge.send.assert_awaited_once()
            self.assertIn('晚安', self.bridge.send.call_args.args[1])
        asyncio.run(run())

    def test_disabled_group_and_self_messages_ignored(self):
        async def run():
            self.bridge.send=AsyncMock()
            event = dict(self.event,message=[{'type':'text','data':{'text':'你是谁？'}}])
            await self.bridge.handle(event)
            await self.bridge.handle(dict(event,user_id=100))
            self.bridge.send.assert_not_awaited()
        asyncio.run(run())

    def test_material_group_collects_without_enabling_replies(self):
        async def run():
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(self.bridge.db)) as db,db:
                db.execute('INSERT INTO meme_sources VALUES (?,?,1)',('300','material'))
            self.bridge.collect_source_memes=AsyncMock()
            self.bridge.send=AsyncMock()
            await self.bridge.handle(dict(self.event,message=[{'type':'image','data':{'sub_type':1,'url':'https://gchat.qpic.cn/test'}}]))
            self.bridge.collect_source_memes.assert_awaited_once()
            self.bridge.send.assert_not_awaited()
            with closing(sqlite3.connect(self.bridge.db)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM groups WHERE enabled=1').fetchone()[0],0)
        asyncio.run(run())

    def test_local_group_admin_requires_token(self):
        async def run():
            self.bridge.call = AsyncMock(return_value=[{'group_id':300,'group_name':'op3'}])
            self.bridge.ws = SimpleNamespace(closed=False)
            rejected = await self.bridge.admin_groups(SimpleNamespace(headers={}))
            self.assertEqual(rejected.status,401)
            self.bridge.call.assert_not_awaited()
            response = await self.bridge.admin_groups(SimpleNamespace(headers={'Authorization':'Bearer secret'}))
            self.assertEqual(response.status,200)
            self.bridge.call.assert_awaited_once_with('get_group_list',{})
        asyncio.run(run())

    def test_reply_inspection_only_enabled_group_and_self(self):
        async def run():
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(self.bridge.db)) as db, db:
                db.execute('INSERT INTO groups VALUES (?,?,?)',('300',1,1))
            self.bridge.call=AsyncMock(side_effect=[{'user_id':100},{'messages':[
                {'user_id':200,'message':[{'type':'text','data':{'text':'private group member message'}}]},
                {'user_id':100,'message':[{'type':'text','data':{'text':'bot reply'}}]},
            ]}])
            request=SimpleNamespace(headers={'Authorization':'Bearer secret'},query={'group_id':'301'})
            self.assertEqual((await self.bridge.admin_replies(request)).status,403)
            self.bridge.call.assert_not_awaited()
            request.query={'group_id':'300'}
            response=await self.bridge.admin_replies(request)
            self.assertEqual(json.loads(response.text)['replies'][0]['text'],'bot reply')
            self.assertNotIn('private group member message',response.text)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
