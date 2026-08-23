"""
QQ机器人本地启动脚本 (无需Redis)
"""
import sys
import os

# 设置工作目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 现在导入并启动应用
from jm_bot import app

if __name__ == '__main__':
    print("\n" + "="*50)
    print("JM漫画查看器 + QQ机器人 启动中...")
    print("访问地址: http://127.0.0.1:5003")
    print("="*50 + "\n")
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)
