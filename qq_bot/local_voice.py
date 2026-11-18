"""Authenticated local Feiyu service; durable submissions, no uncertain retries."""
import json
import os
from pathlib import Path
import re
import time
import sqlite3
import subprocess
import wave
import requests
from contextlib import closing
from filelock import FileLock
try:
    from .feiyu_client import FeiyuClient
except ImportError:
    from feiyu_client import FeiyuClient

def allowed(scope, group):
    if os.getenv('QQ_LOCAL_VOICE_ENABLED')!='1':return False
    if group:
        if os.getenv('QQ_GROUP_VOICE_ENABLED')!='1' or not re.fullmatch(r'group:'+re.escape(str(group))+r':[0-9]+',scope):return False
        database=Path(os.getenv('ONEBOT_DATA_DIR','/home/myl/qq-companion'))/'onebot.sqlite3'
        try:
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                row=db.execute('SELECT enabled FROM groups WHERE id=?',(str(group),)).fetchone()
                return bool(row and row[0])
        except sqlite3.Error:return False
    owner=os.getenv('QQ_AGENT_OWNER_ID','')
    return bool(os.getenv('QQ_LOCAL_VOICE_ENABLED')=='1' and owner and not group
                and scope in ('private:'+owner,'test:private:'+owner))

def generate(root, turn_id, kind, args, cancelled):
    if kind not in ('speak','sing','cover_song'):raise ValueError('Unknown voice operation')
    folder=Path(root)/turn_id/kind
    folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'job.json'
    with FileLock(str(folder/'.lock'),timeout=2):
        state=json.loads(manifest.read_text()) if manifest.exists() else {}
        if state.get('provider') == 'breeze' and state.get('status') == 'ready':
            return folder / 'voice.wav'
        if state.get('status')=='submitting':raise RuntimeError('Voice submission uncertain; check saved job before retrying')
        key = os.getenv('BREEZE_API_KEY', '').strip()
        if kind == 'speak' and key and not state:
            text = args.get('text')
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 250:
                raise ValueError('语音文本需1至250字')
            voices = {'cute':'voc_u3nxkd5578wc', 'gentle':'voc_8yud7hz7c9q2', 'idol':'voc_rn489h83rcvs'}
            voice = voices[args.get('style', 'idol')]
            if cancelled.is_set():
                raise RuntimeError('Voice operation cancelled')
            manifest.write_text(json.dumps({'status':'submitting','provider':'breeze'}))
            response = requests.post(f'https://api.breeze.blue/v1/text-to-speech/{voice}',
                params={'output_format':'wav_24000'}, headers={'xi-api-key':key},
                json={'text':text, 'language_code':'zh', 'model_id':'breeze-tts-2'},
                timeout=(6,45), allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError(f'BreezeBlue 语音合成失败，HTTP {response.status_code}')
            output = folder / 'voice.wav'
            pending = folder / 'voice.part'
            pending.write_bytes(response.content)
            with wave.open(str(pending)) as audio:
                if audio.getnframes() < 2400 or audio.getframerate() != 24000:
                    raise ValueError('BreezeBlue 返回的音频异常')
            pending.replace(output)
            manifest.write_text(json.dumps({'status':'ready','provider':'breeze','voice':voice}))
            return output
        client=FeiyuClient()
        if not state:
            if kind=='cover_song':
                library_path=Path(os.getenv('QQ_SONG_LIBRARY','/home/myl/qq-companion/song-library.json'))
                library=json.loads(library_path.read_text()) if library_path.exists() else {}
                title=args.get('title','').strip()
                if title not in library:raise ValueError('尚未导入此歌曲音源。可用歌曲：'+','.join(library))
                song=library[title]
                if song.get('completed_job_id'):
                    state={'status':'submitted','id':client.identifier(song['completed_job_id'])}
                asset=client.identifier(song['audio_id'])
            elif kind=='sing':
                import mido
                lyrics=''.join(re.findall(r'[\u4e00-\u9fff]',args.get('lyrics','')))
                if not 8<=len(lyrics)<=40:raise ValueError('短歌歌词需8至40个汉字，一字一音')
                notes=args.get('notes') or [60,62,64,67,69,67,64,62]*5
                if len(notes)<len(lyrics) or any(type(n) is not int or not 48<=n<=84 for n in notes):
                    raise ValueError('旋律音符不足或超出范围')
                midi=mido.MidiFile();track=mido.MidiTrack();midi.tracks.append(track)
                track.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(110)))
                for i,note in enumerate(notes[:len(lyrics)]):
                    track.append(mido.Message('note_on',note=note,velocity=90,time=0))
                    track.append(mido.Message('note_off',note=note,velocity=0,time=960 if i%8==7 else 480))
                score=folder/'melody.mid';midi.save(score)
                asset=client.upload(score)
            else:
                text=args.get('text','')
                if not isinstance(text,str) or not 1<=len(text.strip())<=250:raise ValueError('语音文本需1至250字')
                if args.get('style','cute') not in ('cute','gentle'):raise ValueError('Invalid voice style')
            if not state:
                manifest.write_text(json.dumps({'status':'submitting'}))
                result=client.cover(asset) if kind=='cover_song' else client.sing_score(asset,lyrics) if kind=='sing' else client.speak(text,args.get('style','cute'))
                state={'status':'submitted','id':client.identifier(result['id'])}
            manifest.write_text(json.dumps(state))
        output=folder/'voice.wav'
        final=folder/'full-song.mp3' if kind=='cover_song' else output
        if state.get('status')=='ready' and final.exists():return final
        deadline=time.monotonic()+500
        while not cancelled.is_set():
            result=client.status(state['id'])
            if result['status']=='success':
                client.download(state['id'],output)
                if kind=='cover_song':
                    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(output),'-c:a','libmp3lame','-b:a','192k',str(final)],check=True,timeout=120,capture_output=True)
                state['status']='ready';manifest.write_text(json.dumps(state))
                return final
            if result['status'] in ('failed','interrupted'):raise RuntimeError('Local voice job failed: '+state['id'])
            if time.monotonic()>deadline:raise RuntimeError('Local voice job pending: '+state['id'])
            cancelled.wait(2)
        raise RuntimeError('Voice operation cancelled')
