"""在既有服务器源码上应用机器人改动，保留网页密码保护等线上改动。

用法：python upgrade_existing.py /path/to/jm_bot.py
调用前需部署 archives.py 和 qq_service.py；不会自动重载服务。
"""
import ast
import datetime
from pathlib import Path
import shutil
import sys
import json
import os


def upgrade(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if 'qq_executor = install(' in text:
        print("already upgraded")
        return
    start = text.index('# ======================= QQ 机器人 Webhook')
    end = text.index('# ======================= 启动服务', start)
    replacement = '''# ======================= QQ 机器人 Webhook =======================
from qq_service import install
qq_executor = install(app, bot_api, QQ_APP_SECRET, SERVER_DOMAIN, DOWNLOAD_DIR / ".archives")

'''
    text = text[:start] + replacement + text[end:]
    # 领取接口自身验证限时签名；网站现有密码保护继续生效。
    text = text.replace('{"jm_auth", "qq_callback"}', '{"jm_auth", "qq_callback", "archive_download"}')
    for scene in ['群', '私聊']:
        old = f'logger.info(f"发送{scene}消息结果: {{result}}")'
        new = '''if resp.status >= 400 or result.get("code", 0):
                    raise RuntimeError(f"QQ 发送失败，HTTP {resp.status}，code={result.get('code')}")'''
        text = text.replace(old, new)
    ast.parse(text)
    backup = path.with_suffix('.py.before-qq-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    env_path = path.with_name('.env')
    env_text = env_path.read_text(encoding='utf-8') if env_path.exists() else ''
    substitutions = []
    for node in ast.parse(text).body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name in {'QQ_APP_ID', 'QQ_APP_SECRET', 'QQ_TOKEN'} and isinstance(node.value, ast.Constant):
            if not any(line.startswith(name + '=') for line in env_text.splitlines()):
                env_text += '\n' + name + '=' + json.dumps(node.value.value) + '\n'
            old = ast.get_source_segment(text, node)
            substitutions.append((old, f'{name} = os.getenv("{name}", "")'))
    for old, new in substitutions:
        text = text.replace(old, new)
    ast.parse(text)
    env_path.write_text(env_text, encoding='utf-8')
    os.chmod(env_path, 0o600)
    staged = path.with_suffix('.py.next')
    staged.write_text(text, encoding='utf-8')
    staged.replace(path)
    print('updated; backup:', backup.name)


if __name__ == '__main__':
    upgrade(sys.argv[1])
