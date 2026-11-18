import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from qq_bot.agent_workspace import Workspace, allowed, settings

class WorkspaceTests(unittest.TestCase):
    def test_group_admin_scope_and_initiative(self):
        with patch.dict(os.environ,{'QQ_AGENT_ADMIN_GROUPS':'123456','QQ_AGENT_OWNER_ID':'123'}):
            for actor in ('123','456'):
                self.assertTrue(allowed(actor,'123456',scope='group:123456:'+actor))
                self.assertFalse(allowed(actor,'other',scope='group:other:'+actor))
                self.assertFalse(allowed(actor,'123456',{'action':'search'},'group:123456:'+actor))
            self.assertFalse(allowed('456',scope='private:456'))
            self.assertFalse(allowed('456','123456',scope='group:123456:123'))

    def test_memory_privacy_and_live_reload(self):
        from qq_bot.agent_workspace import memory_context
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'QQ_AGENT_WORKSPACE':d}):
            w=Workspace(d)
            w.write('记忆.md','private memory','')
            w.write('人物与特征.MD','private traits','')
            w.write('行为规则.md','public rules','')
            self.assertNotIn('private',memory_context(False))
            self.assertIn('private memory',memory_context(True))
            self.assertIn('public rules',memory_context(False))
            w.write('记忆.md','corrected memory',w.read('记忆.md')['sha256'])
            self.assertIn('corrected memory',memory_context(True))

    def test_scope_must_match_owner(self):
        with patch.dict(os.environ,{'QQ_AGENT_OWNER_ID':'123'}):
            self.assertFalse(allowed('123',scope='private:456'))
            self.assertTrue(allowed('123',scope='private:123'))

    def test_owner_is_enforced_at_gateway(self):
        from qq_bot.harness_runtime import Turn
        from qq_bot.chat_agent import ChatAgent
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'QQ_AGENT_OWNER_ID':'123','QQ_AGENT_WORKSPACE':d}):
            agent=ChatAgent(Path(d)/'chat','https://example.test')
            for actor,group,scope in [('other','','private:other'),('123','g','group:g:123')]:
                t=Turn(agent,'read','s',actor,group,[],None,None);t.scope=scope
                with self.assertRaises(PermissionError):t.tool('workspace_list',{})
            self.assertFalse(allowed('123','',{'action':'comment'},'private:123'))
            t=Turn(agent,'read','private:123','123','',[],None,None)
            self.assertIn('files',t.tool('workspace_list',{}))

    def test_path_confinement_cas_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            w=Workspace(d)
            for path in ('../escape','/etc/passwd','.env','a/../../escape','C:/Windows/x','a\\b'):
                with self.assertRaises(ValueError):w.write(path,'x','')
            first=w.write('notes/test.txt','hello','')
            with self.assertRaises(ValueError):w.write('notes/test.txt','lost','')
            w.write('notes/test.txt','updated',first['sha256'])
            self.assertEqual(w.read('notes/test.txt')['content'],'updated')
            self.assertEqual(len(list((Path(d)/'.backups').glob('*.bak'))),1)

    def test_config_bounds_and_live_reload(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'QQ_AGENT_WORKSPACE':d}):
            w=Workspace(d)
            for data in ({'owner_id':'hacker'},{'max_messages':100},{'context_seconds':True}):
                with self.assertRaises(ValueError):w.call('agent_config_set',{'settings':data,'expected_sha256':''})
            w.call('agent_config_set',{'settings':{'context_seconds':120,'persona_note':'自然聊天'},'expected_sha256':''})
            self.assertEqual(settings()['context_seconds'],120)
            self.assertEqual(settings()['max_messages'],3)


    def test_initial_persona_reload_and_backup(self):
        from qq_bot.chat_agent import ChatAgent, SYSTEM
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'QQ_AGENT_WORKSPACE':d}):
            w=Workspace(d)
            a=ChatAgent(Path(d)/'chat','https://example.test')
            self.assertEqual(a.persona(),SYSTEM)
            w.call('agent_persona_set',{'content':'First persona','expected_sha256':''})
            old=w.call('agent_persona_get',{})
            self.assertEqual(a.persona(),'First persona')
            w.call('agent_persona_set',{'content':'Updated persona','expected_sha256':old['sha256']})
            self.assertEqual(a.persona(),'Updated persona')
            with self.assertRaises(ValueError):w.call('agent_persona_set',{'content':'stale','expected_sha256':old['sha256']})
            with self.assertRaises(ValueError):w.write('persona.txt','   ',w.read('persona.txt')['sha256'])
            self.assertEqual(len(list((Path(d)/'.backups').glob('*.bak'))),1)
            a.persona_name='chen'
            self.assertNotEqual(a.persona(),'Updated persona')
