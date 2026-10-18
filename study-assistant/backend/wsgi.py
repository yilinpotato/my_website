"""Private production entry point; upstream app remains usable separately."""
import os
import secrets
import time
import threading
from collections import defaultdict, deque
from flask import jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix
import app as upstream
from providers import transcribe_audio_file, run_ocr

app = upstream.app
from site_budget import install
install(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
upstream.transcribe_audio_file = transcribe_audio_file
upstream.run_ocr = run_ocr

with app.app_context():
    upstream.db.create_all()
    for name in ('ensure_error_book_entry_schema', 'ensure_note_assistant_schema',
                 'ensure_knowledge_schema', 'ensure_bind_schema'):
        getattr(upstream, name)()

_requests = defaultdict(deque)
_lock = threading.Lock()

@app.before_request
def production_limits():
    if request.method == 'POST' and request.path in (
        '/api/auth/register', '/api/auth/request-password-reset', '/api/auth/resend-code'):
        if not app.config.get('MAIL_PASSWORD'):
            return jsonify(message='当前提供访客体验，请从登录页进入。'), 503
    if request.method not in ('POST', 'PUT'):
        return None
    guest = request.path == '/api/auth/guest'
    limit = 10 if guest else 60
    key = (request.remote_addr, 'guest' if guest else 'write')
    now = time.monotonic()
    with _lock:
        # Bound memory even if many distinct public clients visit.
        for old_key in list(_requests):
            if not _requests[old_key] or _requests[old_key][-1] <= now - 3600:
                del _requests[old_key]
        bucket = _requests[key]
        while bucket and bucket[0] <= now - 3600:
            bucket.popleft()
        if len(bucket) >= limit:
            return jsonify(message='本小时体验次数已用完，请稍后再试。'), 429
        bucket.append(now)

@app.post('/api/auth/guest')
def guest_login():
    suffix = secrets.token_hex(12)
    token = secrets.token_urlsafe(48)
    user = upstream.User(email=f'{suffix}@guest.invalid', username=f'guest_{suffix}',
                         display_name='访客', password_hash=secrets.token_hex(48),
                         role='student', verified=True, auth_token=token)
    upstream.db.session.add(user)
    upstream.db.session.commit()
    return jsonify(token=token)

@app.get('/api/health')
def health():
    return jsonify(status='ok', provider='DeepSeek', speech='faster-whisper', ocr='RapidOCR')

def whisper_status():
    _, error, status = upstream.require_auth()
    if error:
        return error, status
    import providers
    return jsonify(whisper_model_resolved='Whisper base · CPU int8',
                   pipeline_loaded=providers._asr is not None, ffmpeg_in_path=True,
                   init_error='', cuda_available=False)

app.view_functions['note_whisper_status'] = whisper_status
