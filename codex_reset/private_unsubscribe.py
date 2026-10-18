"""Opt-out endpoint for the private recipient; GET only previews the action."""
import json
import secrets
import sqlite3
from pathlib import Path
from flask import abort, render_template, request


def register_private_unsubscribe(app, config_path='/opt/codex-private-mail/config.json'):
    @app.route('/private-unsubscribe/<token>', methods=['GET', 'POST'], endpoint='private_opt_out')
    def private_opt_out(token):
        cfg = json.loads(Path(config_path).read_text())
        expected = cfg.get('unsubscribe_token', '')
        if not expected or not secrets.compare_digest(token, expected):
            abort(404)
        if request.method == 'POST':
            with sqlite3.connect(cfg['database'], timeout=30) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("INSERT OR REPLACE INTO meta VALUES ('enabled','0')")
                db.execute("UPDATE outbox SET state='cancelled',body='' WHERE state='pending'")
            return render_template('action.html', title='已退订',
                message='已停止发送 Codex 重置提醒。已经在投递途中的邮件无法撤回。', action=None)
        return render_template('action.html', title='确认退订',
            message='确认后将停止向你的邮箱发送重置预告、完成记录和额度发放提醒。', action='unsubscribe')
