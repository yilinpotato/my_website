from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
# 在生产环境下，CORS 不是必须的，因为请求都来自同一个域名，不存在跨域问题了。
# 但保留着也无妨。
CORS(app) 

# ★★★ 修正这里的路由 ★★★
@app.route('/ai/api/test', methods=['GET'])
def test_api():
    return jsonify({"message": "Hello from Python Backend!"})

# 如果你还有其他接口，也需要加上 /ai 前缀
# 例如：@app.route('/ai/api/notes', methods=['GET'])

# 注意：if __name__ == '__main__': 这部分在 Gunicorn 启动模式下不会被执行
# Gunicorn 会直接从文件里寻找名为 app 的 Flask 实例
# 所以这部分代码只在你本地 python app.py 调试时有用
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=True)