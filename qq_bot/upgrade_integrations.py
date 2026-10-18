"""对线上/本地已有 jm_bot.py 定点接入缓存和接口错误；保留其余代码。"""
import ast
import os
from pathlib import Path
import shutil
import sys
import time


def upgrade(path):
    path = Path(path)
    source = path.read_text(encoding='utf-8')
    if 'from .qq_errors import read_api_response' not in source:
        source = source.replace('class QQBotAPI:', '''try:
    from .qq_errors import read_api_response
except ImportError:
    from qq_errors import read_api_response

class QQBotAPI:''')
        source = source.replace('result = await resp.json()',
                                'result = await read_api_response(resp, url, (self.app_secret, self.access_token))')
    if 'shared_manga_cache = ' not in source:
        source = source.replace('qq_executor = install(', '''try:
    from .web_cache import install_web_cache
except ImportError:
    from web_cache import install_web_cache
shared_manga_cache = install_web_cache(globals())

qq_executor = install(''')
    ast.parse(source)
    if source == path.read_text(encoding='utf-8'):
        return
    backup = path.with_suffix('.py.before-cache-' + str(int(time.time())))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    staged = path.with_suffix('.py.next')
    staged.write_text(source,encoding='utf-8')
    staged.replace(path)
    print('cache/error integration updated')


if __name__ == '__main__':
    upgrade(sys.argv[1])
