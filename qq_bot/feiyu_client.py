"""Client for the separately installed Feiyu voice service.

Submission returns a durable backend job ID, not a claim of audible delivery.
No retry of POST operations: ambiguous network failures may have created a job.
"""
import os
from pathlib import Path
import re

import requests


class FeiyuError(RuntimeError):
    pass


class FeiyuClient:
    def __init__(self, endpoint=None, token=None):
        self.endpoint = (endpoint or os.getenv('QQ_FEIYU_URL', '')).rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['Authorization'] = 'Bearer ' + (token or os.getenv('QQ_FEIYU_TOKEN', ''))

    def request(self, method, route, **kwargs):
        if not self.endpoint:
            raise FeiyuError('大肥鱼声音服务尚未配置')
        try:
            response = self.session.request(method, self.endpoint + route, timeout=30,
                                            allow_redirects=False, **kwargs)
            if response.status_code not in (200, 201, 202):
                response.close()
                raise FeiyuError(f'声音服务请求失败，HTTP {response.status_code}')
            return response
        except requests.RequestException as exc:
            raise FeiyuError('声音服务连接失败；提交状态不确定时请勿重复提交') from exc

    def json(self, method, route, **kwargs):
        with self.request(method, route, **kwargs) as response:
            try:
                return response.json()
            except ValueError as exc:
                raise FeiyuError('声音服务返回格式异常') from exc

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
            raise FeiyuError('声音服务任务或资源编号异常')
        return value

    def upload(self, path):
        path = Path(path)
        if path.suffix.lower() not in ('.wav', '.flac', '.mid', '.midi', '.abc'):
            raise ValueError('不支持的音频或谱面格式')
        if not 0 < path.stat().st_size <= 128 * 1024 * 1024:
            raise ValueError('音频或谱面文件大小异常')
        with path.open('rb') as stream:
            result = self.json('POST', '/v1/assets', files={'file': (path.name, stream)})
        return self.identifier(result['id'])

    def speak(self, text, style='cute'):
        if style not in ('cute', 'gentle') or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ValueError('语音文本或声线无效')
        return self.json('POST', '/v1/speech', json={'voice': 'feiyu', 'text': text, 'style': style})

    def convert(self, audio_id):
        return self.json('POST', '/v1/voice-conversion',
                         json={'voice': 'feiyu', 'audio_id': self.identifier(audio_id)})

    def cover(self, audio_id):
        return self.json('POST','/v1/voice-conversion',json={
            'voice':'feiyu','audio_id':self.identifier(audio_id),'full_cover':True})

    def sing_score(self, score_id, lyrics):
        if not isinstance(lyrics, str) or not 1 <= len(lyrics.strip()) <= 10000:
            raise ValueError('歌词无效')
        return self.json('POST', '/v1/singing', json={
            'voice': 'feiyu', 'score_id': self.identifier(score_id), 'lyrics': lyrics})

    def status(self, job):
        return self.json('GET', '/v1/jobs/' + self.identifier(job))

    def download(self, job, target):
        target = Path(target)
        temporary = target.with_suffix('.download')
        try:
            with self.request('GET', '/v1/jobs/' + self.identifier(job) + '/audio', stream=True) as response:
                size = 0
                with temporary.open('wb') as stream:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 128 * 1024 * 1024:
                            raise FeiyuError('声音服务返回的音频过大')
                        stream.write(chunk)
            if size < 44:
                raise FeiyuError('声音服务返回空音频')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
