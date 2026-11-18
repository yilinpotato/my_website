import json
import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from flask import Flask
from itsdangerous import URLSafeTimedSerializer
from nacl.signing import SigningKey

from qq_bot.archives import build_archive, parse_album_id
from qq_bot.qq_service import install, upload_archive_parts, finish_part_with_retry, parse_command, send_images
from qq_bot.qq_errors import QQAPIError


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / '.archives'
        self.root.mkdir()
        self.app = Flask(__name__)
        self.app.secret_key = 'test-session-key'
        self.api = SimpleNamespace(app_id="test", send_group_message=AsyncMock(), send_c2c_message=AsyncMock())
        self.secret = "test-secret"
        self.executors = install(self.app, self.api, self.secret, "https://example.test/jm", self.root)
        self.client = self.app.test_client()

    def tearDown(self):
        for executor in self.executors:
            executor.shutdown(wait=True)
        self.tmp.cleanup()

    def post(self, payload):
        raw = json.dumps(payload).encode()
        ts = "1234567890"
        key = SigningKey((self.secret.encode() * 32)[:32])
        return self.client.post("/qq/callback", data=raw, content_type="application/json", headers={
            "X-Signature-Timestamp": ts,
            "X-Signature-Ed25519": key.sign(ts.encode() + raw).signature.hex(),
        })

    def test_commands(self):
        for text in ["123456", "JM123456", "/jm 123456", "漫画 123456", "<@!123> /jm 123456"]:
            self.assertEqual(parse_album_id(text), "123456")
        for text in ["../123", "１２３", "/jm 12 blah", "hello"]:
            self.assertIsNone(parse_album_id(text))

    def test_invalid_signature(self):
        self.assertEqual(self.client.post("/qq/callback", json={"op": 0}).status_code, 401)
        self.api.send_c2c_message.assert_not_called()

    def test_challenge(self):
        result = self.post({"op": 13, "d": {"plain_token": "hello", "event_ts": "123"}})
        self.assertEqual(result.status_code, 200)
        SigningKey((self.secret.encode() * 32)[:32]).verify_key.verify(
            b"123hello", bytes.fromhex(result.json["signature"]))

    def test_reply_and_duplicate(self):
        event = {"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {
            "id": "message", "author": {"user_openid": "user"}, "content": "help"}}
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.post(event).status_code, 200)
        self.executors[0].shutdown(wait=True)
        self.api.send_c2c_message.assert_awaited_once()

    def test_plain_text_routes_to_chat_not_manga(self):
        chat = self.app.extensions['qq_chat']
        with patch.object(chat, 'answer', return_value={'reply':'你好，我是哈基米'}) as answer, \
             patch('qq_bot.qq_service.build_archive') as download:
            self.post({'op':0,'t':'GROUP_AT_MESSAGE_CREATE','d':{
                'id':'chat-message','group_openid':'group','author':{'member_openid':'member'},
                'content':'<@!bot> 你好'}})
            for executor in self.executors: executor.shutdown(wait=True)
            answer.assert_called_once_with('你好','GROUP_AT_MESSAGE_CREATE:group:member','member')
            download.assert_not_called()
            self.assertEqual(self.api.send_group_message.call_args.args[1], '你好，我是哈基米')

    def test_chat_picture_uses_media_sender(self):
        with patch.object(self.app.extensions['qq_chat'], 'answer', return_value={'reply':'给你','image':b'image'}), \
             patch('qq_bot.qq_service.send_chat_image',new_callable=AsyncMock) as media:
            self.post({'op':0,'t':'C2C_MESSAGE_CREATE','d':{
                'id':'chat-image','author':{'user_openid':'member'},'content':'发个表情'}})
            for executor in self.executors: executor.shutdown(wait=True)
            media.assert_awaited_once()
            self.api.send_c2c_message.assert_not_awaited()

    def test_group_archive_and_download(self):
        path = self.root / "123456.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("123456/1/001.jpg", b"synthetic-test-image")
        with patch("qq_bot.qq_service.build_archive", return_value=path) as build, \
                patch("qq_bot.qq_service.send_archive", new_callable=AsyncMock) as send_file:
            event = {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": {
                "id": "message", "group_openid": "group", "content": "压缩包 123456"}}
            self.assertEqual(self.post(event).status_code, 200)
            for executor in self.executors:
                executor.shutdown(wait=True)
            build.assert_called_once_with("123456", self.root)
            send_file.assert_awaited_once()
        content = self.api.send_group_message.call_args.args[1]
        self.assertIn("123456", content)
        token = URLSafeTimedSerializer(self.secret, salt="qq-archive").dumps("123456")
        response = self.client.get("/archive/" + token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("JM123456.zip", response.headers["Content-Disposition"])
        response.close()
        self.assertEqual(self.client.get("/archive/invalid").status_code, 403)
        pending = URLSafeTimedSerializer(self.secret, salt="qq-archive").dumps("456")
        self.assertEqual(self.client.get("/archive/" + pending).status_code, 202)

    def test_modes(self):
        for text in ['web 123456', 'WEB 123456', '123456', '/jm 123456', '网页 123456', '123456 只发网页', '搜索 123456', '/search 123456']:
            self.assertEqual(parse_command(text), ('123456', 'web', 1))
        self.assertEqual(parse_command('<@!bot> 发压缩包 123456'), ('123456', 'zip', 1))
        self.assertEqual(parse_command('/jm 123456 图片 4'), ('123456', 'images', 4))
        self.assertEqual(parse_command('zip 123456'), ('123456', 'zip', 1))
        self.assertEqual(parse_command('img 123456'), ('123456', 'images', 1))
        self.assertEqual(parse_command('img 123456 4'), ('123456', 'images', 4))
        for text in ['图片 123456 0', 'zip 123456 4', '123456 zip web', '图片 ../12']:
            self.assertIsNone(parse_command(text)[0])

    def test_default_only_web_without_download(self):
        with patch('qq_bot.qq_service.build_archive') as build, patch('qq_bot.qq_service.send_archive') as upload:
            for index, content in enumerate(['123456', '只发网页 123456']):
                self.post({'op': 0, 't': 'C2C_MESSAGE_CREATE', 'd': {
                    'id': str(index), 'author': {'user_openid': 'user'}, 'content': content}})
            for executor in self.executors: executor.shutdown(wait=True)
            build.assert_not_called()
            upload.assert_not_called()
        self.assertEqual(self.api.send_c2c_message.await_count, 2)
        for call in self.api.send_c2c_message.call_args_list:
            self.assertIn('https://example.test/jm/view/123456', call.args[1])
            self.assertIn('网页登录密码：EXAMPLE_VIEWER_PASSWORD', call.args[1])

    def test_login_becomes_persistent_and_survives_new_client(self):
        from flask import session
        @self.app.get('/test-login')
        def login():
            session['jm_viewer_authorized'] = True
            return 'ok'
        response = self.client.get('/test-login')
        self.assertIn('Expires=', response.headers['Set-Cookie'])
        cookie = self.client.get_cookie('session')
        fresh = self.app.test_client()
        fresh.set_cookie('session', cookie.value)
        with fresh.session_transaction() as saved:
            self.assertTrue(saved['jm_viewer_authorized'])
            self.assertTrue(saved.permanent)
        self.assertEqual(self.app.permanent_session_lifetime.days, 365)

    def test_help_discloses_requested_viewer_password(self):
        from qq_bot.qq_service import HELP
        self.assertIn('EXAMPLE_VIEWER_PASSWORD', HELP)
        for command in ['help', 'web 123456', 'zip 123456', 'img 123456', 'img 123456 4']:
            self.assertIn(command, HELP)

    def test_image_mode_never_sends_zip_or_web(self):
        path = self.root / '123456.zip'
        path.write_bytes(b'fixture')
        with patch('qq_bot.qq_service.build_archive', return_value=path), \
             patch('qq_bot.qq_service.send_images', new_callable=AsyncMock) as images, \
             patch('qq_bot.qq_service.send_archive', new_callable=AsyncMock) as archive:
            self.post({'op': 0, 't': 'GROUP_AT_MESSAGE_CREATE', 'd': {
                'id': 'images', 'group_openid': 'group', 'content': '图片 123456 4'}})
            for executor in self.executors: executor.shutdown(wait=True)
            images.assert_awaited_once()
            self.assertEqual(images.call_args.args[-1], 4)
            archive.assert_not_awaited()
            self.api.send_group_message.assert_not_awaited()

    def test_image_batch_protocol(self):
        path = self.root / 'images.zip'
        with zipfile.ZipFile(path, 'w') as z:
            for page in [1, 10, 2, 3, 4]: z.writestr(f'123/1/{page}.jpg', str(page).encode())
        calls = []
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return {'file_info': 'image-token'}
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def post(self, url, headers, json):
                calls.append((url, json)); return Response()
        api = SimpleNamespace(base_url='https://api.test', app_secret='secret', get_access_token=AsyncMock(return_value='token'))
        with patch('qq_bot.qq_service.aiohttp.ClientSession', return_value=Session()):
            asyncio.run(send_images(api, 'C2C_MESSAGE_CREATE', 'user', 'message', '123', path, 2))
        import base64
        self.assertEqual([base64.b64decode(body['file_data']) for url, body in calls if url.endswith('/files')], [b'2', b'3', b'4'])
        messages = [body for url, body in calls if url.endswith('/messages')]
        self.assertEqual([body['msg_seq'] for body in messages], [1, 2, 3])
        self.assertIn('img 123 5', messages[-1]['content'])

    def test_webp_and_gif_pages_are_sent_as_jpeg(self):
        from io import BytesIO
        from PIL import Image
        import base64
        path = self.root / 'formats.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for extension in ['webp', 'gif']:
                buffer = BytesIO()
                Image.new('RGB', (12, 16), 'red').save(buffer, format=extension.upper())
                archive.writestr(f'123/1/page.{extension}', buffer.getvalue())
        uploaded = []
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return {'file_info': 'image-token'}
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def post(self, url, headers, json):
                if url.endswith('/files'):
                    uploaded.append(base64.b64decode(json['file_data']))
                return Response()
        with patch('qq_bot.qq_service.aiohttp.ClientSession', return_value=Session()):
            api = SimpleNamespace(base_url='https://api.test', app_secret='secret', get_access_token=AsyncMock(return_value='token'))
            asyncio.run(send_images(api, 'C2C_MESSAGE_CREATE', 'user', 'message', '123', path, 1))
        self.assertEqual(len(uploaded), 2)
        for payload in uploaded:
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual(image.format, 'JPEG')
                self.assertEqual(image.size, (12, 16))

    def test_empty_image_archive_has_clear_error(self):
        path = self.root / 'empty.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('readme.txt', 'no images')
            archive.writestr('fake.jpg/', '')
        with self.assertRaisesRegex(ValueError, '没有可发送的图片'):
            asyncio.run(send_images(SimpleNamespace(base_url='https://api.test'), 'C2C_MESSAGE_CREATE', 'user', 'message', '123', path, 1))

    def test_complete_archive_and_failure(self):
        def construct(config):
            base = Path(config["dir_rule"]["base_dir"])
            def download(album_id):
                directory = base / album_id / "1"
                directory.mkdir(parents=True)
                (directory / "001.jpg").write_bytes(b"synthetic-image")
            return SimpleNamespace(new_jm_client=lambda: SimpleNamespace(get_album_detail=lambda _: [1]),
                                   download_album=download)
        with patch("jmcomic.JmOption.construct", side_effect=construct):
            path = build_archive("123", self.root)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["123/1/001.jpg"])
        with patch("jmcomic.JmOption.construct", side_effect=RuntimeError("failed")):
            self.assertEqual(build_archive("123", self.root), path)
            with self.assertRaises(RuntimeError):
                build_archive("456", self.root)
            self.assertFalse((self.root / "456.zip").exists())

    def test_chunk_upload_protocol(self):
        path = self.root / "fixture.zip"
        path.write_bytes(b"0123456789")
        calls, chunks = [], []
        class Response:
            status = 200
            def __init__(self, data): self.data = data
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self): return self.data
            async def read(self): return b""
        class Session:
            def post(self, url, headers, json):
                calls.append((url, json))
                if url.endswith("upload_prepare"):
                    return Response({"upload_id": "upload", "block_size": "4", "parts": [
                        {"index": i, "block_size": "4", "presigned_url": f"https://upload.test/{i}"}
                        for i in range(1, 4)]})
                return Response({"file_info": "file"} if url.endswith("/files") else {})
            def put(self, url, data):
                chunks.append(data)
                return Response({})
        result = asyncio.run(upload_archive_parts(Session(), "https://api.test/users/user", {}, path, "123"))
        self.assertEqual(result, "file")
        self.assertEqual(chunks, [b"0123", b"4567", b"89"])
        self.assertEqual(calls[1][1]["part_index"], 1)
        self.assertEqual(calls[-2][1]["block_size"], "2")
        self.assertEqual(calls[-1][1]["upload_id"], "upload")
        self.assertFalse(calls[-1][1]["srv_send_msg"])

    def test_transient_finish_retry(self):
        error = QQAPIError('/upload_part_finish', 400, {'code': 40093001})
        post = AsyncMock(side_effect=[error, {}])
        with patch('qq_bot.qq_service.asyncio.sleep', new_callable=AsyncMock) as sleep:
            asyncio.run(finish_part_with_retry(post, {'part_index': 1}, {}))
        self.assertEqual(post.await_count, 2)
        sleep.assert_awaited_once()

    def test_permanent_finish_not_retried(self):
        error = QQAPIError('/upload_part_finish', 400, {'code': 40093002})
        post = AsyncMock(side_effect=error)
        with self.assertRaises(QQAPIError):
            asyncio.run(finish_part_with_retry(post, {}, {}))
        self.assertEqual(post.await_count, 1)

    def test_finish_retry_deadline_preserves_trace(self):
        error = QQAPIError('/upload_part_finish', 400, {'code': 40093001, 'trace_id': 'test-trace'})
        post = AsyncMock(side_effect=error)
        with patch('qq_bot.qq_service.time') as clock:
            clock.monotonic.side_effect = [0, 121]
            with self.assertRaises(QQAPIError) as raised:
                asyncio.run(finish_part_with_retry(post, {}, {}))
        self.assertEqual(raised.exception.trace, 'test-trace')
        self.assertEqual(post.await_count, 1)


if __name__ == "__main__":
    unittest.main()
