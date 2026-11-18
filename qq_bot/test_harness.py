import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from qq_bot.chat_agent import ChatAgent
from qq_bot.harness_protocol import to_chat, events
from qq_bot.harness_runtime import Turn, message_limit, harness_turns

class HarnessTests(unittest.TestCase):
    def test_tool_roundtrip_preserves_reasoning_and_call_identity(self):
        body={'system':'persona','messages':[{'role':'assistant','content':[{'type':'thinking','thinking':'reason'},{'type':'tool_use','id':'call1','name':'say','input':{'text':'hi'}}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'call1','content':'sent'}]}]}
        p=to_chat(body)
        self.assertEqual(p['messages'][1]['reasoning_content'],'reason')
        self.assertEqual(p['messages'][2]['tool_call_id'],'call1')
        self.assertEqual(json.loads(p['messages'][1]['tool_calls'][0]['function']['arguments']),{'text':'hi'})

    def test_stream_reports_tools_and_terminal_event(self):
        result={'choices':[{'message':{'tool_calls':[{'id':'a','function':{'name':'say','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}
        out=list(events(result))
        self.assertEqual(out[-1][0],'message_stop')
        self.assertEqual(out[-2][1]['delta']['stop_reason'],'tool_use')

    def test_quantity_and_default_budget(self):
        self.assertEqual(message_limit('你好'),3)
        self.assertEqual(message_limit('给我发五个图片'),5)
        self.assertEqual(message_limit('发三个图片'),3)
        self.assertEqual(message_limit('发100张'),3)

    def test_no_delivery_record_until_ack_and_send_limit(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                agent=ChatAgent(root,'https://example.test')
                q=asyncio.Queue();t=Turn(agent,'hi','scope','actor','',[],asyncio.get_running_loop(),q)
                worker=asyncio.create_task(asyncio.to_thread(t.send,'hello'))
                result,ack=await q.get()
                with agent.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
                ack.set_result(True);self.assertTrue((await worker)['sent'])
                with agent.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
                t.sent=t.limit
                self.assertEqual(t.send('fourth')['reason'],'message_limit')
        asyncio.run(run())

    def test_failed_send_never_recorded(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                agent=ChatAgent(root,'https://example.test');q=asyncio.Queue()
                t=Turn(agent,'hi','s','a','',[],asyncio.get_running_loop(),q)
                worker=asyncio.create_task(asyncio.to_thread(t.send,'hello'))
                _,ack=await q.get();ack.set_exception(RuntimeError('QQ failed'))
                with self.assertRaises(RuntimeError):await worker
                with agent.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        asyncio.run(run())

    def test_exhausted_delivery_budget_finishes_without_model_request(self):
        with tempfile.TemporaryDirectory() as root:
            agent=ChatAgent(root,'https://example.test')
            t=Turn(agent,'hi','s','a','',[],None,None);t.sent=t.limit
            result=t.model({})
            self.assertEqual(t.calls,0)
            self.assertEqual(result['choices'][0]['finish_reason'],'stop')
            self.assertTrue(result['choices'][0]['message']['content'])

    def test_search_is_cached_and_unknown_tools_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            agent=ChatAgent(root,'https://example.test')
            t=Turn(agent,'hi','s','a','',[],None,None)
            with patch('qq_bot.harness_runtime.web_search',return_value=[]) as search:
                t.tool('search_web',{'query':'天气'});t.tool('search_web',{'query':'天气'})
                search.assert_called_once()
            with self.assertRaises(ValueError):t.tool('shell',{'command':'anything'})

    def test_generator_acknowledges_only_after_transport_resumes(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                agent=ChatAgent(root,'https://example.test')
                def runtime(turn):turn.send('hello')
                with patch('qq_bot.harness_runtime.run',runtime):
                    stream=harness_turns(agent,'hi','s','a')
                    self.assertEqual((await anext(stream))['reply'],'hello')
                    with agent.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
                    with self.assertRaises(StopAsyncIteration):await anext(stream)
                    with agent.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        asyncio.run(run())
