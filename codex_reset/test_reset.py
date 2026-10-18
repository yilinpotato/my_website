import json
import re
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from app import create_app
from core import confirm, database, drain, ingest, initialize, subscribe, unsubscribe


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg={'database':str(Path(self.tmp.name)/'test.sqlite3'),'secret':'test-secret',
                  'base_url':'https://example.test/codex-reset'}
        initialize(self.cfg)
        self.now=time.time()

    def tearDown(self):self.tmp.cleanup()

    def records(self, events=None, now=None):
        now=self.now if now is None else now
        def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
        records=[]
        for source,kind,announced in (events or [('1','reset',now-60)]):
            records.append({'sourceUrl':'https://x.com/test/status/'+source,'kind':kind,
                            'announcedAt':iso(announced),'productId':'codex','verified':True})
        return {'productId':'codex','updatedAt':iso(now)},{'productId':'codex','events':records}

    def join(self,email='me@example.test',preferences=None,now=None):
        subscribe(self.cfg,email,preferences or ['upcoming','reset','banked'],'127.0.0.1',now=now)
        with database(self.cfg) as db:
            mail=db.execute("SELECT * FROM outbox WHERE purpose='verify' ORDER BY id DESC").fetchone()
            sub=db.execute('SELECT * FROM subscribers WHERE email=?',(email,)).fetchone()
            token=re.search(r'/confirm/([\w-]+)',mail['body']).group(1)
        return token,dict(sub)

    def test_double_opt_in_expiry_and_scanner_safe_get(self):
        app=create_app(self.cfg)
        client=app.test_client()
        token,sub=self.join()
        response=client.get('/confirm/'+token,base_url='https://example.test')
        self.assertEqual(response.status_code,200)
        self.assertIn('确认订阅，开启提醒',response.get_data(as_text=True))
        self.assertNotIn('name="email"',response.get_data(as_text=True))
        self.assertNotIn('最近的公开记录',response.get_data(as_text=True))
        with database(self.cfg) as db:self.assertEqual(db.execute('SELECT active FROM subscribers').fetchone()[0],0)
        self.assertFalse(confirm(self.cfg,token,now=self.now+86401))
        self.assertTrue(confirm(self.cfg,token))
        self.assertFalse(confirm(self.cfg,token))

    def test_baseline_no_flood_then_dedup_and_preferences(self):
        token,sub=self.join(preferences=['upcoming'],now=self.now-1000)
        confirm(self.cfg,token,now=self.now-900)
        ingest(self.cfg,*self.records(),now=self.now)
        with database(self.cfg) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE purpose='alert'").fetchone()[0],0)
        latest,history=self.records([('2','upcoming',self.now+10),('3','banked',self.now+10)],now=self.now+20)
        ingest(self.cfg,latest,history,now=self.now+20)
        ingest(self.cfg,latest,history,now=self.now+21)
        sent=[]
        drain(self.cfg,sender=lambda *a:sent.append(a),now=self.now+22)
        drain(self.cfg,sender=lambda *a:sent.append(a),now=self.now+23)
        self.assertEqual(len(sent),1)
        self.assertIn('待执行预告',sent[0][3])
        self.assertIn('/unsubscribe/',sent[0][3])

    def test_no_historical_delivery_to_new_subscribers(self):
        ingest(self.cfg,*self.records(),now=self.now)
        token,_=self.join(now=self.now)
        confirm(self.cfg,token,now=self.now+1)
        ingest(self.cfg,*self.records([('2','reset',self.now-10)],now=self.now+20),now=self.now+20)
        with database(self.cfg) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE purpose='alert'").fetchone()[0],0)

    def test_unsubscribe_cancels_pending_and_one_click(self):
        token,sub=self.join()
        confirm(self.cfg,token)
        client=create_app(self.cfg).test_client()
        url='/unsubscribe/'+sub['unsub_token']
        client.get(url,base_url='https://example.test')
        with database(self.cfg) as db:self.assertEqual(db.execute('SELECT active FROM subscribers').fetchone()[0],1)
        self.assertEqual(client.post(url,data={'List-Unsubscribe':'One-Click'},base_url='https://example.test').status_code,200)
        with database(self.cfg) as db:self.assertEqual(db.execute('SELECT active FROM subscribers').fetchone()[0],0)
        sent=[]
        drain(self.cfg,sender=lambda *a:sent.append(a))
        self.assertEqual(sent,[])

    def test_smtp_failure_retries_without_losing_event(self):
        self.join()
        def fail(*args):raise ConnectionError('secret must not be logged')
        drain(self.cfg,sender=fail,now=self.now+1)
        with database(self.cfg) as db:
            job=db.execute('SELECT * FROM outbox').fetchone()
            self.assertEqual(job['state'],'pending')
            self.assertEqual(job['error'],'ConnectionError')
            next_try=job['next_try']
        sent=[]
        drain(self.cfg,sender=lambda *a:sent.append(a),now=next_try-1)
        self.assertEqual(sent,[])
        drain(self.cfg,sender=lambda *a:sent.append(a),now=next_try+1)
        self.assertEqual(len(sent),1)

    def test_stale_source_and_unknown_types_do_not_trigger(self):
        with self.assertRaises(ValueError):ingest(self.cfg,*self.records(now=self.now-8000),now=self.now)
        with self.assertRaises(ValueError):ingest(self.cfg,*self.records([('1','forecast',self.now)]),now=self.now)

    def test_kind_transition_is_new_but_duplicate_is_not(self):
        ingest(self.cfg,*self.records([('1','upcoming',self.now)]),now=self.now)
        token,_=self.join(now=self.now)
        confirm(self.cfg,token,now=self.now+1)
        ingest(self.cfg,*self.records([('1','reset',self.now+5)],now=self.now+10),now=self.now+10)
        with database(self.cfg) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE purpose='alert'").fetchone()[0],1)

    def test_csrf_validation_rate_limit_and_reverification(self):
        client=create_app(self.cfg).test_client()
        self.assertEqual(client.post('/subscribe',data={'email':'me@example.test'},base_url='https://example.test').status_code,400)
        for i in range(5):self.assertTrue(subscribe(self.cfg,f'u{i}@example.test',['reset'],'ip'))
        self.assertFalse(subscribe(self.cfg,'other@example.test',['reset'],'ip'))
        first,_=self.join()
        second,_=self.join()
        self.assertFalse(confirm(self.cfg,first))
        self.assertTrue(confirm(self.cfg,second))

    def test_email_has_html_and_plain_text_and_escapes_content(self):
        from unittest.mock import patch
        from core import send_mail
        cfg=dict(self.cfg,smtp={'sender':'sender@example.test','username':'sender','password':'secret','host':'smtp.example.test','port':465})
        with patch('core.smtplib.SMTP_SSL') as smtp:
            send_mail(cfg,'me@example.test','[Codex Reset] 验证邮箱','<unsafe>\nhttps://example.test/codex-reset/confirm/test','test','https://example.test/codex-reset/unsubscribe/test')
            msg=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg.get_content_type(),'multipart/alternative')
        self.assertIn('<unsafe>',msg.get_body(preferencelist=('plain',)).get_content())
        html=msg.get_body(preferencelist=('html',)).get_content()
        self.assertIn('&lt;unsafe&gt;',html)
        self.assertIn('#176746',html)
        self.assertIn('验证邮箱 →',html)


if __name__=='__main__':unittest.main()
