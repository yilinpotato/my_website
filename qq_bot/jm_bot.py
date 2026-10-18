"""
QQ 机器人服务 - JM漫画查看功能
支持通过QQ机器人发送漫画ID，获取漫画前3张图片预览和网页链接
"""
import os
import sys
import json
import asyncio
import aiohttp
import hashlib
import hmac
import time
import traceback
import shutil
from urllib.parse import quote
from pathlib import Path
from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
)
from threading import Thread, Lock
import logging
from typing import Optional

load_dotenv(Path(__file__).with_name(".env"))

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ======================= 配置区域 =======================
# QQ机器人配置 (从图片中获取)
QQ_APP_ID = os.getenv("QQ_APP_ID", "")
QQ_APP_SECRET = os.getenv("QQ_APP_SECRET", "")
QQ_TOKEN = os.getenv("QQ_TOKEN", "")
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5003
# 你的服务器域名
SERVER_DOMAIN = "https://potatoma.me/jm"  # 部署后的完整域名

# JM漫画下载配置（优先与网页端共用下载目录）
DEFAULT_WEB_DOWNLOAD_DIR = Path(__file__).resolve().parents[1] / "new" / "myproject" / "static" / "manga"
ENV_DOWNLOAD_DIR = os.getenv("JM_DOWNLOAD_DIR")
if ENV_DOWNLOAD_DIR:
    DOWNLOAD_DIR = Path(ENV_DOWNLOAD_DIR)
elif DEFAULT_WEB_DOWNLOAD_DIR.exists():
    DOWNLOAD_DIR = DEFAULT_WEB_DOWNLOAD_DIR
else:
    DOWNLOAD_DIR = Path(__file__).parent / "jm_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ======================= Flask 应用 =======================
app = Flask(__name__)
JM_VIEWER_PASSWORD = os.getenv("JM_VIEWER_PASSWORD", "")
JM_SESSION_SECRET = os.getenv("JM_SESSION_SECRET", "")
JM_URL_PREFIX = os.getenv("JM_URL_PREFIX", "").rstrip("/")
if not JM_VIEWER_PASSWORD or not JM_SESSION_SECRET:
    raise RuntimeError("JM_VIEWER_PASSWORD and JM_SESSION_SECRET must be configured")

app.config.update(
    SECRET_KEY=JM_SESSION_SECRET,
    SESSION_COOKIE_NAME="jm_viewer_session",
    SESSION_COOKIE_PATH=JM_URL_PREFIX or "/",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
)


# 模拟 Redis（本地测试用）
class MockRedis:
    def __init__(self):
        self._data = {}
    def set(self, key, value, ex=None): self._data[key] = value; return True
    def get(self, key): return self._data.get(key)
    def delete(self, key): return self._data.pop(key, None)
    def exists(self, key): return key in self._data

cache = MockRedis()

# 下载进度缓存
download_status = {}
download_status_lock = Lock()
STATUS_DIR = DOWNLOAD_DIR / ".status"
STATUS_DIR.mkdir(parents=True, exist_ok=True)

def _status_path(album_id: str) -> Path:
    return STATUS_DIR / f"{album_id}.json"

def set_download_status(album_id: str, status: dict) -> None:
    with download_status_lock:
        download_status[album_id] = status
        try:
            _status_path(album_id).write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入下载状态失败: {e}")

def get_download_status(album_id: str) -> dict:
    with download_status_lock:
        status = download_status.get(album_id)
    if status:
        return status
    # 跨进程读取
    try:
        path = _status_path(album_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取下载状态失败: {e}")
    return {"state": "idle", "progress": 0, "message": "等待开始"}

# ======================= JMComic 下载功能 =======================
def find_album_images(album_id: str, title_hint: str | None = None, time_hint: float | None = None) -> tuple:
    """
    查找已下载的漫画图片
    返回: (images_list, album_dir, title)
    """
    marker_name = ".album_id"

    def has_album_marker(directory: Path) -> bool:
        marker_path = directory / marker_name
        if not marker_path.exists():
            return False
        try:
            return marker_path.read_text(encoding="utf-8").strip() == str(album_id)
        except Exception:
            return False

    # 支持的图片格式
    image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.webp', '*.gif']
    
    def get_images_from_dir(directory: Path) -> list:
        images = []
        for ext in image_extensions:
            images.extend(directory.rglob(ext))
        # 按文件名数字排序
        return sorted(images, key=lambda x: (len(x.stem), x.stem))
    
    # 方法1: 直接按ID查找
    album_dir = DOWNLOAD_DIR / album_id
    if album_dir.exists():
        images = get_images_from_dir(album_dir)
        if images:
            return images, album_dir, f"JM{album_id}"
    
    # 方法2: 查找包含ID的目录
    for subdir in DOWNLOAD_DIR.iterdir():
        if subdir.is_dir() and (album_id in subdir.name or has_album_marker(subdir)):
            images = get_images_from_dir(subdir)
            if images:
                return images, subdir, subdir.name

    # 方法3: 根据标题提示 + 时间窗口聚合多章节目录
    candidate_dirs = []
    base_title = None
    if title_hint:
        base_title = title_hint.split()[0].strip()
    for subdir in DOWNLOAD_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if time_hint is not None and subdir.stat().st_mtime < time_hint - 2:
            continue
        name = subdir.name
        if title_hint and (title_hint in name or name in title_hint):
            candidate_dirs.append(subdir)
        elif base_title and base_title in name:
            candidate_dirs.append(subdir)

    if candidate_dirs:
        all_images = []
        for subdir in candidate_dirs:
            all_images.extend(get_images_from_dir(subdir))
        if all_images:
            return all_images, candidate_dirs[0], title_hint or candidate_dirs[0].name

    return [], None, None

def normalize_album_dirs(album_id: str, title_hint: str | None = None, time_hint: float | None = None) -> Path:
    """将多章节目录归档到同一 album 目录下"""
    album_root = DOWNLOAD_DIR / album_id
    album_root.mkdir(parents=True, exist_ok=True)

    candidate_dirs = []
    base_title = None
    if title_hint:
        base_title = title_hint.split()[0].strip()

    for subdir in DOWNLOAD_DIR.iterdir():
        if not subdir.is_dir() or subdir == album_root:
            continue
        marker_path = subdir / ".album_id"
        has_marker = marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == str(album_id)
        if has_marker:
            candidate_dirs.append(subdir)
            continue
        if time_hint is not None and subdir.stat().st_mtime < time_hint - 2:
            continue
        name = subdir.name
        if title_hint and (title_hint in name or name in title_hint):
            candidate_dirs.append(subdir)
        elif base_title and base_title in name:
            candidate_dirs.append(subdir)

    for subdir in candidate_dirs:
        dest = album_root / subdir.name
        if dest.exists():
            continue
        try:
            shutil.move(str(subdir), str(dest))
        except Exception as e:
            logger.warning(f"移动章节目录失败: {subdir} -> {dest}, error={e}")

    return album_root

def get_album_chapters(album_id: str, title_hint: str | None = None, time_hint: float | None = None) -> list:
    """返回章节列表，每个章节包含名称与图片列表"""
    album_root = DOWNLOAD_DIR / album_id
    if not album_root.exists() and (title_hint or time_hint):
        album_root = normalize_album_dirs(album_id, title_hint=title_hint, time_hint=time_hint)

    # 章节为子目录
    if album_root.exists():
        chapter_dirs = [d for d in album_root.iterdir() if d.is_dir()]
        chapter_dirs.sort(key=lambda d: d.name)
        chapters = []
        for d in chapter_dirs:
            images = []
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.webp', '*.gif']:
                images.extend(d.rglob(ext))
            images = sorted(images, key=lambda x: (len(x.stem), x.stem))
            if images:
                chapters.append({"name": d.name, "key": d.name, "images": images})
        if chapters:
            return chapters

        # 兼容：图片直接在 album_root 下
        images = []
        for ext in ['*.jpg', '*.jpeg', '*.png', '*.webp', '*.gif']:
            images.extend(album_root.rglob(ext))
        images = sorted(images, key=lambda x: (len(x.stem), x.stem))
        if images:
            return [{"name": "第1章", "key": "__root__", "images": images}]

    # 回退：尝试从分散目录聚合
    images, album_dir, title = find_album_images(album_id, title_hint=title_hint, time_hint=time_hint)
    if images:
        return [{"name": title or "第1章", "key": album_dir.name if album_dir else "__root__", "images": images}]

    return []

def get_album_detail_info(album_id: str) -> tuple[str | None, list]:
    """获取专辑标题与章节列表"""
    try:
        import jmcomic
        option = jmcomic.JmOption.default()
        try:
            option.download.ignore_photo_error = True
        except Exception:
            pass
        client = option.build_jm_client()
        album = client.get_album_detail(album_id)
        title = getattr(album, 'title', None)
        return title, album.episode_list or []
    except Exception as e:
        logger.warning(f"获取章节列表失败: {e}")
        return None, []

def build_preview_urls(album_id: str, image_paths: list) -> list:
    """根据已下载图片路径生成预览 URL（优先按章节路径）"""
    if not image_paths:
        return []

    chapters = get_album_chapters(album_id)
    if chapters:
        chapter_key = quote(chapters[0]["key"])
        urls = []
        for img_path in chapters[0]["images"][:3]:
            img_name = quote(os.path.basename(img_path))
            urls.append(f"{SERVER_DOMAIN}/images/{album_id}/{chapter_key}/{img_name}")
        if urls:
            return urls

    urls = []
    for img_path in image_paths[:3]:
        img_name = quote(os.path.basename(img_path))
        urls.append(f"{SERVER_DOMAIN}/images/{album_id}/{img_name}")
    return urls

def get_preview_payload(album_id: str) -> dict:
    """复用网页逻辑，返回预览数据"""
    result = download_jm_album(album_id, max_images=3)

    if result.get("success"):
        preview_urls = build_preview_urls(album_id, result.get("images", []))
        return {
            "success": True,
            "album_id": album_id,
            "title": result.get("title", f"JM{album_id}"),
            "preview_images": preview_urls,
            "total_pages": len(result.get("all_images", [])),
            "view_url": f"{SERVER_DOMAIN}/view/{album_id}",
            "cached": result.get("cached", False)
        }

    return {
        "success": False,
        "error": result.get("error", "未知错误")
    }

def download_album_chapters(album_id: str, limit: int = 5, offset: int = 0, update_status: bool = False) -> dict:
    """仅下载指定区间的章节"""
    try:
        import jmcomic

        option = jmcomic.JmOption.default()
        # 降低并发，避免内存爆
        try:
            if hasattr(option, "download") and hasattr(option.download, "thread_count"):
                option.download.thread_count = int(os.getenv("JM_THREAD_COUNT", "2"))
        except Exception:
            pass
        option.dir_rule.base_dir = str(DOWNLOAD_DIR)
        album_title, episodes = get_album_detail_info(album_id)
        album_title = album_title or f"JM{album_id}"
        total = len(episodes)

        slice_eps = episodes[offset:offset + limit] if limit else episodes[offset:]
        total_slice = len(slice_eps)

        if update_status:
            set_download_status(album_id, {
                "state": "downloading",
                "progress": 5,
                "message": f"准备下载章节 1/{max(total_slice,1)}...",
                "current": 0,
                "total": total_slice
            })

        for idx, (photo_id, _episode, _title) in enumerate(slice_eps, start=1):
            # 预创建章节目录，避免多线程下载时目录未生成导致保存失败
            for name in {_title, _episode}:
                if name:
                    try:
                        (DOWNLOAD_DIR / str(name)).mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        logger.warning(f"创建章节目录失败: {name}, error={e}")
            if update_status:
                progress = int(5 + (idx - 1) / max(total_slice, 1) * 85)
                set_download_status(album_id, {
                    "state": "downloading",
                    "progress": progress,
                    "message": f"下载第 {idx}/{total_slice} 章：{_title or _episode}",
                    "current": idx - 1,
                    "total": total_slice
                })
            jmcomic.download_photo(photo_id, option)

        if update_status:
            set_download_status(album_id, {
                "state": "downloading",
                "progress": 95,
                "message": "整理章节...",
                "current": total_slice,
                "total": total_slice
            })

        normalize_album_dirs(album_id, title_hint=album_title, time_hint=None)
        chapters = get_album_chapters(album_id, title_hint=album_title, time_hint=time.time())
        images = [img for ch in chapters for img in ch["images"]] if chapters else []

        result = {
            "success": True,
            "images": [str(img) for img in images[:3]],
            "all_images": [str(img) for img in images],
            "title": album_title,
            "album_id": album_id,
            "total_chapters": total
        }
        if update_status:
            set_download_status(album_id, {
                "state": "done",
                "progress": 100,
                "message": "下载完成",
                "current": total_slice,
                "total": total_slice
            })
        return result
    except Exception as e:
        logger.error(f"分批下载失败: {e}")
        traceback.print_exc()
        return {"success": False, "error": str(e)}

def download_jm_album(album_id: str, max_images: int = 3) -> dict:
    """
    下载JM漫画并返回图片路径
    返回: {"success": bool, "images": [路径列表], "title": 标题, "error": 错误信息}
    """
    try:
        # 先检查是否已下载
        chapters = get_album_chapters(album_id)
        if chapters:
            images = [img for ch in chapters for img in ch["images"]]
            title = chapters[0]["name"] if chapters else f"JM{album_id}"
            return {
                "success": True,
                "images": [str(img) for img in images[:max_images]],
                "all_images": [str(img) for img in images],
                "title": title or f"JM{album_id}",
                "album_id": album_id,
                "album_dir": None,
                "cached": True
            }

        # 仅下载前 5 章
        logger.info(f"开始下载漫画前 5 章: JM{album_id}")
        result = download_album_chapters(album_id, limit=5, offset=0)
        if not result.get("success"):
            return result

        album_title = result.get("title", f"JM{album_id}")
        chapters = get_album_chapters(album_id, title_hint=album_title, time_hint=None)
        images = [img for ch in chapters for img in ch["images"]] if chapters else []

        if images:
            return {
                "success": True,
                "images": [str(img) for img in images[:max_images]],
                "all_images": [str(img) for img in images],
                "title": album_title,
                "album_id": album_id,
                "album_dir": None,
                "cached": False
            }

        dir_contents = list(DOWNLOAD_DIR.iterdir())
        logger.error(f"下载目录内容: {[d.name for d in dir_contents]}")
        return {"success": False, "error": f"下载完成但未找到图片文件，目录内容: {[d.name for d in dir_contents]}"}

    except Exception as e:
        logger.error(f"下载漫画失败: {e}")
        traceback.print_exc()
        return {"success": False, "error": str(e)}

# ======================= QQ 机器人 API =======================
try:
    from .qq_errors import read_api_response
except ImportError:
    from qq_errors import read_api_response

class QQBotAPI:
    """QQ开放平台机器人API"""
    
    SANDBOX_API = "https://sandbox.api.sgroup.qq.com"
    PRODUCTION_API = "https://api.sgroup.qq.com"
    
    def __init__(self, app_id: str, app_secret: str, token: str, sandbox: bool = False):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = token
        self.base_url = self.SANDBOX_API if sandbox else self.PRODUCTION_API
        self.access_token = None
        self.token_expires = 0
    
    async def get_access_token(self) -> str:
        """获取访问令牌"""
        if self.access_token and time.time() < self.token_expires:
            return self.access_token
        
        url = "https://bots.qq.com/app/getAppAccessToken"
        data = {
            "appId": self.app_id,
            "clientSecret": self.app_secret
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as resp:
                result = await read_api_response(resp, url, (self.app_secret, self.access_token))
                if "access_token" in result:
                    self.access_token = result["access_token"]
                    self.token_expires = time.time() + int(result.get("expires_in", 7200)) - 60
                    return self.access_token
                else:
                    raise Exception(f"获取access_token失败: {result}")
    
    async def send_group_message(self, group_openid: str, content: str, msg_id: Optional[str] = None, msg_seq: Optional[int] = None, media_file_info: Optional[str] = None):
        """发送群消息"""
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/groups/{group_openid}/messages"
        
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json"
        }
        
        data = {
            "content": content,
            "msg_type": 0  # 文本消息
        }
        
        if msg_id:
            data["msg_id"] = msg_id
        if msg_seq:
            data["msg_seq"] = msg_seq
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await read_api_response(resp, url, (self.app_secret, self.access_token))
                if resp.status >= 400 or result.get("code", 0):
                    raise RuntimeError(f"QQ 发送失败，HTTP {resp.status}，code={result.get('code')}")
                return result

    async def send_c2c_message(self, user_openid: str, content: str, msg_id: Optional[str] = None, msg_seq: Optional[int] = None):
        """发送私聊消息"""
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/users/{user_openid}/messages"

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json"
        }

        data = {
            "content": content,
            "msg_type": 0
        }

        if msg_id:
            data["msg_id"] = msg_id
        if msg_seq:
            data["msg_seq"] = msg_seq

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as resp:
                result = await read_api_response(resp, url, (self.app_secret, self.access_token))
                if resp.status >= 400 or result.get("code", 0):
                    raise RuntimeError(f"QQ 发送失败，HTTP {resp.status}，code={result.get('code')}")
                return result
    
    async def upload_group_media(self, group_openid: str, file_type: int, url: str):
        """上传群媒体文件
        file_type: 1-图片, 2-视频, 3-语音, 4-文件
        """
        token = await self.get_access_token()
        api_url = f"{self.base_url}/v2/groups/{group_openid}/files"
        
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json"
        }
        
        data = {
            "file_type": file_type,
            "url": url,
            "srv_send_msg": False
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers=headers, json=data) as resp:
                return await resp.json()

    async def upload_c2c_media(self, user_openid: str, file_type: int, url: str):
        """上传私聊媒体文件"""
        token = await self.get_access_token()
        api_url = f"{self.base_url}/v2/users/{user_openid}/files"

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json"
        }

        data = {
            "file_type": file_type,
            "url": url,
            "srv_send_msg": False
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, headers=headers, json=data) as resp:
                return await resp.json()
    
    async def send_group_rich_message(self, group_openid: str, content: str, msg_id: str, image_urls: Optional[list[str]] = None):
        """发送带图片的群消息"""
        token = await self.get_access_token()
        
        # 如果有图片，先上传
        if image_urls:
            for img_url in image_urls[:3]:  # 最多3张
                try:
                    media_result = await self.upload_group_media(group_openid, 1, img_url)
                    logger.info(f"上传图片结果: {media_result}")
                except Exception as e:
                    logger.error(f"上传图片失败: {e}")
        
        # 发送文本消息
        return await self.send_group_message(group_openid, content, msg_id)

    async def send_c2c_rich_message(self, user_openid: str, content: str, msg_id: str, image_urls: Optional[list[str]] = None):
        """发送带图片的私聊消息"""
        if image_urls:
            for img_url in image_urls[:3]:
                try:
                    media_result = await self.upload_c2c_media(user_openid, 1, img_url)
                    logger.info(f"上传私聊图片结果: {media_result}")
                except Exception as e:
                    logger.error(f"上传私聊图片失败: {e}")

        return await self.send_c2c_message(user_openid, content, msg_id)

# 初始化机器人API
bot_api = QQBotAPI(QQ_APP_ID, QQ_APP_SECRET, QQ_TOKEN)

# ======================= Flask 路由 =======================

AUTH_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>漫画查看器 · 访问验证</title>
    <style>
        * { box-sizing: border-box; }
        body {
            min-height: 100vh; margin: 0; padding: 24px;
            display: grid; place-items: center;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
            color: #eef2ff; background: linear-gradient(145deg, #111827, #1e1b4b);
        }
        .auth-card {
            width: min(420px, 100%); padding: 36px;
            border: 1px solid rgba(255,255,255,.12); border-radius: 20px;
            background: rgba(255,255,255,.08); box-shadow: 0 24px 70px rgba(0,0,0,.34);
            backdrop-filter: blur(14px);
        }
        h1 { margin: 0 0 10px; font-size: 28px; }
        p { margin: 0 0 24px; color: #c7d2fe; line-height: 1.6; }
        label { display: block; margin-bottom: 8px; font-weight: 600; }
        input {
            width: 100%; padding: 13px 14px; border: 1px solid #64748b;
            border-radius: 10px; background: #fff; color: #111827; font-size: 16px;
        }
        input:focus { outline: 3px solid rgba(129,140,248,.32); border-color: #818cf8; }
        button {
            width: 100%; margin-top: 16px; padding: 13px; border: 0; border-radius: 10px;
            background: #6366f1; color: #fff; font-size: 16px; font-weight: 700; cursor: pointer;
        }
        button:hover { background: #4f46e5; }
        .error { margin: 14px 0 0; color: #fecaca; }
        .back { display: inline-block; margin-top: 20px; color: #c7d2fe; text-decoration: none; }
    </style>
</head>
<body>
    <main class="auth-card">
        <h1>漫画查看器</h1>
        <p>请输入访问密码后继续。</p>
        <form method="post">
            <input type="hidden" name="next" value="{{ next_path }}">
            <label for="password">访问密码</label>
            <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
            <button type="submit">进入查看器</button>
        </form>
        {% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}
        <a class="back" href="/">← 返回项目主页</a>
    </main>
</body>
</html>
"""


def _public_path(internal_path: str) -> str:
    if not internal_path.startswith("/") or internal_path.startswith("//"):
        internal_path = "/"
    return f"{JM_URL_PREFIX}{internal_path}"


@app.before_request
def require_viewer_password():
    if request.endpoint in {"jm_auth", "qq_callback", "archive_download"}:
        return None
    if session.get("jm_viewer_authorized"):
        return None

    next_path = request.full_path.rstrip("?")
    auth_url = f"{_public_path('/auth')}?next={quote(next_path, safe='')}"
    return redirect(auth_url)


@app.route('/auth', methods=['GET', 'POST'])
def jm_auth():
    next_path = request.values.get("next", "/")
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"

    error = None
    status = 200
    if request.method == "POST":
        supplied_password = request.form.get("password", "")
        if hmac.compare_digest(supplied_password, JM_VIEWER_PASSWORD):
            session["jm_viewer_authorized"] = True
            return redirect(_public_path(next_path))
        error = "密码错误，请重新输入。"
        status = 401

    return render_template_string(
        AUTH_PAGE, next_path=next_path, error=error
    ), status

@app.route('/')
def index():
    """首页 - 显示漫画查看功能说明"""
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN" data-jm-authorized="true">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>漫画查看器</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                min-height: 100vh;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                color: #fff;
                padding: 20px;
            }
            .container { max-width: 800px; margin: 0 auto; }
            h1 { text-align: center; margin-bottom: 30px; font-size: 2rem; }
            .card {
                background: rgba(255,255,255,0.1);
                border-radius: 15px;
                padding: 25px;
                margin-bottom: 20px;
                backdrop-filter: blur(10px);
            }
            .card h2 { margin-bottom: 15px; color: #00d4ff; }
            .card p { line-height: 1.8; margin-bottom: 10px; }
            code {
                background: rgba(0,212,255,0.2);
                padding: 2px 8px;
                border-radius: 4px;
                font-family: monospace;
            }
            .search-box {
                display: flex;
                gap: 10px;
                margin-top: 20px;
            }
            .search-box input {
                flex: 1;
                padding: 12px 15px;
                border: none;
                border-radius: 8px;
                font-size: 16px;
            }
            .search-box button {
                padding: 12px 25px;
                background: linear-gradient(135deg, #00d4ff, #0099cc);
                border: none;
                border-radius: 8px;
                color: #fff;
                font-size: 16px;
                cursor: pointer;
                transition: transform 0.2s;
            }
            .search-box button:hover { transform: scale(1.05); }
            .footer { text-align: center; margin-top: 40px; opacity: 0.7; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📚 漫画查看器</h1>
            
            <div class="card">
                <h2>🤖 QQ机器人使用方法</h2>
                <p>在QQ群中发送以下命令：</p>
                <p><code>/jm 漫画ID</code> - 获取漫画前3张预览图和完整阅读链接</p>
                <p>例如：<code>/jm 123456</code></p>
            </div>
            
            <div class="card">
                <h2>🔍 网页直接查看</h2>
                <p>在下方输入漫画ID即可在线查看：</p>
                <div class="search-box">
                    <input type="text" id="albumId" placeholder="输入漫画ID，例如：123456">
                    <button onclick="viewAlbum()" id="searchBtn" data-analytics-id="comic-open">查看</button>
                </div>
                <div id="loadingStatus" style="display:none; margin-top:15px; padding:15px; background:rgba(0,212,255,0.1); border-radius:8px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <div class="spinner"></div>
                        <span id="statusText">正在获取漫画信息...</span>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>📖 功能说明</h2>
                <p>• 支持通过QQ机器人快速查看漫画预览</p>
                <p>• 自动下载并缓存漫画图片</p>
                <p>• 提供网页端完整阅读体验</p>
            </div>
            
            <p class="footer">© 2026 漫画查看器</p>
        </div>
        
        <style>
            .spinner {
                width: 20px; height: 20px;
                border: 3px solid rgba(0,212,255,0.3);
                border-top-color: #00d4ff;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            }
            @keyframes spin { to { transform: rotate(360deg); } }
        </style>
        
        <script>
            async function viewAlbum() {
                const id = document.getElementById('albumId').value.trim();
                if (!id) { alert('请输入漫画ID'); return; }
                if (!/^\d{1,16}$/.test(id)) { alert('请输入有效的数字漫画ID'); return; }
                
                const btn = document.getElementById('searchBtn');
                const loading = document.getElementById('loadingStatus');
                const statusText = document.getElementById('statusText');
                
                btn.disabled = true;
                btn.textContent = '获取中...';
                loading.style.display = 'block';
                statusText.textContent = '正在获取漫画信息，首次加载可能需要1-2分钟...';
                
                try {
                    // 使用相对路径，适配 /jm/ 前缀
                    document.dispatchEvent(new CustomEvent('comic:opened', {
                        detail: { comicId: id, source: 'viewer_search' },
                    }));
                    window.location.href = 'view/' + id;
                } catch(e) {
                    statusText.textContent = '获取失败: ' + e.message;
                    btn.disabled = false;
                    btn.textContent = '查看';
                }
            }
            document.getElementById('albumId').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') viewAlbum();
            });
        </script>
    </body>
    </html>
    """
    return html

@app.route('/view/<album_id>')
def view_album(album_id):
    """查看漫画页面"""
    # 先检查是否已下载
    chapters = get_album_chapters(album_id)
    if not chapters:
        # 未缓存时显示进度条页面
        return f"""
        <!DOCTYPE html>
        <html lang=\"zh-CN\">
        <head>
            <meta charset=\"UTF-8\">
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
            <title>正在下载 JM{album_id}...</title>
            <style>
                body {{ background:#1a1a2e; color:#fff; font-family:'Segoe UI','Microsoft YaHei',sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
                .box {{ width:420px; background:rgba(255,255,255,0.05); padding:30px; border-radius:12px; text-align:center; }}
                .bar {{ width:100%; height:12px; background:rgba(255,255,255,0.1); border-radius:8px; overflow:hidden; margin-top:15px; }}
                .bar > div {{ height:100%; width:0; background:linear-gradient(90deg,#00d4ff,#0099cc); transition:width .3s; }}
                .status {{ margin-top:10px; opacity:.85; }}
                a {{ color:#00d4ff; text-decoration:none; }}
            </style>
        </head>
        <body>
            <div class=\"box\">
                <h2>📥 正在下载 JM{album_id}</h2>
                <div class=\"bar\"><div id=\"progressBar\"></div></div>
                <div class=\"status\" id=\"statusText\">准备开始下载...</div>
                <p style=\"margin-top:15px;\"><a href=\"../\">返回首页</a></p>
            </div>
            <script>
                const albumId = "{album_id}";
                async function startDownload() {{
                    await fetch(`../api/download/${{albumId}}`, {{ method: 'POST' }});
                    pollStatus();
                }}
                async function pollStatus() {{
                    try {{
                        const res = await fetch(`../api/download_status/${{albumId}}`);
                        const data = await res.json();
                        const bar = document.getElementById('progressBar');
                        const text = document.getElementById('statusText');
                        bar.style.width = (data.progress || 0) + '%';
                        text.textContent = data.message || '下载中...';
                        if (data.state === 'done') {{
                            window.location.href = `./${{albumId}}`;
                            return;
                        }}
                        if (data.state === 'error') {{
                            text.textContent = data.message || '下载失败';
                            return;
                        }}
                    }} catch (e) {{
                        // ignore
                    }}
                    setTimeout(pollStatus, 1500);
                }}
                startDownload();
            </script>
        </body>
        </html>
        """

    album_title, episode_list = get_album_detail_info(album_id)
    total_chapters = len(episode_list) if episode_list else len(chapters)
    loaded_chapters = len(chapters)
    if album_title:
        normalize_album_dirs(album_id, title_hint=album_title, time_hint=None)
        chapters = get_album_chapters(album_id, title_hint=album_title, time_hint=None)
    try:
        visible_count = int(request.args.get('count', 5))
    except ValueError:
        visible_count = 5
    visible_count = max(1, min(visible_count, loaded_chapters))

    try:
        chapter_index = int(request.args.get('ch', 0))
    except ValueError:
        chapter_index = 0
    if chapter_index < 0:
        chapter_index = 0
    if chapter_index >= visible_count:
        chapter_index = visible_count - 1

    visible_chapters = chapters[:visible_count]
    current_chapter = visible_chapters[chapter_index]
    chapter_key = quote(current_chapter["key"])

    # 生成图片列表HTML - 使用相对路径（适配 /jm/ 前缀）
    images_html = ""
    for i, img_path in enumerate(current_chapter["images"]):
        img_path = Path(img_path)
        img_name = quote(img_path.name)
        images_html += f'<img src="../images/{album_id}/{chapter_key}/{img_name}" alt="第{i+1}页" loading="lazy" onerror="this.style.display=\'none\'">'

    options_html = ""
    for idx, ch in enumerate(visible_chapters):
        selected = "selected" if idx == chapter_index else ""
        options_html += f'<option value="{idx}" {selected}>{idx+1}. {ch["name"]}</option>'
    
    html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{current_chapter["name"] or ('JM' + album_id)} - 漫画查看器</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                background: #1a1a2e;
                color: #fff;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            }}
            .header {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                background: rgba(26,26,46,0.95);
                padding: 15px 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                z-index: 100;
                backdrop-filter: blur(10px);
            }}
            .header h1 {{ font-size: 1.2rem; }}
            .header a {{
                color: #00d4ff;
                text-decoration: none;
                padding: 8px 15px;
                background: rgba(0,212,255,0.2);
                border-radius: 5px;
            }}
            .gallery {{
                max-width: 1000px;
                margin: 80px auto 20px;
                padding: 20px;
            }}
            .gallery img {{
                width: 100%;
                display: block;
                margin-bottom: 5px;
                border-radius: 5px;
            }}
            .chapter-nav {{
                max-width: 1000px;
                margin: 80px auto 10px;
                padding: 0 20px;
                display: flex;
                gap: 10px;
                align-items: center;
                flex-wrap: wrap;
            }}
            .chapter-nav select, .chapter-nav button {{
                padding: 8px 12px;
                border-radius: 6px;
                border: none;
                background: rgba(0,212,255,0.2);
                color: #fff;
                cursor: pointer;
            }}
            .chapter-nav button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
            .load-more {{
                text-align: center;
                margin: 20px auto 40px;
            }}
            .load-more button {{
                padding: 10px 16px;
                border-radius: 6px;
                border: none;
                background: linear-gradient(135deg,#00d4ff,#0099cc);
                color: #fff;
                cursor: pointer;
            }}
            .overlay {{
                position: fixed;
                inset: 0;
                background: rgba(0,0,0,0.55);
                display: none;
                align-items: center;
                justify-content: center;
                z-index: 200;
            }}
            .overlay .box {{
                width: 420px;
                background: rgba(255,255,255,0.06);
                padding: 24px;
                border-radius: 12px;
                text-align: center;
            }}
            .overlay .bar {{
                width:100%; height:12px; background:rgba(255,255,255,0.1);
                border-radius:8px; overflow:hidden; margin-top:12px;
            }}
            .overlay .bar > div {{
                height:100%; width:0; background:linear-gradient(90deg,#00d4ff,#0099cc); transition:width .3s;
            }}
            .page-count {{
                text-align: center;
                padding: 20px;
                opacity: 0.7;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📖 {current_chapter["name"] or ('JM' + album_id)}</h1>
            <a href="../">返回首页</a>
        </div>
        <div class="chapter-nav">
            <button id="prevBtn" onclick="goChapter(-1)" {"disabled" if chapter_index == 0 else ""}>上一章</button>
            <select id="chapterSelect" onchange="onSelectChange()">
                {options_html}
            </select>
            <button id="nextBtn" onclick="goChapter(1)" {"disabled" if chapter_index == visible_count - 1 else ""}>下一章</button>
            <span style="opacity:.7;">已加载 {visible_count}/{total_chapters} 章</span>
        </div>
        <div class="gallery">
            {images_html}
        </div>
        <div class="page-count">当前章节 {len(current_chapter["images"])} 页</div>
        <div class="load-more" style="display: {"none" if visible_count >= total_chapters else "block"};">
            <button id="loadMoreBtn" onclick="loadMore()">继续下载后续 5 章</button>
        </div>
        <div class="overlay" id="progressOverlay">
            <div class="box">
                <h3>正在下载更多章节</h3>
                <div class="bar"><div id="overlayBar"></div></div>
                <div style="margin-top:10px;opacity:.8;" id="overlayText">准备开始...</div>
            </div>
        </div>
        <script>
            const albumId = "{album_id}";
            let chapterIndex = {chapter_index};
            let visibleCount = {visible_count};

            function goChapter(step) {{
                const next = chapterIndex + step;
                if (next < 0 || next >= visibleCount) return;
                window.location.href = `?ch=${{next}}&count=${{visibleCount}}`;
            }}
            function onSelectChange() {{
                const sel = document.getElementById('chapterSelect');
                const idx = parseInt(sel.value, 10);
                window.location.href = `?ch=${{idx}}&count=${{visibleCount}}`;
            }}
            async function pollMoreStatus() {{
                try {{
                    const res = await fetch(`../api/download_status/${{albumId}}`);
                    const data = await res.json();
                    const bar = document.getElementById('overlayBar');
                    const text = document.getElementById('overlayText');
                    bar.style.width = (data.progress || 0) + '%';
                    text.textContent = data.message || '下载中...';
                    if (data.state === 'done') {{
                        const nextCount = visibleCount + 5;
                        window.location.href = `?ch=${{chapterIndex}}&count=${{nextCount}}`;
                        return;
                    }}
                    if (data.state === 'error') {{
                        text.textContent = data.message || '下载失败';
                        return;
                    }}
                }} catch (e) {{
                    // ignore
                }}
                setTimeout(pollMoreStatus, 1500);
            }}
            async function loadMore() {{
                const btn = document.getElementById('loadMoreBtn');
                const overlay = document.getElementById('progressOverlay');
                btn.disabled = true;
                btn.textContent = '下载中...';
                overlay.style.display = 'flex';
                try {{
                    await fetch(`../api/download_more/${{albumId}}?limit=5&offset=${{visibleCount}}`, {{ method: 'POST' }});
                }} catch (e) {{
                    // ignore
                }}
                pollMoreStatus();
            }}
        </script>
    </body>
    </html>
    """
    return html

@app.route('/api/download/<album_id>', methods=['POST'])
def api_download(album_id):
    """触发后台下载任务"""
    status = get_download_status(album_id)
    if status.get("state") in {"downloading", "done"}:
        return jsonify({"success": True, **status})

    def worker():
        try:
            set_download_status(album_id, {"state": "downloading", "progress": 10, "message": "开始下载..."})
            result = download_album_chapters(album_id, limit=5, offset=0, update_status=True)
            if result.get("success"):
                set_download_status(album_id, {"state": "done", "progress": 100, "message": "下载完成，正在加载..."})
            else:
                set_download_status(album_id, {"state": "error", "progress": 0, "message": result.get("error", "下载失败")})
        except Exception as e:
            set_download_status(album_id, {"state": "error", "progress": 0, "message": str(e)})

    set_download_status(album_id, {"state": "queued", "progress": 5, "message": "进入队列..."})
    Thread(target=worker, daemon=True).start()
    return jsonify({"success": True, **get_download_status(album_id)})

@app.route('/api/download_status/<album_id>')
def api_download_status(album_id):
    """获取下载进度"""
    return jsonify(get_download_status(album_id))

@app.route('/api/download_more/<album_id>', methods=['POST'])
def api_download_more(album_id):
    """继续下载后续章节"""
    limit = request.args.get('limit', '5')
    offset = request.args.get('offset', '0')
    try:
        limit = int(limit)
    except ValueError:
        limit = 5
    try:
        offset = int(offset)
    except ValueError:
        offset = 0

    status = get_download_status(album_id)
    if status.get("state") == "downloading":
        return jsonify({"success": True, **status})

    def worker():
        try:
            set_download_status(album_id, {"state": "downloading", "progress": 10, "message": f"下载后续 {limit} 章..."})
            result = download_album_chapters(album_id, limit=limit, offset=offset, update_status=True)
            if result.get("success"):
                set_download_status(album_id, {"state": "done", "progress": 100, "message": "下载完成"})
            else:
                set_download_status(album_id, {"state": "error", "progress": 0, "message": result.get("error", "下载失败")})
        except Exception as e:
            set_download_status(album_id, {"state": "error", "progress": 0, "message": str(e)})

    Thread(target=worker, daemon=True).start()
    return jsonify({"success": True, "message": f"开始下载后续 {limit} 章"})

@app.route('/images/<album_id>/<chapter>/<path:filename>')
def serve_image(album_id, chapter, filename):
    """提供图片文件（按章节）"""
    album_dir = DOWNLOAD_DIR / album_id
    if chapter == "__root__":
        target_dir = album_dir
    else:
        target_dir = album_dir / chapter

    if target_dir.exists():
        return send_from_directory(target_dir, filename)

    # 兼容：目录不在 album_id 下（如 350237_xxx）
    direct_dir = DOWNLOAD_DIR / chapter
    if direct_dir.exists():
        return send_from_directory(direct_dir, filename)

    # 兜底：按 album_id 搜索目录
    try:
        for subdir in DOWNLOAD_DIR.iterdir():
            if not subdir.is_dir():
                continue
            marker_path = subdir / ".album_id"
            has_marker = marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == str(album_id)
            if has_marker or album_id in subdir.name:
                return send_from_directory(subdir, filename)
    except Exception as e:
        logger.warning(f"图片目录兜底查找失败: {e}")

    # 回退：在下载目录中按章节名查找
    for subdir in DOWNLOAD_DIR.iterdir():
        if subdir.is_dir() and subdir.name == chapter:
            return send_from_directory(subdir, filename)

    # 回退：在 album 目录中搜索
    if album_dir.exists():
        for img_file in album_dir.rglob(filename):
            return send_from_directory(img_file.parent, img_file.name)

    # 回退：在下载根目录中搜索
    for subdir in DOWNLOAD_DIR.iterdir():
        if subdir.is_dir():
            for img_file in subdir.rglob(filename):
                return send_from_directory(img_file.parent, img_file.name)

    logger.error(f"图片未找到: album_id={album_id}, chapter={chapter}, filename={filename}")
    return "Image not found", 404

@app.route('/images/<album_id>/<path:filename>')
def serve_image_legacy(album_id, filename):
    """兼容旧版图片路径"""
    album_dir = DOWNLOAD_DIR / album_id
    if album_dir.exists():
        for img_file in album_dir.rglob(filename):
            return send_from_directory(img_file.parent, img_file.name)

    for subdir in DOWNLOAD_DIR.iterdir():
        if subdir.is_dir() and album_id in subdir.name:
            for img_file in subdir.rglob(filename):
                return send_from_directory(img_file.parent, img_file.name)

    logger.error(f"图片未找到: album_id={album_id}, filename={filename}")
    return "Image not found", 404

@app.route('/api/preview/<album_id>')
def api_preview(album_id):
    """API: 获取漫画预览信息"""
    payload = get_preview_payload(album_id)
    return jsonify(payload)

# ======================= QQ 机器人 Webhook =======================
from qq_service import install
try:
    from .web_cache import install_web_cache
except ImportError:
    from web_cache import install_web_cache
shared_manga_cache = install_web_cache(globals())

qq_executor = install(app, bot_api, QQ_APP_SECRET, SERVER_DOMAIN, DOWNLOAD_DIR / ".archives")

# ======================= 启动服务 =======================

if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════════════════════════╗
║              JM漫画查看器 + QQ机器人服务                       ║
╠══════════════════════════════════════════════════════════════╣
║  服务地址:  http://127.0.0.1:5003                             ║
║  QQ回调:    http://你的域名/qq/callback                       ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # 检查jmcomic是否安装
    try:
        import jmcomic
        logger.info(f"jmcomic 版本: {jmcomic.__version__}")
    except ImportError:
        logger.warning("jmcomic 未安装，请运行: pip install jmcomic")
    
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False, threaded=True)
