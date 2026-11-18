"""Bounded delivery loop and validated, persistent meme discovery."""
import asyncio
import hashlib
import html
import json
import re
import sqlite3
import time
import logging
from contextlib import closing
from io import BytesIO
from urllib.parse import urlparse

import requests
from PIL import Image
from filelock import FileLock

try:
    from .qq_memes import QQMemeStore
except ImportError:
    from qq_memes import QQMemeStore


def discover(store, agent, query, actor):
    response=requests.get('https://memecub.com/zh/search',params={'q':query[:100]},timeout=(5,15))
    response.raise_for_status()
    if len(response.content)>3_000_000:return 0
    candidates=re.findall(r'<img[^>]*src="(https://gpic[^\"]+)"[^>]*alt="([^\"]*)"',response.content.decode('utf-8'))
    added=0
    for url,title in list(dict.fromkeys(candidates))[:4]:
        url=html.unescape(url)
        if urlparse(url).hostname!='gpic.code-nav.cn' or not urlparse(url).path.startswith('/memecub/'):continue
        key=hashlib.sha256(url.encode()).hexdigest()
        with FileLock(store.root/'.index.lock',timeout=45):
            with closing(sqlite3.connect(store.db)) as db:
                if db.execute('SELECT 1 FROM memes WHERE url_key=?',('web:'+key,)).fetchone():continue
            with requests.get(url,stream=True,allow_redirects=False,timeout=(5,12)) as r:
                if r.status_code!=200:continue
                data=bytearray()
                for chunk in r.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>5_000_000:break
                if len(data)>5_000_000:continue
            try:
                with Image.open(BytesIO(data)) as im,BytesIO() as out:
                    if im.width*im.height>12_000_000:continue
                    im.convert('RGB').resize((min(im.width,512),min(im.height,512))).save(out,'JPEG')
                    label=agent.describe_meme(out.getvalue(),actor)
            except (OSError,ValueError):continue
            if label.get('safe') is not True or label.get('is_meme') is not True:continue
            mid=hashlib.sha256(data).hexdigest()
            with closing(sqlite3.connect(store.db)) as db,db:
                if db.execute('SELECT 1 FROM memes WHERE id=?',(mid,)).fetchone():continue
                if db.execute('SELECT COALESCE(SUM(size),0) FROM memes').fetchone()[0]+len(data)>100*1024*1024:break
                if db.execute('SELECT COUNT(*) FROM memes').fetchone()[0]>=512:break
                (store.root/(mid+'.img')).write_bytes(data)
                db.execute('INSERT INTO memes VALUES (?,?,?,?,?,?,?,?,?)',(mid,'web:'+key,'curated_pack','',str(label.get('description',''))[:450],json.dumps(label.get('tags',[])[:16],ensure_ascii=False),len(data),time.time(),1))
                added+=1
    return added


def resolve_meme(store,agent,query,actor,group,sources,exclude=None):
    image=store.choose(query,actor,group,agent.select_meme,sources,**({"exclude":exclude} if exclude else {}))
    if image:return image
    queries=[query];seen=set()
    for step in range(2):
        current=queries[-1]
        if current in seen:break
        seen.add(current)
        discover(store,agent,current,actor)
        image=store.choose(query,actor,group,agent.select_meme,sources,**({"exclude":exclude} if exclude else {}))
        if image:return image
        if step<1:
            plan=agent.complete([{'role':'system','content':'你为表情搜索改写关键词。只输出JSON {"query":"短关键词"}。保留原要求的主体和情绪，不能放宽成不相关图片。已尝试的词不要重复。'},
                {'role':'user','content':json.dumps({'request':query,'tried':list(seen)},ensure_ascii=False)}],actor)
            next_query=str(plan.get('query') or '').strip()[:100]
            if not next_query:break
            queries.append(next_query)
    return None


async def turns(agent,text,scope,actor,group='',sources=None,priority=False,max_parts=4,voice=False,initiative=None,context=None,sender=None):
    import os
    from dotenv import dotenv_values
    from pathlib import Path
    mode=os.getenv('QQ_HARNESS_MODE') or dotenv_values(Path(__file__).with_name('.env')).get('QQ_HARNESS_MODE','off')
    if mode=='on' or (mode=='test' and scope.startswith('test:private:')):
        try:
            from .harness_runtime import harness_turns
        except ImportError:
            from harness_runtime import harness_turns
        async for result in harness_turns(agent,text,scope,actor,group,sources,max_parts=max_parts,voice=voice,initiative=initiative,context=context,sender=sender):
            yield result
        return
    requested=re.search(r'(?:分|发|发送)\s*([4-8四五六七八])\s*(?:条|张|次)',text)
    message_limit=3
    if requested:
        value=requested[1];message_limit=int(value) if value.isdigit() else '四五六七八'.index(value)+4
        max_parts=max(max_parts,message_limit)
    sent=0
    store=QQMemeStore(agent.root/'qq-memes')
    result=await asyncio.to_thread(agent.answer,text,scope,actor,**({'priority':True} if priority else {}))
    batch=re.search(r'(?:发|发送|来|给我|要).*?([2-8二两三四五六七八])\s*(?:张|个)',text)
    if batch and re.search(r'图|表情',text):
        value=batch[1];target=int(value) if value.isdigit() else {'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8}[value]
        query=result.get('meme_query') or text
        excluded=set()
        for number in range(target):
            image=await asyncio.to_thread(resolve_meme,store,agent,query,actor,group,sources or [],excluded)
            if not image:
                yield {'reply':f'这次只找到 {number} 张符合要求的不重复表情，剩下的没有凑图。'}
                return
            excluded.add(hashlib.sha256(image).hexdigest())
            logging.getLogger('qq_turn').info('batch_image part=%s target=%s',number+1,target)
            yield {'reply':'','image':image}
            agent.record_delivery(scope,'',True)
        return
    delivered=[];seen=set()
    for index in range(max_parts):
        if sent>=message_limit:return
        if result.get('silent'):return
        if result.get('meme_query'):
            image=await asyncio.to_thread(resolve_meme,store,agent,result['meme_query'],actor,group,sources or [])
            result=dict(result,image=image,reply=(result.get('caption') or '给你～') if image else '没找到符合这个要求的表情，这次没有发送图片。')
            if not image:result['continue']=False
        signature=(result.get('reply',''),hashlib.sha256(result.get('image') or b'').hexdigest())
        if signature in seen:return
        seen.add(signature)
        if result.get('image') and result.get('reply') and message_limit-sent==1:
            result=dict(result,reply='')
        count=int(bool(result.get('reply')))+int(bool(result.get('image')))
        if not count:return
        logging.getLogger('qq_turn').info('part=%s image=%s continue=%s remaining=%s',index+1,bool(result.get('image')),result.get('continue') is True,bool(result.get('remaining')))
        yield result  # Caller sends successfully before the next iteration starts.
        agent.record_delivery(scope,result.get('reply',''),bool(result.get('image')))
        sent+=count
        delivered.append({'text':result.get('reply',''),'image_sent':bool(result.get('image'))})
        if sent>=message_limit or result.get('continue') is not True or not result.get('remaining') or index+1>=max_parts:return
        plan=await asyncio.to_thread(agent.complete,[{'role':'system','content':agent.persona()+'\n只补充原问题尚未完成的内容。不扩展新话题、不重复已发送内容。只输出JSON {"reply":"文字","action":"none|meme","query":"表情关键词","continue":false,"remaining":""}。已完成就reply为空。只有确有必要的未完成部分才continue=true并说明remaining。'},
            {'role':'user','content':json.dumps({'request':text,'already_sent':delivered,'unfinished':result['remaining']},ensure_ascii=False)}],actor)
        result={'reply':str(plan.get('reply') or '')[:1200],'continue':plan.get('continue') is True,'remaining':str(plan.get('remaining') or '')[:200]}
        if plan.get('action')=='meme':result.update(meme_query=str(plan.get('query') or text)[:160],caption=result['reply'])
        elif not result['reply']:return
