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
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import has_request_context, request, g, jsonify

DB_PATH = os.getenv('SITE_BUDGET_DB', '/home/myl/api-budget/usage.sqlite3')
GLOBAL_LIMIT = 3_000_000_000
PERSON_LIMIT = 500_000_000
# Nano-CNY per token: input hit 0.04/M, miss 2/M, output 8/M.
HIT, MISS, OUTPUT = 40, 2000, 8000


class BudgetExceeded(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        if has_request_context():
            g.budget_error = message


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
        if total + amount > GLOBAL_LIMIT:
            raise BudgetExceeded('全站今日 3 元 AI 额度已用完或不足以完成本次请求，请北京时间明日零点后再试。')
        if personal + amount > PERSON_LIMIT:
            raise BudgetExceeded('您今日 0.5 元 AI 额度已用完或不足以完成本次请求，请北京时间明日零点后再试。')
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


def completion(api_key, payload, project, timeout=100):
    """Reserve worst-case cost atomically before any network activity.

    Unknown/timeout/invalid usage keeps the reservation, including after restart.
    Never retries upstream. Small midnight quiet window avoids cross-day billing.
    """
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.hour == 23 and now.minute >= 57:
        raise BudgetExceeded('每日额度正在结算，请北京时间零点后再试。')
    messages = payload.get('messages')
    if not isinstance(messages, list) or not messages or any(
        m.get('role') not in ('system','user','assistant') or not isinstance(m.get('content'), str)
        for m in messages
    ):
        raise BudgetExceeded('仅支持文本请求，已停止 API 调用。')
    payload = dict(payload)
    payload.update(model='deepseek-flash', max_tokens=min(int(payload.get('max_tokens',4096)),4096),
                   stream=False, thinking={'type':'disabled'})
    if payload['max_tokens'] <= 0:
        raise BudgetExceeded('无效输出长度。')
    # UTF-8 bytes bound text BPE tokens; generous allowance for chat framing.
    input_bound = sum(len(m['content'].encode('utf-8')) + 256 for m in messages) + 1024
    if input_bound > 120_000:
        raise BudgetExceeded('输入内容过长，请缩短后重试。')
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
    result = response.json()
    try:
        cost = usage_cost(result['usage'])
    except (KeyError,ValueError,TypeError):
        # Missing billing data: retain upper bound instead of silently allowing more.
        return result
    settle(token, cost, 'settled')
    return result
