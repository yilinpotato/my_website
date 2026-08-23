"""
一键启动所有本地服务
"""
import subprocess
import sys
import os
import time
import webbrowser
from threading import Thread

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def resolve_python_command():
    """优先使用指定的 conda 环境，否则回退到当前解释器"""
    conda_exe = r"D:\new\Scripts\conda.exe"
    if os.path.exists(conda_exe):
        return f'"{conda_exe}" run -p D:\\new --no-capture-output python'
    return f'"{sys.executable}"'

def run_service(name, command, cwd):
    """运行一个服务"""
    print(f"[启动] {name}...")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        shell=True,
        stdout=None,
        stderr=None,
        text=True
    )
    return process

def main():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║                     一键启动所有服务                               ║
╠══════════════════════════════════════════════════════════════════╣
║  📍 导航页面:      http://127.0.0.1:8000                          ║
║  🤖 新版网站:      http://127.0.0.1:5001  (AI运动评估系统)         ║
║  📦 旧版网站:      http://127.0.0.1:5002  (经典版)                ║
║  📚 JM漫画查看器:  http://127.0.0.1:5003  (QQ机器人)              ║
╚══════════════════════════════════════════════════════════════════╝
    """)
    
    processes = []
    
    py_cmd = resolve_python_command()

    # 1. 启动导航页面静态服务器
    p1 = run_service(
        "导航页面 (端口 8000)",
        f'{py_cmd} -m http.server 8000',
        BASE_DIR
    )
    processes.append(p1)
    
    # 2. 启动新版网站
    p2 = run_service(
        "新版网站 (端口 5001)",
        f'{py_cmd} run_local.py',
        os.path.join(BASE_DIR, 'new', 'myproject')
    )
    processes.append(p2)
    
    # 3. 启动旧版网站
    p3 = run_service(
        "旧版网站 (端口 5002)",
        f'{py_cmd} run_local.py',
        os.path.join(BASE_DIR, 'old')
    )
    processes.append(p3)
    
    # 4. 启动JM漫画查看器
    p4 = run_service(
        "JM漫画查看器 (端口 5003)",
        f'{py_cmd} run_local.py',
        os.path.join(BASE_DIR, 'qq_bot')
    )
    processes.append(p4)
    
    print("\n[OK] 所有服务启动完成！")
    print("[INFO] 3秒后打开浏览器...")
    
    time.sleep(3)
    webbrowser.open('http://127.0.0.1:8000')
    
    print("\n[INFO] 按 Ctrl+C 停止所有服务\n")
    
    try:
        # 等待进程
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[停止] 正在关闭所有服务...")
        for p in processes:
            p.terminate()
        print("[OK] 所有服务已停止")

if __name__ == '__main__':
    main()
