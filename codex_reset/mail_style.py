"""Inline HTML email with a plain-text counterpart provided by core.send_mail."""
import re
from html import escape


def render_email(subject, body, unsubscribe_url):
    links=re.findall(r'https://[^\s<>]+',body)
    confirm=next((url for url in links if '/confirm/' in url),None)
    source=next((url for url in links if url.startswith(('https://x.com/','https://twitter.com/'))),None)
    home=unsubscribe_url.split('/unsubscribe/')[0]+'/'
    target=confirm or source or home
    label='验证邮箱' if confirm else '查看原始公告' if source else '打开提醒页面'
    title='确认你的订阅' if confirm else subject.replace('[Codex Reset]','').strip()
    paragraphs=[]
    for line in body.splitlines():
        line=line.strip()
        if not line or line.startswith(('https://','验证邮箱（','取消此订阅请求：','退订：','查看状态：','订阅页面：','数据来源：')):
            continue
        # All links are rendered separately; avoid long raw URLs in mail layouts.
        if 'https://' in line:continue
        paragraphs.append('<p style="margin:0 0 16px;color:#586c64;font-size:15px;line-height:1.8">'+escape(line)+'</p>')
    content=''.join(paragraphs)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f6f0;font-family:Arial,'Microsoft YaHei',sans-serif;color:#142923">
<div style="display:none;max-height:0;overflow:hidden">{escape(title)} · Potatoma Codex 重置提醒</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f6f0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px"><tr><td style="padding:0 8px 22px;font-size:12px;font-weight:bold;letter-spacing:1px;color:#176746">POTATOMA / RESET WATCH</td></tr>
<tr><td style="background:#ffffff;border:1px solid #d8e2da;border-radius:18px;padding:32px 28px">
<p style="font-size:11px;font-weight:bold;letter-spacing:2px;color:#176746;margin:0 0 16px">CODEX · EMAIL ALERTS</p>
<h1 style="font-size:26px;line-height:1.4;margin:0 0 24px;color:#142923">{escape(title)}</h1>
{content}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:26px"><tr><td bgcolor="#176746" style="border-radius:9px;text-align:center"><a href="{escape(target,quote=True)}" style="display:inline-block;padding:15px 30px;color:#ffffff;font-size:16px;font-weight:bold;text-decoration:none">{label} →</a></td></tr></table>
<p style="font-size:12px;color:#586c64;margin:22px 0 0">{'链接 24 小时内有效。打开后点击“确认订阅”完成验证。非本人操作可忽略此邮件。' if confirm else '公开记录不代表个人账户额度已到账，请以实际用量页面为准。'}</p>
</td></tr><tr><td style="padding:22px 8px;color:#586c64;font-size:12px;line-height:1.8">
<a href="{escape(home,quote=True)}" style="color:#176746">提醒首页</a> · <a href="{escape(unsubscribe_url,quote=True)}" style="color:#176746">{'取消订阅请求' if confirm else '退订邮件'}</a><br>
{'邮箱验证后才会开启自动提醒。' if confirm else '数据来源：<a href="https://limitreset.net/api-docs" style="color:#176746">LimitReset</a> · CC BY 4.0'}<br>独立提醒服务，与 OpenAI 无关联。
</td></tr></table></td></tr></table></body></html>'''
