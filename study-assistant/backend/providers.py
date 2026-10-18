"""Production inference providers for the small CPU deployment."""
import os
import threading
from types import SimpleNamespace
import requests
from site_budget import completion


class DeepSeekClient:
    def __init__(self):
        self.models = self

    def generate_content(self, *, model, contents):
        result = completion(os.environ['DEEPSEEK_API_KEY'],
            {'messages': [{'role': 'user', 'content': contents}],
             'temperature': 0.3, 'max_tokens': 4096,
             'response_format': {'type': 'json_object'}}, 'study')
        return SimpleNamespace(text=result['choices'][0]['message']['content'])



def get_text_client(api_key):
    if os.getenv('DEEPSEEK_API_KEY'):
        return DeepSeekClient()
    raise RuntimeError('未配置受额度控制的 DeepSeek 服务')


_model_lock = threading.Lock()
_asr = None
_ocr = None


def transcribe_audio_file(path):
    global _asr
    with _model_lock:
        if _asr is None:
            from faster_whisper import WhisperModel
            _asr = WhisperModel(os.getenv('WHISPER_MODEL', 'base'), device='cpu',
                                compute_type='int8', cpu_threads=2, num_workers=1)
        segments, _ = _asr.transcribe(path, beam_size=3, vad_filter=True)
        return ''.join(segment.text for segment in segments).strip()


def run_ocr(path):
    global _ocr
    with _model_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        result, _ = _ocr(path)
        texts = [item[1] for item in (result or [])]
        return '\n'.join(texts), {'rec_texts': texts}
