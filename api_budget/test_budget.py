import concurrent.futures
import os
import tempfile
import unittest
from unittest.mock import patch, Mock
from flask import Flask
import requests
import site_budget as b


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        b.DB_PATH = os.path.join(self.temp.name,'usage.db')
        self.app = Flask(__name__)
        b.install(self.app)
        self.ctx = self.app.test_request_context('/',environ_base={'REMOTE_ADDR':'127.0.0.1'},headers={'X-Real-IP':'203.0.113.1'})
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()
        self.temp.cleanup()

    def test_person_and_global_across_projects(self):
        b.reserve('a',500_000_000,'study')
        with self.assertRaises(b.BudgetExceeded): b.reserve('a',1,'myagents')
        for person in 'bcdef': b.reserve(person,500_000_000,'myagents')
        with self.assertRaises(b.BudgetExceeded): b.reserve('g',1,'study')

    def test_concurrent_reservations(self):
        with b.connect(): pass
        def attempt(i):
            try: b.reserve('a',100_000_000,str(i)); return True
            except b.BudgetExceeded: return False
        with concurrent.futures.ThreadPoolExecutor(10) as pool:
            self.assertEqual(sum(pool.map(attempt,range(20))),5)

    def test_next_day_and_persistence(self):
        b.reserve('a',500_000_000,'study','2026-09-25')
        with self.assertRaises(b.BudgetExceeded): b.reserve('a',1,'study','2026-09-25')
        b.reserve('a',500_000_000,'study','2026-09-26')

    def test_cached_usage(self):
        self.assertEqual(b.usage_cost({'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':60}),162400)

    @patch.object(b.requests,'post')
    def test_rejection_before_network(self,post):
        b.reserve(b.identity(),500_000_000,'study')
        with self.assertRaises(b.BudgetExceeded): b.completion('dummy',{'messages':[{'role':'user','content':'hi'}]},'myagents')
        post.assert_not_called()

    @patch.object(b.requests,'post')
    def test_timeout_keeps_reservation(self,post):
        post.side_effect=requests.Timeout()
        with self.assertRaises(requests.Timeout): b.completion('dummy',{'messages':[{'role':'user','content':'hi'}]},'study')
        with b.connect() as c:
            self.assertGreater(c.execute('SELECT SUM(amount) FROM calls').fetchone()[0],0)

    @patch.object(b.requests,'post')
    def test_success_settles(self,post):
        response=Mock(status_code=200)
        response.json.return_value={'usage':{'prompt_tokens':100,'completion_tokens':10},'choices':[]}
        post.return_value=response
        b.completion('dummy',{'messages':[{'role':'user','content':'hi'}]},'study')
        with b.connect() as c: self.assertEqual(c.execute('SELECT amount FROM calls').fetchone()[0],280000)
        self.assertEqual(post.call_args.kwargs['json']['max_tokens'],4096)
        self.assertFalse(post.call_args.kwargs['json']['stream'])

    def test_untrusted_forwarding(self):
        with self.app.test_request_context('/',environ_base={'REMOTE_ADDR':'203.0.113.2'},headers={'X-Real-IP':'8.8.8.8'}):
            self.assertEqual(b.identity(),b.hashlib.sha256(b'203.0.113.2').hexdigest())

    def test_429_even_if_endpoint_catches_error(self):
        @self.app.get('/fail')
        def fail():
            try: raise b.BudgetExceeded('额度已用完')
            except RuntimeError: return 'legacy failure',500
        r=self.app.test_client().get('/fail')
        self.assertEqual(r.status_code,429)
        self.assertEqual(r.json['code'],'daily_budget_exceeded')


if __name__=='__main__': unittest.main()
