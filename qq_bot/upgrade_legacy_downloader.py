"""让旧网站后台下载队列复用机器人/查看器缓存，保留其余站点逻辑。"""
import ast
from pathlib import Path
import shutil
import sys
import time


def upgrade(path):
    path=Path(path)
    text=path.read_text(encoding='utf-8')
    tree=ast.parse(text)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_perform_download')
    replacement='''def _perform_download(album_id):
    """旧后台队列使用网页/QQ 共用的完整漫画缓存。"""
    try:
        from pathlib import Path
        module_root = Path(__file__).resolve().parents[2] / "qq_bot"
        if str(module_root) not in sys.path:
            sys.path.insert(0, str(module_root))
        from archives import build_archive
        build_archive(str(album_id), Path(DOWNLOAD_DIR) / ".archives")
        return True, "共享漫画缓存已就绪"
    except Exception as exc:
        return False, f"下载失败: {exc}"
'''
    lines=text.splitlines(keepends=True)
    updated=''.join(lines[:function.lineno-1])+replacement+''.join(lines[function.end_lineno:])
    ast.parse(updated)
    backup=path.with_suffix('.py.before-shared-cache-'+str(int(time.time())))
    shutil.copy2(path,backup)
    backup.chmod(0o600)
    staged=path.with_suffix('.py.next')
    staged.write_text(updated,encoding='utf-8')
    staged.replace(path)
    print('legacy downloader now uses shared cache')


if __name__=='__main__':
    upgrade(sys.argv[1])
