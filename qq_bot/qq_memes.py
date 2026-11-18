"""QQ-origin meme cache with visual labels; never searches stock-photo websites."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
import re
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode, urlunparse

import requests
from filelock import FileLock
from PIL import Image


def qq_image_url(url):
    p=urlparse(str(url))
    host=(p.hostname or '').lower()
    return (p.scheme in ('http','https') and not p.username and not p.password
            and p.port in (None,80,443)
            and any(host==d or host.endswith('.'+d) for d in ('qpic.cn','qq.com','qq.com.cn','gtimg.cn')))


def download_qq_image(url):
    for _ in range(3):
        if not qq_image_url(url): raise ValueError('不是允许的 QQ 图片地址')
        if url.startswith('http:'): url='https:'+url[5:]
        with requests.get(url,timeout=(5,12),stream=True,allow_redirects=False) as r:
            if 300<=r.status_code<400:
                url=urljoin(url,r.headers.get('Location',''))
                continue
            r.raise_for_status()
            data=bytearray()
            for part in r.iter_content(65536):
                data.extend(part)
                if len(data)>6*1024*1024: raise ValueError('QQ 表情超过 6 MiB')
            with Image.open(BytesIO(data)) as im, BytesIO() as out:
                if im.width*im.height>12_000_000: raise ValueError('QQ 表情尺寸过大')
                im.seek(0)
                preview=im.convert('RGB')
                preview.thumbnail((512,512))
                preview.save(out,format='JPEG',quality=80)
                return bytes(data),out.getvalue()
    raise ValueError('QQ 图片跳转次数过多')


class QQMemeStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'memes.sqlite3'
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('''CREATE TABLE IF NOT EXISTS memes(id TEXT PRIMARY KEY,url_key TEXT UNIQUE,
                source TEXT,group_id TEXT,description TEXT,tags TEXT,size INTEGER,used REAL)''')
            db.execute('CREATE TABLE IF NOT EXISTS rejected(url_key TEXT PRIMARY KEY,updated REAL)')
            if 'popularity' not in {r[1] for r in db.execute('PRAGMA table_info(memes)')}:
                db.execute('ALTER TABLE memes ADD COLUMN popularity INTEGER NOT NULL DEFAULT 1')

    @staticmethod
    def url_key(url):
        p=urlparse(url)
        q=[(k,v) for k,v in parse_qsl(p.query) if k.lower() not in ('rkey','token','expires')]
        return hashlib.sha256(urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(sorted(q)),'' )).encode()).hexdigest()

    def known(self,url):
        key=self.url_key(url)
        with closing(sqlite3.connect(self.db)) as db:
            return bool(db.execute('SELECT 1 FROM memes WHERE url_key=?',(key,)).fetchone() or db.execute('SELECT 1 FROM rejected WHERE url_key=? AND updated>?',(key,time.time()-86400)).fetchone())

    def note_usage(self,url,popularity):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('UPDATE memes SET popularity=MAX(popularity,?) WHERE url_key=?',(popularity,self.url_key(url)))

    def ingest(self,url,source,group_id,actor,describe,extra_tags='',popularity=1):
        if not qq_image_url(url): return False
        with FileLock(self.root/'.index.lock',timeout=45):
            if self.known(url): return False
            data,preview=download_qq_image(url)
            label=describe(preview,actor)
            if label.get('is_meme') is not True or label.get('safe') is not True:
                with closing(sqlite3.connect(self.db)) as db,db:
                    db.execute('INSERT OR REPLACE INTO rejected VALUES (?,?)',(self.url_key(url),time.time()))
                return False
            image_id=hashlib.sha256(data).hexdigest()
            description=str(label.get('description',''))[:450]
            tags=[str(t)[:30] for t in label.get('tags',[]) if isinstance(t,str)][:16]
            if extra_tags:
                tags.extend(extra_tags[:120].replace('，',' ').replace(',',' ').split()[:8])
            if not description: return False
            temp=self.root/(uuid.uuid4().hex+'.tmp')
            temp.write_bytes(data);os.replace(temp,self.root/(image_id+'.img'))
            with closing(sqlite3.connect(self.db)) as db,db:
                db.execute('INSERT OR REPLACE INTO memes(id,url_key,source,group_id,description,tags,size,used,popularity) VALUES (?,?,?,?,?,?,?,?,?)',
                    (image_id,self.url_key(url),source,str(group_id),description,json.dumps(tags,ensure_ascii=False),len(data),time.time(),max(1,int(popularity))))
                rows=db.execute('SELECT id,size FROM memes ORDER BY used DESC').fetchall()
                total=0
                for i,(mid,size) in enumerate(rows):
                    total+=size
                    if i>=512 or total>100*1024*1024:
                        (self.root/(mid+'.img')).unlink(missing_ok=True)
                        db.execute('DELETE FROM memes WHERE id=?',(mid,))
            return True

    def total(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute('SELECT COUNT(*) FROM memes').fetchone()[0]

    def candidates(self,group_id='',allowed_sources=None):
        groups=list(dict.fromkeys([str(group_id)]+[str(g) for g in (allowed_sources or [])]))
        with closing(sqlite3.connect(self.db)) as db:
            rows=db.execute("SELECT id,description,tags,popularity,source FROM memes WHERE source IN ('qq_favorite','user_upload','curated_pack') OR group_id IN ("+','.join('?' for g in groups)+") ORDER BY popularity DESC,used DESC LIMIT 512",groups).fetchall()
        results=[]
        for row in rows:
            try: tags=json.loads(row[2])
            except ValueError:continue
            results.append({'id':row[0],'description':row[1],'tags':tags,'popularity':row[3],'source':row[4]})
        return results

    def choose(self,query,actor,group_id,selector,allowed_sources=None,exclude=None):
        options=[o for o in self.candidates(group_id,allowed_sources) if o["id"] not in (exclude or set())]
        for names in [('奶龙',),('咖波','猫猫虫'),('卡皮巴拉','水豚'),('熊猫头',),('蘑菇头',),('鲸鱼娘','DeepSeek娘'),('柴犬',),('仓鼠',),('兔子','兔兔')]:
            if any(name.lower() in query.lower() for name in names):
                options=[o for o in options if any(name.lower() in (o['description']+' '.join(o['tags'])).lower() for name in names)]
        if not options: return None
        terms={query[i:i+2] for i in range(max(0,len(query)-1))}
        options=sorted(options,key=lambda o:sum(t in o['description']+' '.join(o['tags']) for t in terms),reverse=True)[:64]
        selected=selector(query,options,actor)
        mid=selected.get('id')
        if selected.get('match') is not True or mid not in {o['id'] for o in options}: return None
        path=self.root/(mid+'.img')
        if not path.is_file(): return None
        data=path.read_bytes()
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('UPDATE memes SET used=? WHERE id=?',(time.time(),mid))
        return data
