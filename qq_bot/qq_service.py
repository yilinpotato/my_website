"""QQ 官方回调、有限后台队列和签名 ZIP 领取链接。"""
import asyncio
import json
import time
import sqlite3
import aiohttp
import hashlib
import re
import base64
import zipfile
from io import BytesIO
from PIL import Image
from datetime import timedelta
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, BoundedSemaphore

from flask import jsonify, request, send_file, render_template_string, session as viewer_session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from nacl.signing import SigningKey
from nacl.exceptions import BadSignatureError
from werkzeug.wsgi import ClosingIterator

LOGIN_HINT = "网页登录密码：EXAMPLE_VIEWER_PASSWORD\n同一浏览器登录后会记住登录状态（有效期 365 天，访问自动续期）。"
HELP = "help：查看帮助\nweb 123456：阅读链接\nzip 123456：发送 ZIP\nimg 123456：发送前 3 张图片\nimg 123456 4：从第 4 页继续\n直接发送漫画号也可获取阅读链接。\n" + LOGIN_HINT


def parse_command(content):
    content = re.sub(r"<@!?[^>]+>", "", content).strip()
    content = re.sub(r"^(?:搜索|/search|search)\s*", "", content, flags=re.I)
    album = parse_album_id(content)
    if album:
        return album, "web", 1
    content = re.sub(r"^/jm\s+", "", content, flags=re.I)
    mode_pattern = r"(只发网页|只发图片|发压缩包|压缩包|网页|图片|zip|web|img)"
    match = re.fullmatch(mode_pattern + r"\s*([0-9]{1,12})(?:\s+([0-9]{1,6}))?", content, re.I)
    if match:
        mode, album, page = match.groups()
    else:
        match = re.fullmatch(r"([0-9]{1,12})\s*" + mode_pattern + r"(?:\s+([0-9]{1,6}))?", content, re.I)
        if not match:
            return None, None, 1
        album, mode, page = match.groups()
    mode = mode.lower()
    mode = "zip" if mode in ("zip", "压缩包", "发压缩包") else "images" if mode in ("img", "图片", "只发图片") else "web"
    if (page and mode != "images") or (page and int(page) < 1):
        return None, None, 1
    return album, mode, int(page or 1)


async def send_images(api, event, target, msg_id, album_id, archive, page):
    scope = "groups" if event == "GROUP_AT_MESSAGE_CREATE" else "users"
    base = f"{api.base_url}/v2/{scope}/{target}"
    with zipfile.ZipFile(archive) as images:
        names = [i.filename for i in images.infolist() if not i.is_dir()
                 and i.filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))]
        names.sort(key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', n)])
        if not names:
            raise ValueError("压缩包中没有可发送的图片")
        if page < 1 or page > len(names):
            raise ValueError(f"页码超出范围，共 {len(names)} 页")
        token = await api.get_access_token()
        headers = {"Authorization": f"QQBot {token}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
            for seq, name in enumerate(names[page-1:page+2], 1):
                if images.getinfo(name).file_size > 10 * 1024 * 1024:
                    raise ValueError(f"第 {page+seq-1} 页超过本服务单图 10 MiB 上限")
                payload = images.read(name)
                if name.lower().endswith(('.webp', '.gif')):
                    with Image.open(BytesIO(payload)) as source, BytesIO() as output:
                        source.seek(0)
                        source.convert('RGB').save(output, format='JPEG', quality=90)
                        payload = output.getvalue()
                if len(payload) > 10 * 1024 * 1024:
                    raise ValueError(f"第 {page+seq-1} 页转换后超过本服务单图 10 MiB 上限")
                body = {"file_type": 1, "file_data": base64.b64encode(payload).decode('ascii'), "srv_send_msg": False}
                async with session.post(base+'/files', headers=headers, json=body) as response:
                    result = await read_api_response(response, '/files', (token, api.app_secret))
                if not result.get('file_info'):
                    raise ValueError("QQ 未返回图片凭据")
                caption = f"第 {page+seq-1}/{len(names)} 页"
                if seq == 3 and page+3 <= len(names):
                    caption += f"；继续发送：img {album_id} {page+3}"
                async with session.post(base+'/messages', headers=headers, json={
                    'msg_type': 7, 'content': caption, 'msg_id': msg_id, 'msg_seq': seq,
                    'media': {'file_info': result['file_info']},
                }) as response:
                    await read_api_response(response, '/messages', (token, api.app_secret))

try:
    from .archives import build_archive, parse_album_id
    from .qq_errors import QQAPIError, read_api_response, safe_message
    from .manga_cache import MangaCache
except ImportError:
    from archives import build_archive, parse_album_id
    from qq_errors import QQAPIError, read_api_response, safe_message
    from manga_cache import MangaCache


async def finish_part_with_retry(post, body, config):
    """仅重试腾讯明确标为暂时失败的 40093001，保留最终接口错误。"""
    timeout = min(max(float(config.get("retry_timeout", 120)), 1), 120)
    delay = min(max(float(config.get("retry_delay", 1)), 0.1), 10)
    deadline = time.monotonic() + timeout
    while True:
        try:
            return await post("/upload_part_finish", body)
        except QQAPIError as exc:
            if str(exc.code) != "40093001" or time.monotonic() + delay >= deadline:
                raise
            await asyncio.sleep(delay)


async def upload_archive_parts(session, base, headers, archive, album_id):
    """按官方分片协议上传本地 ZIP，避免平台 URL 拉取失败。"""
    md5, sha1 = hashlib.md5(), hashlib.sha1()
    with archive.open("rb") as source:
        head = source.read(10002432)
        md5.update(head)
        sha1.update(head)
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            md5.update(chunk)
            sha1.update(chunk)

    async def post(endpoint, body):
        async with session.post(base + endpoint, headers=headers, json=body) as response:
            data = await read_api_response(response, endpoint, (headers.get('Authorization'),))
            return data

    prepared = await post("/upload_prepare", {
        "file_type": 4, "file_size": str(archive.stat().st_size),
        "file_name": f"JM{album_id}.zip", "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(), "md5_10m": hashlib.md5(head).hexdigest(),
    })
    upload_id = prepared["upload_id"]
    block_size = int(prepared["block_size"])
    if not 0 < block_size <= 200 * 1000 * 1000:
        raise ValueError("无效的分片大小")
    parts = sorted(prepared.get("parts", []), key=lambda p: int(p["index"]))
    # 官方 SDK 使用 1 起始编号；兼容旧协议返回完整的 0 起始序列。
    indices = [int(part["index"]) for part in parts]
    if not indices or indices[0] not in (0, 1) or indices != list(range(indices[0], indices[0] + len(parts))):
        raise ValueError("QQ 返回无效的分片编号")
    origin = indices[0]
    uploaded = 0
    with archive.open("rb") as source:
        for part in parts:
            index = int(part["index"])
            offset = (index - origin) * block_size
            length = min(int(part.get("block_size") or block_size), archive.stat().st_size - offset)
            if length <= 0:
                raise ValueError("QQ 分片超出文件范围")
            source.seek(offset)
            chunk = source.read(length)
            if len(chunk) != length:
                raise ValueError("上传期间文件长度发生变化")
            uploaded += len(chunk)
            # 仅对腾讯返回的预签名地址上传，不附带机器人访问令牌。
            async with session.put(part["presigned_url"], data=chunk) as response:
                if response.status >= 400:
                    raise RuntimeError(f"QQ 分片传输失败: HTTP {response.status}")
                await response.read()
            await finish_part_with_retry(post, {
                "upload_id": upload_id, "part_index": index,
                "block_size": str(len(chunk)), "md5": hashlib.md5(chunk).hexdigest(),
            }, prepared.get("upload_config") or {})
    if uploaded != archive.stat().st_size:
        raise ValueError("QQ 分片未覆盖完整文件")
    result = await post("/files", {
        "file_type": 4, "file_name": f"JM{album_id}.zip",
        "upload_id": upload_id, "srv_send_msg": False,
    })
    if not result.get("file_info"):
        raise RuntimeError("QQ 未返回文件凭据")
    return result["file_info"]


async def send_archive(api, event, target, msg_id, url, album_id, archive):
    scope = "groups" if event == "GROUP_AT_MESSAGE_CREATE" else "users"
    token = await api.get_access_token()
    base = f"{api.base_url}/v2/{scope}/{target}"
    headers = {"Authorization": f"QQBot {token}"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        file_info = await upload_archive_parts(session, base, headers, archive, album_id)
        async with session.post(base + "/messages", headers=headers, json={
            "msg_type": 7, "content": "", "msg_id": msg_id, "msg_seq": 2,
            "media": {"file_info": file_info},
        }) as response:
            await read_api_response(response, base + '/messages', (token, api.app_secret))


def install(app, api, secret, domain, root):
    app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(days=365), SESSION_REFRESH_EACH_REQUEST=True)

    @app.after_request
    def remember_viewer_login(response):
        # 登录响应及旧会话下一次访问时，升级为持久 Cookie。
        if viewer_session.get('jm_viewer_authorized'):
            viewer_session.permanent = True
        return response

    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qq-reply")
    downloads = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qq-download")
    download_slots = BoundedSemaphore(8)
    slots = BoundedSemaphore(16)
    lock = Lock()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "jobs.sqlite3"
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, expires REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, message TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS diagnostics (request_id TEXT PRIMARY KEY, event TEXT, stage TEXT, detail TEXT, updated REAL)")
    signer = URLSafeTimedSerializer(secret or "unconfigured", salt="qq-archive")

    def diagnostic(data_id, event, stage, detail=''):
        request_id = hashlib.sha256(data_id.encode()).hexdigest()[:12]
        detail = str(detail) if isinstance(detail, QQAPIError) else safe_message(detail, (secret,))
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            db.execute("DELETE FROM diagnostics WHERE updated < ?", (time.time()-86400*7,))
            db.execute("INSERT OR REPLACE INTO diagnostics VALUES (?, ?, ?, ?, ?)",
                       (request_id, event, stage, detail, time.time()))
        app.logger.info('QQ request=%s event=%s stage=%s detail=%s', request_id, event, stage, detail)
        return request_id

    def set_job(album_id, message):
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (album_id, message))

    def signing_key():
        seed = secret.encode("utf-8")
        return SigningKey((seed * (32 // len(seed) + 1))[:32])

    async def handle(event, data):
        target = (data.get("group_openid") if event == "GROUP_AT_MESSAGE_CREATE"
                  else data.get("author", {}).get("user_openid"))
        if not target or not data.get("id"):
            diagnostic(data.get('id', 'missing'), event, 'invalid_target', '事件缺少回复目标；字段=' + ','.join(sorted(data)))
            return
        send = api.send_group_message if event == "GROUP_AT_MESSAGE_CREATE" else api.send_c2c_message
        content = data.get("content", "").strip()
        album_id, mode, page = parse_command(content)
        diagnostic(data['id'], event, 'parsed', f'album={album_id or "none"}; target_present=True; content_length={len(content)}')
        if not album_id:
            await send(target, HELP, data["id"], msg_seq=1)
            diagnostic(data['id'], event, 'help_sent')
            return
        if mode == 'web':
            await send(target, f"{domain}/view/{album_id}\n{LOGIN_HINT}", data['id'], msg_seq=1)
            MangaCache(root.parent).touch(album_id)
            diagnostic(data['id'], event, 'web_sent', f'album={album_id}')
            return
        token = signer.dumps(album_id)
        url = f"{domain}/archive/{token}"
        if mode == 'zip':
            await send(target, f"已收到漫画号 {album_id}，正在准备 ZIP 压缩包。首次下载需要等待。", data["id"], msg_seq=1)
        app.logger.info("QQ 请求已确认: album=%s", album_id)
        if not download_slots.acquire(blocking=False):
            diagnostic(data['id'], event, 'queue_full')
            await send(target, "下载队列已满，请稍后重试。", data["id"], msg_seq=2)
            return
        set_job(album_id, "正在准备压缩包，请稍后刷新。若长时间无进展可重新发送漫画号。")
        downloads.submit(prepare, album_id, event, target, data["id"], url, time.time(), mode, page)

    def prepare(album_id, event, target, msg_id, url, started, mode='zip', page=1):
        try:
            archive = build_archive(album_id, root)
            set_job(album_id, "ready")
            limit = 230 if event == "GROUP_AT_MESSAGE_CREATE" else 3500
            if time.time() - started < limit:
                with MangaCache(root.parent).pin(album_id):
                    remaining = min(180, max(1, limit - (time.time() - started)))
                    if mode == 'images':
                        asyncio.run(asyncio.wait_for(send_images(api, event, target, msg_id, album_id, archive, page), timeout=remaining))
                        diagnostic(msg_id, event, 'images_sent', f'album={album_id}; start_page={page}')
                    else:
                        asyncio.run(deliver(archive, album_id, event, target, msg_id, url, remaining))
        except Exception as exc:
            app.logger.exception("漫画压缩包准备失败")
            rid = diagnostic(msg_id, event, 'prepare_failed', exc)
            detail = str(exc) if isinstance(exc, QQAPIError) else safe_message(str(exc), (secret,))
            error = f"{'图片发送' if mode == 'images' else '准备'}失败：{detail}\n请求编号：{rid}"
            set_job(album_id, error)
            send = api.send_group_message if event == "GROUP_AT_MESSAGE_CREATE" else api.send_c2c_message
            try:
                asyncio.run(send(target, error, msg_id, msg_seq=4))
            except Exception:
                app.logger.warning("失败提示未能送达")
        finally:
            download_slots.release()

    async def deliver(archive, album_id, event, target, msg_id, url, upload_timeout=180):
        try:
            if archive.stat().st_size > 200 * 1000 * 1000:
                raise ValueError("超过 QQ 文件上限")
            await asyncio.wait_for(send_archive(api, event, target, msg_id, url, album_id, archive), timeout=upload_timeout)
            diagnostic(msg_id, event, 'file_sent', f'album={album_id}')
            app.logger.info("QQ ZIP 发送成功: album=%s bytes=%s", album_id, archive.stat().st_size)
        except Exception as exc:
            app.logger.exception("文件直发失败，尝试下载链接")
            rid = diagnostic(msg_id, event, 'file_failed', exc)
            send = api.send_group_message if event == "GROUP_AT_MESSAGE_CREATE" else api.send_c2c_message
            detail = ("QQ 文件上传等待超时，请使用下方网页阅读或下载。" if isinstance(exc, asyncio.TimeoutError)
                      else str(exc) if isinstance(exc, QQAPIError) else safe_message(exc, (secret,)))
            try:
                await send(target, f"ZIP 已生成，但文件直发失败。\n{detail}\n请求编号：{rid}\n网页版阅读：\n{domain}/view/{album_id}\n{LOGIN_HINT}\n备用 ZIP 下载（24 小时有效）：\n{url}", msg_id, msg_seq=3)
                diagnostic(msg_id, event, 'link_sent', exc)
            except Exception as fallback:
                diagnostic(msg_id, event, 'reply_failed', fallback)
                return
            app.logger.info("QQ ZIP 下载链接发送成功: album=%s", album_id)

    def run(event, data):
        key = event + ":" + data["id"]
        try:
            asyncio.run(handle(event, data))
        except Exception as exc:
            app.logger.exception("QQ 消息处理失败")
            diagnostic(data['id'], event, 'handler_failed', exc)
            with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
                db.execute("DELETE FROM events WHERE id = ?", (key,))
        finally:
            slots.release()

    @app.route("/qq/callback", methods=["POST", "GET"])
    def qq_callback():
        if request.method == "GET":
            if request.args.get("challenge"):
                return request.args["challenge"]
            return jsonify({"configured": bool(secret and api.app_id), "version": "20260929-command-modes-v2"})
        if not secret or not api.app_id:
            return jsonify({"error": "机器人凭据未配置"}), 503
        raw = request.get_data()
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        signature = request.headers.get("X-Signature-Ed25519", "")
        try:
            if not timestamp:
                raise ValueError("missing timestamp")
            signing_key().verify_key.verify(timestamp.encode() + raw, bytes.fromhex(signature))
        except (ValueError, BadSignatureError):
            return jsonify({"error": "无效签名"}), 401
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("d"), dict):
            return jsonify({"error": "无效事件"}), 400
        if data.get("op") == 13:
            challenge = data["d"]
            plain = challenge.get("plain_token", "")
            message = (str(challenge.get("event_ts", "")) + plain).encode()
            return jsonify({"plain_token": plain,
                            "signature": signing_key().sign(message).signature.hex()})
        event = data.get("t")
        if data.get("op") != 0 or event not in {"GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"}:
            return jsonify({"op": 12})
        event_data = data["d"]
        if not isinstance(event_data.get("id"), str) or not event_data["id"]:
            return jsonify({"error": "缺少消息 ID"}), 400
        key = event + ":" + event_data["id"]
        diagnostic(event_data['id'], event, 'received', 'fields=' + ','.join(sorted(event_data)))
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("DELETE FROM events WHERE expires < ?", (now,))
            if db.execute("SELECT 1 FROM events WHERE id = ?", (key,)).fetchone():
                return jsonify({"op": 12})
            if not slots.acquire(blocking=False):
                return jsonify({"error": "服务繁忙，请重试"}), 503
            db.execute("INSERT INTO events VALUES (?, ?)", (key, now + 86400))
        try:
            executor.submit(run, event, event_data)
        except RuntimeError:
            slots.release()
            with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
                db.execute('DELETE FROM events WHERE id=?',(key,))
            return jsonify(error='服务正在重载，请重试'),503
        return jsonify({"op": 12})

    @app.get("/archive/<token>")
    def archive_download(token):
        if not secret:
            return "机器人尚未配置", 503
        try:
            album_id = signer.loads(token, max_age=86400)
            if not isinstance(album_id, str) or parse_album_id(album_id) != album_id:
                raise BadSignature("invalid album")
        except (BadSignature, SignatureExpired):
            return "链接无效或已过期，请向机器人重新发送漫画号。", 403
        archive = root / f"{album_id}.zip"
        lease = MangaCache(root.parent).pin(album_id)
        lease.__enter__()
        if archive.is_file():
            try:
                response = send_file(archive, as_attachment=True, download_name=f"JM{album_id}.zip",
                                     mimetype="application/zip", max_age=0)
                response.response = ClosingIterator(response.response, lambda: lease.__exit__(None,None,None))
                return response
            except BaseException:
                lease.__exit__(None,None,None)
                raise
        lease.__exit__(None,None,None)
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            row = db.execute("SELECT message FROM jobs WHERE id = ?", (album_id,)).fetchone()
        message = row[0] if row else "任务排队中；若服务重启过，请重新向机器人发送漫画号。"
        return render_template_string(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>漫画压缩包</title><h1>漫画 {{ album_id }}</h1>'
            '<p>{{ message }}</p><button onclick="location.reload()">刷新状态 / 下载</button></html>',
            album_id=album_id, message=message), 202

    return executor, downloads
