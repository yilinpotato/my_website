"""
简化版启动脚本 - 用于本地测试新版网站
绕过 Redis 依赖
"""
import sys
import os

# 模拟 Redis 模块
class MockRedis:
    def __init__(self, *args, **kwargs):
        self._data = {}
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
    def rpush(self, key, *values):
        if key not in self._data: self._data[key] = []
        for v in values: self._data[key].append(v)
        return len(self._data[key])
    def rpop(self, key):
        if key in self._data and self._data[key]: return self._data[key].pop()
        return None
    def lpop(self, key):
        if key in self._data and self._data[key]: return self._data[key].pop(0)
        return None
    def blpop(self, keys, timeout=0):
        # 模拟阻塞弹出 - 在无数据时返回 None
        import time
        if isinstance(keys, str): keys = [keys]
        for key in keys:
            if key in self._data and self._data[key]:
                return (key, self._data[key].pop(0))
        if timeout > 0:
            time.sleep(min(timeout, 1))  # 最多等1秒
        return None
    def brpop(self, keys, timeout=0):
        import time
        if isinstance(keys, str): keys = [keys]
        for key in keys:
            if key in self._data and self._data[key]:
                return (key, self._data[key].pop())
        if timeout > 0:
            time.sleep(min(timeout, 1))
        return None
    def lrange(self, key, start, end):
        if key in self._data: return self._data[key][start:end+1 if end != -1 else None]
        return []
    def llen(self, key): return len(self._data.get(key, []))
    def exists(self, key): return key in self._data
    def expire(self, key, seconds): return True
    def hset(self, name, key=None, value=None, mapping=None):
        if name not in self._data: self._data[name] = {}
        if key: self._data[name][key] = value
        if mapping: self._data[name].update(mapping)
        return 1
    def hget(self, name, key): return self._data.get(name, {}).get(key)
    def hgetall(self, name): return self._data.get(name, {})
    def hdel(self, name, *keys):
        if name in self._data:
            for k in keys: self._data[name].pop(k, None)
        return len(keys)
    def incr(self, key):
        self._data[key] = int(self._data.get(key, 0)) + 1
        return self._data[key]
    def decr(self, key):
        self._data[key] = int(self._data.get(key, 0)) - 1
        return self._data[key]

class MockRedisModule:
    Redis = MockRedis
    class exceptions:
        ConnectionError = Exception
        BusyLoadingError = Exception

# 在导入 app 之前，先注入模拟的 redis 模块
sys.modules["redis"] = MockRedisModule()

# 设置工作目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 现在导入并启动应用
from app import app

if __name__ == '__main__':
    print("\n" + "="*50)
    print("新版网站 (AI运动评估系统) 启动中...")
    print("访问地址: http://127.0.0.1:5001")
    print("="*50 + "\n")
    app.run(host='127.0.0.1', port=5001, debug=False, threaded=True)
