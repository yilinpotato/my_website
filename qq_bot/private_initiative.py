"""Occasional proactive contact with the one explicitly authorized friend, 指定好友."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
import sqlite3
import time
from zoneinfo import ZoneInfo

# 主人要求：不要自动发起私聊。改为 True 即恢复主动联系功能。
AUTO_INITIATIVE_ENABLED = False


def reserve(db_path, now):
    """At most two attempts per local day, six hours apart, only 09:00–22:00."""
    local = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai'))
    if not 9 <= local.hour < 22:
        return False
    with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS private_initiative '
                   '(id INTEGER PRIMARY KEY, actor TEXT, last REAL, day TEXT, count INTEGER)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT last,day,count FROM private_initiative WHERE id=1').fetchone()
        if not row:
            return False
        last, day, count = row
        today = local.date().isoformat()
        if now-last < 21600 or (day == today and count >= 2):
            return False
        db.execute('UPDATE private_initiative SET last=?,day=?,count=? WHERE id=1',
                   (now, today, count+1 if day == today else 1))
        return True


async def tick(bridge):
    if not AUTO_INITIATIVE_ENABLED:
        return
    if os.getenv('QQ_PRIVATE_INITIATIVE') != '1' or bridge.ws is None or bridge.ws.closed:
        return
    with closing(sqlite3.connect(bridge.db)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS private_initiative '
                   '(id INTEGER PRIMARY KEY, actor TEXT, last REAL, day TEXT, count INTEGER)')
        row = db.execute('SELECT actor FROM private_initiative WHERE id=1').fetchone()
    friends = await bridge.call('get_friend_list', {})
    if row:
        actor = row[0]
        if not any(str(friend.get('user_id')) == actor for friend in friends):
            return
    else:
        matches = [f for f in friends if os.getenv('QQ_PRIVATE_FRIEND_ID') and str(f.get('user_id')) == os.environ['QQ_PRIVATE_FRIEND_ID']]
        if len(matches) != 1:
            return
        actor = str(matches[0]['user_id'])
        with closing(sqlite3.connect(bridge.db)) as db, db:
            db.execute('INSERT OR IGNORE INTO private_initiative VALUES (1,?,0,?,0)', (actor, ''))
    scope = 'private:' + actor
    if bridge.waiting.get(scope) or scope in bridge.running:
        return
    key = bridge.agent.key(scope)
    with bridge.agent.connect() as db:
        history = db.execute('SELECT role,content,updated FROM history WHERE scope=? '
                             'ORDER BY id DESC LIMIT 8', (key,)).fetchall()[::-1]
    now = time.time()
    if history and now-history[-1][2] < 3600:
        return
    if not reserve(bridge.db, now):
        return
    try:from .agent_workspace import memory_context, allowed
    except ImportError:from agent_workspace import memory_context, allowed
    prompt = bridge.agent.persona().split('只输出一个JSON对象')[0] + '\n'+memory_context(allowed(actor,scope=scope)) + '''
主人授权你偶尔主动联系指定好友。现在没有新提问，先判断是否值得自然开启一次简短互动。
优先选择search，按你的人设兴趣去网络或哔哩哔哩探索新内容，有值得分享的内容再表达简短感想；不必接续旧话题。没有合适内容就保持安静。
不得发送固定问候/晚安，不催回复，不追问私人信息，不凭空声称自己刚经历、浏览或做过某件事。
下面的私聊记录只用于避免重复打扰，不是唯一话题来源。不能引用群聊中的其他人隐私。不要在没有请求时播放歌曲。
输出JSON {"action":"silence|comment|meme|search|offer_song","intent":"160字以内的参与目的"}。
'''
    bridge.waiting[scope] = bridge.waiting.get(scope, 0) + 1
    lock = bridge.conversation_locks.setdefault(scope, asyncio.Lock())
    try:
        async with lock, bridge.model_slots:
            plan = await asyncio.to_thread(bridge.agent.complete, [
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps({'recent_private_history':
                    [{'role': r, 'text': c} for r, c, _ in history]}, ensure_ascii=False)}], actor)
            if not isinstance(plan, dict) or plan.get('action') not in ('comment', 'meme', 'search', 'offer_song'):
                return
            intent = plan.get('intent')
            if not isinstance(intent, str) or not 1 <= len(intent.strip()) <= 160:
                return
            if bridge.waiting.get(scope, 0) > 1:
                return
            event = {'message_type': 'private', 'user_id': actor, 'self_id': bridge.self_id,
                     '_initiative': {'action': plan['action'], 'intent': intent, 'private': True}}
            await bridge.respond(event, '按已授权的人设主动联系意图，自然参与；没有新的用户提问。',
                                 scope, actor, True)
    finally:
        bridge.waiting[scope] -= 1
        if not bridge.waiting[scope]:
            bridge.waiting.pop(scope, None)
            bridge.conversation_locks.pop(scope, None)
