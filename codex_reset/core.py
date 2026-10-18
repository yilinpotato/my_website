"""Public reset events, double opt-in subscriptions and durable mail queue."""
import hashlib
import json
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

KINDS = {'upcoming': '重置预告', 'reset': '重置记录', 'banked': '重置额度发放'}
BASE_API = 'https://limitreset.net/api/v1/codex/'


def settings():
    return json.loads(Path(os.environ.get('RESET_CONFIG', '/etc/codex-reset/config.json')).read_text())


@contextmanager
def database(cfg):
    db = sqlite3.connect(cfg['database'], timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(cfg):
    Path(cfg['database']).parent.mkdir(parents=True, exist_ok=True)
    with database(cfg) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS subscribers (
          id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 0,
          preferences TEXT NOT NULL, confirmed_at REAL, generation INTEGER NOT NULL DEFAULT 0,
          verify_hash TEXT, verify_until REAL, unsub_hash TEXT NOT NULL, unsub_token TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          key TEXT PRIMARY KEY, kind TEXT NOT NULL, announced REAL NOT NULL,
          source TEXT NOT NULL, title TEXT NOT NULL, discovered REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
          id INTEGER PRIMARY KEY, unique_key TEXT UNIQUE NOT NULL,
          subscriber_id INTEGER NOT NULL REFERENCES subscribers(id), purpose TEXT NOT NULL,
          generation INTEGER NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
          next_try REAL NOT NULL DEFAULT 0, sent_at REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, started REAL, count INTEGER);
        ''')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def meta(db, key, value):
    db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, str(value)))


def get_meta(db, key, default=None):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def beijing(timestamp):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间')


def rate_limit(db, key, limit, now):
    row = db.execute('SELECT * FROM rate_limits WHERE key=?', (key,)).fetchone()
    if not row or now - row['started'] >= 3600:
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES (?,?,1)', (key, now))
        return True
    if row['count'] >= limit:
        return False
    db.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
    return True


def enqueue(db, subscriber, key, purpose, subject, body):
    db.execute('''INSERT OR IGNORE INTO outbox
      (unique_key,subscriber_id,purpose,generation,subject,body) VALUES (?,?,?,?,?,?)''',
      (key, subscriber['id'], purpose, subscriber['generation'], subject, body))


def subscribe(cfg, email, preferences, ip, now=None):
    now = time.time() if now is None else now
    with database(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        if not rate_limit(db, 'ip:'+digest(ip), 5, now):
            return False
        if not rate_limit(db, 'mail:'+digest(email), 3, now):
            return True  # Same response whether this mailbox already exists or not.
        current = db.execute('SELECT * FROM subscribers WHERE email=?', (email,)).fetchone()
        if current and current['active']:
            return True
        token = secrets.token_urlsafe(32)
        unsub = secrets.token_urlsafe(32)
        db.execute('''INSERT INTO subscribers
          (email,preferences,verify_hash,verify_until,unsub_hash,unsub_token)
          VALUES (?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET
          preferences=excluded.preferences,verify_hash=excluded.verify_hash,
          verify_until=excluded.verify_until,unsub_hash=excluded.unsub_hash,
          unsub_token=excluded.unsub_token,generation=subscribers.generation+1''',
          (email,json.dumps(preferences),digest(token),now+86400,digest(unsub),unsub))
        sub = db.execute('SELECT * FROM subscribers WHERE email=?',(email,)).fetchone()
        body = (f"请确认你在 Potatoma 订阅 Codex 重置提醒。\n\n"
                f"验证邮箱（24 小时内有效）：\n{cfg['base_url']}/confirm/{token}\n\n"
                f"订阅类型：{'、'.join(KINDS[k] for k in preferences)}\n"
                "验证后才会收到后续新公告，不会补发历史记录。\n"
                "若不是你操作，无需点击，订阅不会生效。\n\n"
                f"取消此订阅请求：{cfg['base_url']}/unsubscribe/{unsub}\n")
        enqueue(db,sub,'verify:'+digest(token),'verify','[Codex Reset] 验证你的订阅邮箱',body)
    return True


def confirm(cfg, token, now=None):
    now = time.time() if now is None else now
    with database(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM subscribers WHERE verify_hash=? AND verify_until>?',
                       (digest(token),now)).fetchone()
        if not row:
            return False
        db.execute('UPDATE subscribers SET active=1,confirmed_at=?,verify_hash=NULL WHERE id=?',
                   (now,row['id']))
        db.execute("UPDATE outbox SET state='cancelled',body='' WHERE subscriber_id=? AND purpose='verify' AND state='pending'",(row['id'],))
    return True


def unsubscribe(cfg, token):
    with database(cfg) as db:
        row=db.execute('SELECT id FROM subscribers WHERE unsub_hash=?',(digest(token),)).fetchone()
        if row:
            db.execute('UPDATE subscribers SET active=0,verify_hash=NULL,generation=generation+1 WHERE id=?',(row['id'],))
            db.execute("UPDATE outbox SET state='cancelled',body='' WHERE subscriber_id=? AND state='pending'",(row['id'],))


def fetch_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Potatoma-Codex-Reset/1.0 (+https://potatoma.me/codex-reset/)'})
    with urllib.request.urlopen(request,timeout=20) as response:
        body=response.read(2_000_001)
    if len(body)>2_000_000:
        raise ValueError('API response too large')
    return json.loads(body)


def iso_time(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def ingest(cfg, latest, history, now=None):
    now=time.time() if now is None else now
    updated=iso_time(latest['updatedAt'])
    if not -300 <= now-updated <= 7200:
        raise ValueError('API data is stale')
    if latest.get('productId')!='codex' or history.get('productId')!='codex' or not isinstance(history.get('events'),list):
        raise ValueError('Unexpected API schema')
    records=[]
    for event in history['events']:
        kind=event.get('kind')
        if kind not in KINDS or event.get('productId')!='codex' or event.get('verified') is not True:
            continue
        source=event.get('sourceUrl','')
        if not source.startswith(('https://x.com/','https://twitter.com/')):
            continue
        announced=iso_time(event['announcedAt'])
        if announced>now+300:
            raise ValueError('Event publication time is in the future')
        key=digest(source.rstrip('/')+'|'+kind)
        records.append((key,kind,announced,source,KINDS[kind],now))
    if not records:
        raise ValueError('API returned no supported events')
    with database(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        baseline=get_meta(db,'initialized') is None
        for record in sorted(records,key=lambda r:r[2]):
            inserted=db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',record).rowcount
            key,kind,announced,source,title,_=record
            if not inserted or baseline or announced<now-48*3600:
                continue
            for sub in db.execute('SELECT * FROM subscribers WHERE active=1 AND confirmed_at<=?',(announced,)).fetchall():
                if kind not in json.loads(sub['preferences']):
                    continue
                note='这是待执行预告，不能据此认定额度已经恢复。' if kind=='upcoming' else '这是第三方收录的公开记录，不代表你的个人账户额度已经到账。'
                body=(f"Codex {title}\n公告时间：{beijing(announced)}\n\n{note}\n"
                      f"请查看原始公告，核对适用账户与具体时间：\n{source}\n\n"
                      "数据来源：LimitReset（独立第三方，CC BY 4.0）\nhttps://limitreset.net/codex\n"
                      f"查看状态：{cfg['base_url']}/\n"
                      f"退订：{cfg['base_url']}/unsubscribe/{sub['unsub_token']}\n")
                enqueue(db,sub,f"event:{key}:{sub['id']}:{sub['generation']}",'alert',f'[Codex Reset] {title}',body)
        meta(db,'initialized',1)
        meta(db,'last_success',now)
        meta(db,'upstream_updated',updated)
        meta(db,'last_error','')


def send_mail(cfg, recipient, subject, body, message_key, unsubscribe_url):
    mail=cfg['smtp']
    msg=EmailMessage()
    msg['From']=mail['sender']
    msg['To']=recipient
    msg['Subject']=subject
    msg['Date']=formatdate(localtime=False)
    msg['Message-ID']=f"<{digest(message_key)}@potatoma.me>"
    msg['List-Unsubscribe']=f'<{unsubscribe_url}>'
    msg['List-Unsubscribe-Post']='List-Unsubscribe=One-Click'
    msg.set_content(body)
    from mail_style import render_email
    msg.add_alternative(render_email(subject,body,unsubscribe_url),subtype='html')
    context=ssl.create_default_context()
    client=smtplib.SMTP_SSL if mail.get('ssl',True) else smtplib.SMTP
    kwargs={'timeout':20}
    if mail.get('ssl',True):kwargs['context']=context
    with client(mail['host'],mail['port'],**kwargs) as smtp:
        if not mail.get('ssl',True):smtp.starttls(context=context)
        smtp.login(mail['username'],mail['password'])
        smtp.send_message(msg)


def drain(cfg, sender=send_mail, now=None):
    now=time.time() if now is None else now
    with database(cfg) as db:
        jobs=db.execute("SELECT id FROM outbox WHERE state='pending' AND next_try<=? ORDER BY id LIMIT 10",(now,)).fetchall()
    for job in jobs:
        # A short write lock linearizes opt-out against an in-flight send.
        with database(cfg) as db:
            db.execute('BEGIN IMMEDIATE')
            item=db.execute('SELECT * FROM outbox WHERE id=?',(job['id'],)).fetchone()
            sub=db.execute('SELECT * FROM subscribers WHERE id=?',(item['subscriber_id'],)).fetchone()
            valid=(item['state']=='pending' and item['generation']==sub['generation'] and
                   (bool(sub['active']) if item['purpose']=='alert' else bool(sub['verify_hash']) and sub['verify_until']>now))
            if not valid:
                db.execute("UPDATE outbox SET state='cancelled',body='' WHERE id=?",(item['id'],))
                continue
            try:
                sender(cfg,sub['email'],item['subject'],item['body'],item['unique_key'],cfg['base_url']+'/unsubscribe/'+sub['unsub_token'])
            except Exception as exc:
                attempts=item['attempts']+1
                db.execute('UPDATE outbox SET attempts=?,next_try=?,state=?,error=? WHERE id=?',
                           (attempts,now+min(3600,60*2**attempts),'failed' if attempts>=6 else 'pending',type(exc).__name__,item['id']))
            else:
                db.execute("UPDATE outbox SET state='sent',sent_at=?,body='',error=NULL WHERE id=?",(now,item['id']))
                meta(db,'last_mail_success',now)
    with database(cfg) as db:
        db.execute('DELETE FROM rate_limits WHERE started<?',(now-86400,))
        db.execute("DELETE FROM outbox WHERE state IN ('sent','cancelled') AND id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 1000)")


def poll(cfg):
    try:
        ingest(cfg,fetch_json(BASE_API+'latest'),fetch_json(BASE_API+'events'))
    except Exception as exc:
        with database(cfg) as db:meta(db,'last_error',type(exc).__name__)
        print('Event sync failed:',type(exc).__name__,flush=True)
    drain(cfg)


if __name__=='__main__':
    import fcntl
    cfg=settings()
    initialize(cfg)
    with open(str(Path(cfg['database']).with_suffix('.lock')),'w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit(0)
        poll(cfg)
