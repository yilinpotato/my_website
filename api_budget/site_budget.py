"""Shared, fail-closed DeepSeek budget. Money is integer nano-CNY.

All public callers must use completion(). X-Real-IP is overwritten by nginx;
backends listen on loopback only. Peak Flash prices, checked 2026-09-25:
https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
"""
import hashlib
import ipaddress
import json
import os
import sqlite3
import uuid
import base64
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import has_request_context, request, g, jsonify

DB_PATH = os.getenv('SITE_BUDGET_DB', '/home/myl/api-budget/usage.sqlite3')
GLOBAL_LIMIT = 3_000_000_000
PERSON_LIMIT = 500_000_000
# Nano-CNY per token: input hit 0.04/M, miss 2/M, output 8/M.
HIT, MISS, OUTPUT = 40, 2000, 8000
_service_person = ContextVar('site_budget_service_person', default=None)


@contextmanager
def service_identity(service, subject):
    """Trusted internal callers only; never populate from an HTTP header/body."""
    if service != 'qqbot' or not isinstance(subject, str) or not subject:
        raise BudgetExceeded('无效服务身份。')
    person = hashlib.sha256((service + ':' + subject).encode()).hexdigest()
    token = _service_person.set(person)
    try:
        yield
    finally:
        _service_person.reset(token)


class BudgetExceeded(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        if has_request_context():
            g.budget_error = message


class UpstreamResponseError(RuntimeError):
    pass


def install(app):
    @app.get('/api/budget')
    def budget_status():
        person, day = identity(), day_now()
        with connect() as c:
            total = c.execute('SELECT COALESCE(SUM(amount),0) FROM calls WHERE day=?',(day,)).fetchone()[0]
            own = c.execute('SELECT COALESCE(SUM(amount),0) FROM calls WHERE day=? AND person=?',(day,person)).fetchone()[0]
        return jsonify(day=day, timezone='Asia/Shanghai', global_limit_cny=3,
                       personal_limit_cny=0.5, global_used_cny=total/1e9,
                       personal_used_cny=own/1e9, identity='ip', pricing='peak_conservative')

    @app.errorhandler(BudgetExceeded)
    def exceeded(exc):
        return jsonify(error=str(exc), message=str(exc), code='daily_budget_exceeded'), 429

    @app.after_request
    def budget_response(response):
        # Some legacy endpoints catch RuntimeError themselves.
        if getattr(g, 'budget_error', None):
            response = jsonify(error=g.budget_error, message=g.budget_error, code='daily_budget_exceeded')
            response.status_code = 429
        return response


def day_now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def identity():
    if _service_person.get() is not None:
        return _service_person.get()
    if not has_request_context():
        raise BudgetExceeded('缺少访问者身份，已停止 API 调用。')
    # Do not trust caller-supplied forwarded headers on non-loopback requests.
    peer = request.environ.get('werkzeug.proxy_fix.orig', {}).get('REMOTE_ADDR', request.remote_addr)
    address = request.headers.get('X-Real-IP') if peer in ('127.0.0.1', '::1') else peer
    try:
        address = str(ipaddress.ip_address(address))
    except (ValueError, TypeError):
        raise BudgetExceeded('无法识别访问者，已停止 API 调用。')
    return hashlib.sha256(address.encode()).hexdigest()


@contextmanager
def connect():
    c = sqlite3.connect(DB_PATH, timeout=15)
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('''CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY, day TEXT NOT NULL, person TEXT NOT NULL,
            amount INTEGER NOT NULL, status TEXT NOT NULL, project TEXT NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS calls_day ON calls(day,person)')
        c.execute('''CREATE TABLE IF NOT EXISTS usage_details (
            call_id TEXT PRIMARY KEY, model TEXT, purpose TEXT, thinking INTEGER,
            prompt_tokens INTEGER, completion_tokens INTEGER, cache_hit_tokens INTEGER,
            reasoning_tokens INTEGER, key_fingerprint TEXT, recorded_at TEXT)''')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def reserve(person, amount, project, day=None):
    day = day or day_now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        total = c.execute('SELECT COALESCE(SUM(amount),0) FROM calls WHERE day=?', (day,)).fetchone()[0]
        personal = c.execute('SELECT COALESCE(SUM(amount),0) FROM calls WHERE day=? AND person=?', (day,person)).fetchone()[0]
        # QQ's temporary test ceiling applies only to authenticated service calls.
        qq_test = project == 'qqbot' and bool(_service_person.get())
        total_limit = int(os.getenv('QQ_TEST_GLOBAL_NCNY', GLOBAL_LIMIT)) if qq_test else GLOBAL_LIMIT
        person_limit = int(os.getenv('QQ_TEST_PERSON_NCNY', PERSON_LIMIT)) if qq_test else PERSON_LIMIT
        for kind,used,limit in [('全站',total,total_limit),('个人',personal,person_limit)]:
            if used + amount > limit:
                raise BudgetExceeded(f'{kind}日预算不足：已用及预留 ¥{used/1e9:.4f}，本次需预留 ¥{amount/1e9:.4f}，上限 ¥{limit/1e9:.2f}；北京时间零点重置。')
        token = uuid.uuid4().hex
        c.execute('INSERT INTO calls VALUES (?,?,?,?,?,?)', (token,day,person,amount,'reserved',project))
    return token


def settle(token, amount, status):
    with connect() as c:
        c.execute('UPDATE calls SET amount=?,status=? WHERE id=?', (amount,status,token))


def usage_cost(usage):
    prompt = int(usage['prompt_tokens'])
    output = int(usage['completion_tokens'])
    hit = int(usage.get('prompt_cache_hit_tokens', 0))
    if min(prompt, output, hit) < 0 or hit > prompt:
        raise ValueError('Invalid usage')
    return hit * HIT + (prompt-hit) * MISS + output * OUTPUT


def input_token_bound(messages):
    total, images = 1024, 0
    for message in messages:
        content = message.get('content')
        if message.get('role') not in (('system','user','assistant','tool') if _service_person.get() else ('system','user','assistant')):
            raise BudgetExceeded('无效消息角色。')
        if _service_person.get():
            total += len(json.dumps(message.get('tool_calls',[]),ensure_ascii=False).encode('utf-8'))
            total += len(str(message.get('reasoning_content') or '').encode('utf-8'))
        if isinstance(content,str):
            total += len(content.encode('utf-8')) + 256
            continue
        if not _service_person.get() or message.get('role')!='user' or not isinstance(content,list):
            raise BudgetExceeded('仅支持文本请求，已停止 API 调用。')
        total += 256
        for block in content:
            if block.get('type')=='text' and isinstance(block.get('text'),str):
                total += len(block['text'].encode('utf-8'))+256
            elif block.get('type')=='image_url':
                url=block.get('image_url',{}).get('url','')
                if not url.startswith('data:image/jpeg;base64,') or len(url)>1_400_000:
                    raise BudgetExceeded('仅允许受控的小型内嵌图片。')
                try: raw=base64.b64decode(url.split(',',1)[1],validate=True)
                except (ValueError,TypeError): raise BudgetExceeded('图片编码无效。')
                if not raw.startswith(b'\xff\xd8\xff'):
                    raise BudgetExceeded('图片格式无效。')
                images += 1
                if images>4: raise BudgetExceeded('单次最多处理 4 张图片。')
                # Official Vision docs: <=1024 tokens/image; add framing allowance.
                total += 1280
            else:
                raise BudgetExceeded('不支持的消息内容。')
    return total


def completion(api_key, payload, project, timeout=100, purpose='unspecified'):
    """Reserve worst-case cost atomically before any network activity.

    Unknown/timeout/invalid usage keeps the reservation, including after restart.
    Never retries upstream. Small midnight quiet window avoids cross-day billing.
    """
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.hour == 23 and now.minute >= 57:
        raise BudgetExceeded('每日额度正在结算，请北京时间零点后再试。')
    messages = payload.get('messages')
    if not isinstance(messages,list) or not messages or any(not isinstance(m,dict) for m in messages):
        raise BudgetExceeded('无效消息请求。')
    payload = dict(payload)
    thinking = bool(_service_person.get()) and project == 'qqbot' and payload.get('thinking') == {'type':'enabled'}
    payload.pop('reasoning_effort', None)
    if thinking:
        payload['reasoning_effort'] = 'low'
    payload.update(model='deepseek-flash', max_tokens=min(int(payload.get('max_tokens',4096)),4096),
                   stream=False, thinking={'type':'enabled' if thinking else 'disabled'})
    if payload['max_tokens'] <= 0:
        raise BudgetExceeded('无效输出长度。')
    # UTF-8 bytes bound text BPE tokens; generous allowance for chat framing.
    input_bound = input_token_bound(messages) + len(json.dumps(payload.get('tools',[]),ensure_ascii=False).encode('utf-8'))
    input_limit = int(os.getenv('QQ_TEST_INPUT_BOUND',120_000)) if project=='qqbot' and _service_person.get() else 120_000
    if input_bound > input_limit:
        raise BudgetExceeded(f'输入预算超限：估算上界 {input_bound}，上限 {input_limit}。')
    reserved = input_bound * MISS + payload['max_tokens'] * OUTPUT
    try:
        token = reserve(identity(), reserved, project)
    except sqlite3.Error as exc:
        raise BudgetExceeded('额度服务暂时不可用，已停止 API 调用。') from exc
    response = requests.post('https://api.deepseek.com/chat/completions',
                             headers={'Authorization':'Bearer '+api_key}, json=payload,
                             timeout=(10,min(timeout,100)))
    if response.status_code in (400,401,402,403,404,413,422,429):
        settle(token,0,'rejected')
    response.raise_for_status()
    try:
        result = response.json()
        if not isinstance(result,dict):
            raise ValueError('non-object response')
    except ValueError as exc:
        fingerprint=hashlib.sha256(response.content).hexdigest()[:12]
        logging.getLogger('site_budget').warning(
            'upstream_response_invalid ref=%s status=%s content_type=%s bytes=%s offset=%s',
            fingerprint,response.status_code,response.headers.get('Content-Type',''),
            len(response.content),getattr(exc,'pos','unknown'))
        # Billing is unknown: keep the reservation; do not retry a paid call blindly.
        raise UpstreamResponseError(f'DeepSeek 接口返回数据不完整（HTTP {response.status_code}，响应编号 {fingerprint}），请稍后再试。') from exc
    try:
        cost = usage_cost(result['usage'])
    except (KeyError,ValueError,TypeError):
        # Missing billing data: retain upper bound instead of silently allowing more.
        return result
    settle(token, cost, 'settled')
    usage = result['usage']
    try:
        with connect() as c:
            c.execute('INSERT OR REPLACE INTO usage_details VALUES (?,?,?,?,?,?,?,?,?,?)',
                      (token, payload['model'], str(purpose)[:64], int(thinking),
                       int(usage['prompt_tokens']), int(usage['completion_tokens']),
                       int(usage.get('prompt_cache_hit_tokens', 0)),
                       (usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                       hashlib.sha256(api_key.encode()).hexdigest()[:12],
                       datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()))
    except (sqlite3.Error, ValueError, TypeError):
        logging.getLogger('site_budget').warning('usage_detail_write_failed call_id=%s', token)
    return result
