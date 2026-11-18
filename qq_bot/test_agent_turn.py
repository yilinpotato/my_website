import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from qq_bot.agent_turn import turns,resolve_meme
from qq_bot.chat_agent import ChatAgent

class TurnTests(unittest.TestCase):
    def test_continue_only_with_unfinished_content(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                a=ChatAgent(root,'https://example.test')
                a.answer=Mock(return_value={'reply':'第一部分','continue':True,'remaining':'还有注意事项'})
                a.complete=Mock(return_value={'reply':'注意事项说完了','action':'none','continue':False})
                got=[r async for r in turns(a,'问题','s','u')]
                self.assertEqual([r['reply'] for r in got],['第一部分','注意事项说完了'])
                a.answer.return_value={'reply':'说完了','continue':True,'remaining':''}
                a.complete.reset_mock()
                self.assertEqual(len([r async for r in turns(a,'问题','s','u')]),1)
                a.complete.assert_not_called()
        asyncio.run(run())

    def test_no_continuation_before_successful_delivery(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                a=ChatAgent(root,'https://example.test')
                a.answer=Mock(return_value={'reply':'第一部分','continue':True,'remaining':'第二部分'})
                a.complete=Mock()
                gen=turns(a,'问题','s','u')
                await gen.__anext__()
                await gen.aclose()
                a.complete.assert_not_called()
        asyncio.run(run())

    def test_search_rewrites_then_reuses_original_requirement(self):
        store=Mock();store.choose.side_effect=[None,None,b'validated-image']
        a=Mock();a.complete.return_value={'query':'猫咪困倦'}
        with patch('qq_bot.agent_turn.discover') as discover:
            self.assertEqual(resolve_meme(store,a,'猫猫犯困','u','g',[]),b'validated-image')
        self.assertEqual([c.args[2] for c in discover.call_args_list],['猫猫犯困','猫咪困倦'])
        self.assertTrue(all(c.args[0]=='猫猫犯困' for c in store.choose.call_args_list))

    def test_personas_are_separate(self):
        with tempfile.TemporaryDirectory() as root:
            a=ChatAgent(root,'https://example.test');whale=a.persona()
            a.persona_name='chen'
            self.assertIn('陈千语',a.persona())
            self.assertNotEqual(a.persona(),whale)
