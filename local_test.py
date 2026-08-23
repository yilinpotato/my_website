"""
本地测试启动脚本
在没有 Redis 的情况下启动网站进行测试
"""
import subprocess
import sys
import os
import time
import webbrowser
from threading import Thread
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from functools import partial

# 设置工作目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NEW_PROJECT_DIR = os.path.join(BASE_DIR, 'new', 'myproject')
OLD_PROJECT_DIR = os.path.join(BASE_DIR, 'old')

def mock_redis():
    """创建一个模拟的 Redis 类，用于在没有 Redis 时运行"""
    class MockRedis:
        def __init__(self, *args, **kwargs):
            self._data = {}
            
        def ping(self):
            return True
            
        def set(self, key, value, nx=False, ex=None):
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True
            
        def get(self, key):
            return self._data.get(key)
            
        def delete(self, *keys):
            for key in keys:
                self._data.pop(key, None)
            return len(keys)
            
        def lpush(self, key, *values):
            if key not in self._data:
                self._data[key] = []
            for v in values:
                self._data[key].insert(0, v)
            return len(self._data[key])
            
        def rpop(self, key):
            if key in self._data and self._data[key]:
                return self._data[key].pop()
            return None
            
        def lrange(self, key, start, end):
            if key in self._data:
                return self._data[key][start:end+1 if end != -1 else None]
            return []
            
        def llen(self, key):
            return len(self._data.get(key, []))
            
        def exists(self, key):
            return key in self._data
            
        def expire(self, key, seconds):
            return True
    
    return MockRedis

def start_server(port, project_dir, name):
    """启动 Flask 服务器"""
    print(f"\n{'='*50}")
    print(f"正在启动 {name} (端口 {port})...")
    print(f"目录: {project_dir}")
    print(f"{'='*50}\n")
    
    os.chdir(project_dir)
    
    # 添加模拟 Redis 到环境
    env = os.environ.copy()
    env['PYTHONPATH'] = BASE_DIR
    
    # 创建启动命令
    startup_code = f'''
import sys
import os
sys.path.insert(0, r"{project_dir}")
os.chdir(r"{project_dir}")

# 模拟 Redis
class MockRedis:
    def __init__(self, *args, **kwargs):
        self._data = {{}}
    def ping(self): return True
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self._data: return False
        self._data[key] = value
        return True
    def get(self, key): return self._data.get(key)
    def delete(self, *keys):
        for key in keys: self._data.pop(key, None)
        return len(keys)
    def lpush(self, key, *values):
        if key not in self._data: self._data[key] = []
        for v in values: self._data[key].insert(0, v)
        return len(self._data[key])
    def rpop(self, key):
        if key in self._data and self._data[key]: return self._data[key].pop()
        return None
    def lrange(self, key, start, end):
        if key in self._data: return self._data[key][start:end+1 if end != -1 else None]
        return []
    def llen(self, key): return len(self._data.get(key, []))
    def exists(self, key): return key in self._data
    def expire(self, key, seconds): return True

class MockRedisModule:
    Redis = MockRedis
    class exceptions:
        ConnectionError = Exception
        BusyLoadingError = Exception

sys.modules["redis"] = MockRedisModule()

from app import app
app.run(host="127.0.0.1", port={port}, debug=False, threaded=True)
'''
    
    subprocess.run([sys.executable, '-c', startup_code], cwd=project_dir)

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                    本地网站测试启动器                          ║
╠══════════════════════════════════════════════════════════════╣
║  导航页面:  http://127.0.0.1:8000                             ║
║  新版网站:  http://127.0.0.1:5001  (AI运动评估系统)            ║
║  旧版网站:  http://127.0.0.1:5002  (经典版)                   ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # 启动静态文件服务器 (导航页面)
    print("启动导航页面服务器 (端口 8000)...")
    
    # 3秒后打开浏览器
    def open_browser():
        time.sleep(3)
        webbrowser.open('http://127.0.0.1:8000')
    
    Thread(target=open_browser, daemon=True).start()
    
    # 启动静态服务器（带简单回退接口，避免 404 噪音）
    class NavHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/favicon.ico':
                self.send_response(204)
                self.end_headers()
                return
            if parsed.path == '/hybridaction/zybTrackerStatisticsAction':
                params = parse_qs(parsed.query)
                callback = params.get('__callback__', ['__cb__'])[0]
                body = f"{callback}({{}})"
                data = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return super().do_GET()

    handler = partial(NavHandler, directory=BASE_DIR)
    with ThreadingHTTPServer(('127.0.0.1', 8000), handler) as httpd:
        print("Serving HTTP on 127.0.0.1 port 8000 (http://127.0.0.1:8000/) ...")
        httpd.serve_forever()

if __name__ == '__main__':
    main()
