import re
import secrets
import time
from flask import Flask, abort, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from core import KINDS, beijing, confirm, database, digest, get_meta, initialize, settings, subscribe, unsubscribe


def create_app(cfg=None):
    cfg=cfg or settings()
    initialize(cfg)
    app=Flask(__name__)
    app.secret_key=cfg['secret']
    app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_prefix=1)
    app.config.update(SESSION_COOKIE_NAME='reset_session',SESSION_COOKIE_PATH='/codex-reset',
                      SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',
                      MAX_CONTENT_LENGTH=4096)

    @app.before_request
    def csrf():
        if 'csrf' not in session:session['csrf']=secrets.token_urlsafe(32)
        # RFC 8058 uses an unguessable per-subscriber URL instead of a browser CSRF token.
        one_click=(request.endpoint in ('opt_out','private_opt_out') and request.form.get('List-Unsubscribe')=='One-Click')
        if request.method=='POST' and not one_click:
            if not secrets.compare_digest(request.form.get('csrf',''),session['csrf']):abort(400)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def page(message=None,action=None,code=200):
        focused=request.endpoint in ('verify','opt_out') or (request.endpoint=='register' and code==200)
        if focused:
            title=('确认订阅' if action=='confirm' else '确认退订' if action=='unsubscribe'
                   else '订阅已生效' if request.endpoint=='verify' and request.method=='POST' and code==200
                   else '链接已失效' if code>=400 else '已退订' if request.endpoint=='opt_out'
                   else '请查收验证邮件')
            return render_template('action.html',title=title,message=message,action=action),code
        with database(cfg) as db:
            events=db.execute('SELECT * FROM events ORDER BY announced DESC LIMIT 8').fetchall()
            success=get_meta(db,'last_success')
            updated=get_meta(db,'upstream_updated')
            stale=not success or time.time()-float(success)>1800 or bool(get_meta(db,'last_error'))
        return render_template('index.html',events=events,kinds=KINDS,beijing=beijing,
                               checked=float(success) if success else None,
                               updated=float(updated) if updated else None,stale=stale,
                               message=message,action=action),code

    @app.get('/')
    def index():return page()

    @app.post('/subscribe')
    def register():
        email=request.form.get('email','').strip().lower()
        preferences=[k for k in KINDS if k in request.form.getlist('kinds')]
        if len(email)>254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",email):
            return page('请输入有效的邮箱地址。',code=400)
        if not preferences:return page('请至少选择一种提醒。',code=400)
        if request.form.get('website'):return page('请求已收到。')
        if not subscribe(cfg,email,preferences,request.remote_addr or 'unknown'):
            return page('操作较频繁，请一小时后重试。',code=429)
        return page('请求已收到。若邮箱尚未订阅，将在几分钟内收到验证邮件；请检查收件箱及垃圾邮件文件夹。已生效的订阅不会重复发送验证邮件。')

    @app.route('/confirm/<token>',methods=['GET','POST'])
    def verify(token):
        if len(token)>100:abort(404)
        if request.method=='POST':
            ok=confirm(cfg,token)
            return page('订阅已生效！后续新公告将发送到你的邮箱，每封邮件都可退订。' if ok else '验证链接已使用或过期，请重新填写邮箱申请。',code=200 if ok else 400)
        with database(cfg) as db:
            valid=db.execute('SELECT id FROM subscribers WHERE verify_hash=? AND verify_until>?',(digest(token),time.time())).fetchone()
        return page('点击确认后，才会启用邮件提醒。' if valid else '验证链接已使用或过期。',action='confirm' if valid else None,code=200 if valid else 400)

    @app.route('/unsubscribe/<token>',methods=['GET','POST'])
    def opt_out(token):
        if len(token)>100:abort(404)
        if request.method=='POST':
            unsubscribe(cfg,token)
            return page('已退订，后续不会再发送提醒。需要时可在首页重新订阅。')
        return page('确认后停止所有 Codex 重置邮件提醒。',action='unsubscribe')

    @app.get('/health')
    def health():
        with database(cfg) as db:
            success=get_meta(db,'last_success')
            fresh=bool(success and time.time()-float(success)<1800 and not get_meta(db,'last_error'))
        return {'status':'ok' if fresh else 'degraded','source':'LimitReset','fresh':fresh},200 if fresh else 503

    from private_unsubscribe import register_private_unsubscribe
    register_private_unsubscribe(app)
    return app
