import os
import shutil
import time
import subprocess
import json
import io

import csv
import random  # ★★★ 新增这一行 ★★★
import string  # ★★★ 新增这一行 ★★★
from threading import Lock, Thread
# ★★★ 新增下面这一行 ★★★
from flask_mail import Mail, Message
from flask import (
    Flask, render_template, jsonify, request, abort,
    send_from_directory, session, redirect, url_for,
    flash, after_this_request, send_file, Response # ★★★ 确保 Response 在这里 ★★★
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import secrets
import psutil
import redis
import tempfile
import uuid
from werkzeug.utils import secure_filename
import zipfile
import hmac
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

# ===================================================
#             1. 应用初始化与核心配置
# ===================================================
app = Flask(__name__)

# ★★★ 支持反向代理环境，修复 url_for 生成的路径 ★★★
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# --- 核心安全配置 ---
# SECRET_KEY 用于 session 加密，请确保它是一个复杂的随机字符串
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

# --- 数据库配置 ---
# 获取项目根目录的绝对路径
basedir = os.path.abspath(os.path.dirname(__file__))
# 使用 SQLite 数据库，文件名为 users.db，将存储在项目根目录下
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'users.db')
# 关闭一个不必要的性能警告
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- 文件上传与大小限制配置 ---
UPLOAD_FOLDER = os.path.join(basedir, 'static/uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 8000 * 1024 * 1024  # 8000 MB
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# ★★★ 新增下面的邮箱配置 ★★★
# --- 邮箱配置 ---
app.config['MAIL_SERVER'] = 'smtp.163.com'
app.config['MAIL_PORT'] = 465  # 163邮箱使用SSL的端口
app.config['MAIL_USE_SSL'] = True # 必须开启SSL
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "").strip()
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "").strip()
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER") or app.config["MAIL_USERNAME"] or "noreply@example.com"

# --- 自定义应用配置 ---
DOWNLOAD_DIR = os.path.join(app.root_path, 'static', 'manga')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
MY_COOKIE_STRING = os.getenv("JM_COOKIE_STRING", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_IP = os.getenv("ADMIN_IP", "127.0.0.1").strip()

# --- JSON 文件路径配置 ---
MESSAGES_FILE = os.path.join(basedir, 'messages.json')
URLS_FILE = os.path.join(basedir, 'urls.json')
IP_NICKNAMES_FILE = os.path.join(basedir, 'ip_nicknames.json')
file_lock = Lock()


# ===================================================
#             2. 扩展初始化
# ===================================================

# --- 初始化数据库 ORM ---
# 必须在 app.config 配置完成后再初始化
db = SQLAlchemy(app)

# ★★★ 新增下面这一行 ★★★
mail = Mail(app)

# --- 初始化登录管理器 ---
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # 未登录用户访问保护页面时，重定向到 /login
login_manager.login_message = "请先登录以访问此页面。"
login_manager.login_message_category = "info" # 使用 flash 消息的 'info' 类别


# ===================================================
#             3. 后台服务与全局变量
# ===================================================

# --- Redis 配置 ---
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
REDIS_PENDING_QUEUE = "jm:queue:pending"
REDIS_CURRENT_TASK = "jm:task:current"
REDIS_COMPLETED_LIST = "jm:list:completed"

# --- 下载器性能配置 (原样保留) ---
IO_THROTTLING_ENABLED = True
IO_HIGH_WATERMARK = 70.0
IO_LOW_WATERMARK = 50.0
IO_CHECK_INTERVAL = 3
is_io_throttled = Lock()
is_io_throttled.acquire(blocking=False)
DOWNLOAD_THREADS_MAX = 10
DOWNLOAD_THREADS_NORMAL = 5
DOWNLOAD_THREADS_MIN = 2
IO_NORMAL_THRESHOLD = 45.0
IO_BUSY_THRESHOLD = 75.0
current_download_threads = DOWNLOAD_THREADS_MAX

# --- 后台线程状态监控 ---
thread_status = {
    "io_monitor": {"alive": False, "last_seen": 0},
    "download_worker": {"alive": False, "last_seen": 0}
}


# ★★★ 新增：用户数据库模型 ★★★
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# ★★★ 新增：Flask-Login 需要的回调函数，用于从 session 中加载用户 ★★★
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ★★★ 生产环境自动初始化数据库（模型定义后）★★★
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"数据库初始化失败: {e}")

# --- 辅助函数 ---
ALLOWED_PLY_EXTENSIONS = {'ply'}

def allowed_ply_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_PLY_EXTENSIONS

# --- 添加一个辅助函数来检查文件扩展名 ---
def allowed_image_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def io_monitor_thread():
    global current_download_threads
    print("智能I/O监控线程已启动...")
    while IO_THROTTLING_ENABLED:
        try:
            thread_status["io_monitor"]["alive"] = True
            thread_status["io_monitor"]["last_seen"] = time.time()
            iowait = psutil.cpu_times_percent(interval=IO_CHECK_INTERVAL).iowait
            if iowait < IO_NORMAL_THRESHOLD:
                if current_download_threads != DOWNLOAD_THREADS_MAX:
                    print(f"I/O空闲 (iowait: {iowait:.2f}%), 提升下载速度至 {DOWNLOAD_THREADS_MAX} 线程。")
                    current_download_threads = DOWNLOAD_THREADS_MAX
            elif IO_NORMAL_THRESHOLD <= iowait < IO_BUSY_THRESHOLD:
                if current_download_threads != DOWNLOAD_THREADS_NORMAL:
                    print(f"I/O正常 (iowait: {iowait:.2f}%), 调整下载速度为 {DOWNLOAD_THREADS_NORMAL} 线程。")
                    current_download_threads = DOWNLOAD_THREADS_NORMAL
            else:
                if current_download_threads != DOWNLOAD_THREADS_MIN:
                    print(f"I/O繁忙 (iowait: {iowait:.2f}%), 降低下载速度至 {DOWNLOAD_THREADS_MIN} 线程。")
                    current_download_threads = DOWNLOAD_THREADS_MIN
            is_currently_throttled = not is_io_throttled.locked()
            if is_currently_throttled:
                if iowait < IO_LOW_WATERMARK:
                    is_io_throttled.release()
                    print(f"I/O负载已恢复 (iowait: {iowait:.2f}%)，解除熔断。")
            else:
                if iowait > IO_HIGH_WATERMARK:
                    is_io_throttled.acquire()
                    print(f"I/O负载过高 (iowait: {iowait:.2f}%)，触发熔断！暂停新下载任务。")
        except Exception as e:
            print(f"I/O监控线程出错: {e}")
            time.sleep(5)
    thread_status["io_monitor"]["alive"] = False
            
def read_json_file(filepath, default_type='list'):
    with file_lock:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return [] if default_type == 'list' else {}
        with open(filepath, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return [] if default_type == 'list' else {}

def write_json_file(filepath, data):
    with file_lock:
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
        
def is_admin(): return session.get('is_admin') == True
if not os.path.exists(MESSAGES_FILE): write_json_file(MESSAGES_FILE, [])
if not os.path.exists(IP_NICKNAMES_FILE): write_json_file(IP_NICKNAMES_FILE, {})
if not os.path.exists(URLS_FILE): write_json_file(URLS_FILE, [])

# --- 认证、网址大全、留言板API路由 ---
# ... (此处省略这些路由的完整代码，请确保您有这些函数的正确版本)
# login, logout, get_urls, add_url, delete_url, check_admin_status,
# get_messages, delete_message, get_nickname, post_message,
# like_message, reply_to_message


# --- HTML 路由 ---
@app.route('/')
def root_path():
    # ★★★ 确保这里传递了 is_admin 变量 ★★★
    return render_template('index.html', is_admin=is_admin())

# 2. 找到这个 index.html 路由
@app.route('/index.html')
def index_page():
    # ★★★ 确保这里也传递了 is_admin 变量 ★★★
    return render_template('index.html', is_admin=is_admin())

@app.route('/jm-viewer.html')
def jm_viewer_page(): return render_template('jm-viewer.html')
@app.route('/urls.html')
def urls_page(): return render_template('urls.html')


@app.route('/guestbook.html')

def guestbook_page():
    return render_template('guestbook.html')

@app.route('/3d-viewer')
def ply_viewer_page():
    models_dir = os.path.join(app.static_folder, 'models')
    server_models = []
    if os.path.exists(models_dir):
        # 同时获取 .splat 和 .ply 文件
        server_models = sorted([f for f in os.listdir(models_dir) if f.endswith('.splat') or f.endswith('.ply')])
    
    return render_template('ply_viewer.html', server_models=server_models)

# ★★★ 为上传的临时3D模型提供下载路由 ★★★
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    
@app.route('/ply_viewer/upload', methods=['POST'])
def upload_ply_file():
    if 'ply_file' not in request.files:
        flash('请求中没有文件部分')
        return redirect(url_for('ply_viewer_page'))

    file = request.files['ply_file']
    if file.filename == '':
        flash('没有选择文件')
        return redirect(url_for('ply_viewer_page'))

    if file and allowed_ply_file(file.filename):
        # 直接使用原始文件名，加上唯一ID前缀
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        
        # 保存到 uploads 目录
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(save_path)
        
        # 将新的文件名传递给模板
        return render_template('ply_viewer.html', model_filename=unique_filename)
    else:
        flash('无效的文件类型，请上传 .ply 文件')
        return redirect(url_for('ply_viewer_page'))


@app.route('/hybrid_creator', methods=['GET', 'POST'])
def hybrid_creator_page():
    if request.method == 'POST':
        # 1. 基础检查 (这部分逻辑保持不变)
        if 'carrier_image' not in request.files or 'hidden_file' not in request.files:
            flash('请求中缺少文件部分'); return redirect(url_for('hybrid_creator_page'))
        image_file, hidden_file = request.files['carrier_image'], request.files['hidden_file']
        if image_file.filename == '' or hidden_file.filename == '':
            flash('没有选择文件'); return redirect(url_for('hybrid_creator_page'))
        if not allowed_image_file(image_file.filename):
            flash('图片格式无效'); return redirect(url_for('hybrid_creator_page'))
        original_hidden_filename = secure_filename(hidden_file.filename)
        if not original_hidden_filename:
            flash('隐藏的文件名无效'); return redirect(url_for('hybrid_creator_page'))

        # ★★★【修改点 1】: 不再需要 output_path 和 temp_zip_path 了 ★★★
        image_ext = image_file.filename.rsplit('.', 1)[1].lower()
        output_filename = f"hybrid_output.{image_ext}" # 准备一个下载时的文件名

        try:
            # 2. 将文件读入内存
            image_content = image_file.read()
            hidden_file_content = hidden_file.read()

            if not image_content or not hidden_file_content:
                raise ValueError("上传的文件内容为空！")

            # 3. 在内存中创建ZIP压缩包
            zip_in_memory = io.BytesIO()
            with zipfile.ZipFile(zip_in_memory, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(original_hidden_filename, hidden_file_content)
            zip_in_memory.seek(0) # 移动指针到开头以便读取

            # 4. 在内存中拼接图片和ZIP数据
            final_file_in_memory = io.BytesIO()
            final_file_in_memory.write(image_content)
            final_file_in_memory.write(zip_in_memory.read())
            final_file_in_memory.seek(0) # 移动指针到开头以便发送

            # ★★★【修改点 2】: 使用 send_file 直接发送内存中的数据 ★★★
            # 这样就无需在磁盘上创建任何最终文件，也无需清理
            return send_file(
                final_file_in_memory,
                as_attachment=True,
                download_name=output_filename, # 在新版本 Flask 中参数名为 download_name
                # attachment_filename=output_filename, # 在旧版本 Flask 中参数名为 attachment_filename
                mimetype=image_file.mimetype # 保持原始图片的MIME类型
            )

        except Exception as e:
            print(f"!!! CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            flash('服务器内部发生错误，请联系管理员。')
            return redirect(url_for('hybrid_creator_page'))
        # ★★★【修改点 3】: 不再需要 finally 块来清理临时文件，因为我们没有在磁盘上创建它们 ★★★

    # --- GET 请求逻辑 ---
    return render_template('hybrid_creator.html')


@app.route('/hybrid_extractor', methods=['POST'])
def hybrid_extractor_page():
    """
    处理从混合图片中提取ZIP文件的请求。
    """
    # 1. 基础检查
    if 'hybrid_image' not in request.files:
        flash('请求中缺少文件部分')
        return redirect(url_for('hybrid_creator_page'))
    
    hybrid_file = request.files['hybrid_image']
    if hybrid_file.filename == '':
        flash('没有选择文件')
        return redirect(url_for('hybrid_creator_page'))

    try:
        # 2. 读取文件内容到内存
        hybrid_file_content = hybrid_file.read()
        if not hybrid_file_content:
            raise ValueError("上传的文件内容为空！")

        # 3. ★★★ 核心：查找ZIP文件签名 ★★★
        # ZIP文件的标准起始签名是 b'\x50\x4b\x03\x04' (PK..).
        ZIP_SIGNATURE = b'\x50\x4b\x03\x04'
        zip_start_index = hybrid_file_content.find(ZIP_SIGNATURE)

        # 如果找不到签名，说明这不是一个有效的混合文件
        if zip_start_index == -1:
            flash('未在此图片中找到隐藏的ZIP压缩包！')
            return redirect(url_for('hybrid_creator_page'))

        # 4. 截取ZIP数据
        zip_data = hybrid_file_content[zip_start_index:]
        
        # 5. 将ZIP数据放入内存文件对象中
        zip_in_memory = io.BytesIO(zip_data)

        # 6. 发送文件给用户下载
        return send_file(
            zip_in_memory,
            as_attachment=True,
            download_name='extracted_archive.zip',
            mimetype='application/zip'
        )

    except Exception as e:
        print(f"!!! CRITICAL ERROR during extraction: {e}")
        import traceback
        traceback.print_exc()
        flash('解压过程中发生服务器内部错误，请联系管理员。')
        return redirect(url_for('hybrid_creator_page'))



# --- 下载相关核心函数 (确保定义在被调用的地方之前) ---
def rename_and_set_perms(dir_list, album_id):
    final_dir_names = []
    for original_dir_name in dir_list:
        if not original_dir_name.startswith(f"{album_id}_"):
            new_dir_name = f"{album_id}_{original_dir_name}"
            original_path = os.path.join(DOWNLOAD_DIR, original_dir_name)
            new_path = os.path.join(DOWNLOAD_DIR, new_dir_name)
            if original_path == new_path:
                final_dir_names.append(original_dir_name)
                continue
            if os.path.exists(new_path): shutil.rmtree(new_path)
            if os.path.exists(original_path):
                os.rename(original_path, new_path)
                final_dir_names.append(new_dir_name)
        else:
            final_dir_names.append(original_dir_name)
    for final_dir in final_dir_names:
        final_path = os.path.join(DOWNLOAD_DIR, final_dir)
        if os.path.exists(final_path):
            try:
                subprocess.run(['chown', '-R', 'www-data:www-data', final_path], check=True, capture_output=True)
                subprocess.run(['chmod', '-R', '755', final_path], check=True, capture_output=True)
            except Exception as perm_error:
                print(f"警告：为 {final_path} 自动设置权限失败: {perm_error}")

def _perform_download(album_id):
    before_dirs = set(os.listdir(DOWNLOAD_DIR))
    try:
        if not is_io_throttled.locked():
            return False, "服务器I/O负载过高，任务已暂停"

        option = jmcomic.JmOption.default()
        option.download.ignore_photo_error = True
        option.dir_rule.base_dir = DOWNLOAD_DIR
        option.download.threading.photo = current_download_threads
        
        if getattr(option.client.postman.meta_data, 'headers', None) is None:
            option.client.postman.meta_data.headers = {}
        option.client.postman.meta_data.headers['Cookie'] = MY_COOKIE_STRING
        
        option.client.postman.meta_data.timeout = 30
        option.client.retry_times = 2
        
        print(f"ID {album_id}: 已使用正确的属性方式配置JmOption。")

        client = option.new_jm_client()
        album_for_check = client.get_album_detail(album_id)
        if album_for_check is None: return False, f"无法获取ID {album_id} 的详情"
        if len(album_for_check) > 10: return False, f"章节数({len(album_for_check)}) > 10，为防服务器卡死已中止"
        
        option.download_album(album_id)
        time.sleep(1)

        after_dirs = set(os.listdir(DOWNLOAD_DIR))
        newly_created_dirs_this_run = after_dirs - before_dirs
        target_dir_prefix = f"{album_id}_"

        if newly_created_dirs_this_run:
            rename_and_set_perms(newly_created_dirs_this_run, album_id)
            return True, "首次下载成功"
            
        matched_dirs = [d for d in after_dirs if d.startswith(target_dir_prefix) or d == str(album_id)]
        if matched_dirs:
            rename_and_set_perms(matched_dirs, album_id)
            return True, "增量下载/缓存命中成功"

        raise Exception("下载过程未创建任何新文件夹，也未在磁盘上找到匹配的文件夹")

    except Exception as e:
        print(f"下载ID {album_id} 过程中发生严重错误，将执行精确清理...")
        import traceback
        traceback.print_exc()
        final_dirs = set(os.listdir(DOWNLOAD_DIR))
        dirs_to_cleanup = final_dirs - before_dirs
        if dirs_to_cleanup:
            print(f"检测到需要清理的垃圾文件夹: {list(dirs_to_cleanup)}")
            for d in dirs_to_cleanup:
                try: shutil.rmtree(os.path.join(DOWNLOAD_DIR, d))
                except OSError as cleanup_error: print(f"清理文件夹 {d} 时出错: {cleanup_error}")
        return False, f"下载失败: {e}"

def download_worker():
    print("后台下载工作线程已启动 (使用Redis, 永不死模式)...")
    while True:
        try:
            thread_status["download_worker"]["alive"] = True
            thread_status["download_worker"]["last_seen"] = time.time()
            task = redis_client.blpop(REDIS_PENDING_QUEUE, timeout=3600)
            if task is None: continue
            _, album_id = task
            current_task_data = json.dumps({ "id": album_id, "status": "正在下载...", "started_at": time.time() })
            redis_client.set(REDIS_CURRENT_TASK, current_task_data)
            success, message = _perform_download(album_id)
            print(f"工作线程：处理完成 {album_id}, 结果: {success}, 信息: {message}")
            completed_task_data = json.dumps({ "id": album_id, "success": success, "message": message, "timestamp": int(time.time()) })
            redis_client.lpush(REDIS_COMPLETED_LIST, completed_task_data)
            redis_client.ltrim(REDIS_COMPLETED_LIST, 0, 19)
            redis_client.delete(REDIS_CURRENT_TASK)
        except redis.exceptions.ConnectionError as e:
            print(f"工作线程严重错误：无法连接到Redis！将暂停30秒后重试。错误: {e}")
            time.sleep(30)
        except Exception as e:
            print(f"工作线程遇到未捕获的严重异常: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(10)
    thread_status["download_worker"]["alive"] = False

@app.route('/api/health_check')
def health_check():
    now = time.time()
    worker_last_seen = thread_status["download_worker"]["last_seen"]
    monitor_last_seen = thread_status["io_monitor"]["last_seen"]
    return jsonify({ "status": "ok", "io_monitor": { "is_alive": thread_status["io_monitor"]["alive"], "seconds_since_last_seen": now - monitor_last_seen if monitor_last_seen else -1 }, "download_worker": { "is_alive": thread_status["download_worker"]["alive"], "seconds_since_last_seen": now - worker_last_seen if worker_last_seen else -1 }, "redis_pending_tasks": redis_client.llen(REDIS_PENDING_QUEUE) })

@app.route('/api/manga_manager', methods=['POST'])
def manga_manager():
    # 1. 权限检查：确保只有管理员能访问
    if not is_admin():
        abort(403) # Forbidden

    data = request.json
    action = data.get('action')
    payload = data.get('payload')

    if not action or not payload:
        return jsonify({"success": False, "error": "缺少 action 或 payload 参数"}), 400

    # --- 删除操作 ---
    if action == 'delete':
        dir_name = payload.get('dir_name')
        if not dir_name:
            return jsonify({"success": False, "error": "删除操作需要 dir_name"}), 400
        
        dir_path = os.path.join(DOWNLOAD_DIR, dir_name)
        # 安全性检查：确保我们只在 DOWNLOAD_DIR 内部操作
        if not os.path.abspath(dir_path).startswith(os.path.abspath(DOWNLOAD_DIR)):
            return jsonify({"success": False, "error": "检测到非法路径"}), 400

        try:
            if os.path.isdir(dir_path):
                shutil.rmtree(dir_path)
                print(f"管理员操作：已删除文件夹 {dir_path}")
                return jsonify({"success": True, "message": f"文件夹 {dir_name} 已删除"})
            else:
                return jsonify({"success": False, "error": "目标不是一个文件夹或已不存在"}), 404
        except Exception as e:
            print(f"管理员删除文件夹时出错: {e}")
            return jsonify({"success": False, "error": f"删除失败: {e}"}), 500

    # --- 重命名操作 ---
    elif action == 'rename':
        old_dir_name = payload.get('old_dir_name')
        new_dir_name = payload.get('new_dir_name')

        if not old_dir_name or not new_dir_name:
            return jsonify({"success": False, "error": "重命名操作需要 old_dir_name 和 new_dir_name"}), 400
        
        # 清理用户输入，防止路径遍历等安全问题
        # 只允许字母、数字、下划线、空格、点和中文字符
        import re
        safe_pattern = re.compile(r'^[a-zA-Z0-9_\s.\u4e00-\u9fa5-]+$')
        if not safe_pattern.match(new_dir_name):
            return jsonify({"success": False, "error": "新名称包含非法字符"}), 400

        old_path = os.path.join(DOWNLOAD_DIR, old_dir_name)
        new_path = os.path.join(DOWNLOAD_DIR, new_dir_name)

        # 安全性检查
        if not os.path.abspath(old_path).startswith(os.path.abspath(DOWNLOAD_DIR)):
            return jsonify({"success": False, "error": "检测到非法路径"}), 400
        if os.path.exists(new_path):
            return jsonify({"success": False, "error": "目标名称已存在"}), 409 # Conflict

        try:
            if os.path.isdir(old_path):
                os.rename(old_path, new_path)
                print(f"管理员操作：已重命名 {old_path} -> {new_path}")
                return jsonify({"success": True, "message": "重命名成功"})
            else:
                return jsonify({"success": False, "error": "目标文件夹不存在"}), 404
        except Exception as e:
            print(f"管理员重命名文件夹时出错: {e}")
            return jsonify({"success": False, "error": f"重命名失败: {e}"}), 500
    
    return jsonify({"success": False, "error": "未知的 action"}), 400

# --- 启动后台线程 ---
redis_lock_key = "jm:worker:startup_lock"
if redis_client.set(redis_lock_key, "locked", nx=True, ex=30):
    print("获取到启动锁，正在启动后台线程...")
    monitor_thread = Thread(target=io_monitor_thread, daemon=True)
    monitor_thread.start()
    worker_thread = Thread(target=download_worker, daemon=True)
    worker_thread.start()
else:
    print("启动锁已被其他worker持有，跳过后台线程启动。")

# --- 辅助函数 (增强版) ---
def read_json_file(filepath, default_type='list'):
    """
    一个更健壮的JSON文件读取函数。
    default_type可以是 'list' 或 'dict'。
    """
    with file_lock:
        if not os.path.exists(filepath):
            return [] if default_type == 'list' else {}
        # 即使文件存在，也可能为空
        if os.path.getsize(filepath) == 0:
            return [] if default_type == 'list' else {}
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return [] if default_type == 'list' else {}

def write_json_file(filepath, data):
    with file_lock:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

# ★ 新增：管理员身份验证辅助函数
# ★ 核心修改：管理员身份验证函数，现在检查Session
def is_admin():
    return session.get('is_admin') == True

# --- 初始化文件 (新增对urls.json的支持) ---
if not os.path.exists(MESSAGES_FILE): write_json_file(MESSAGES_FILE, [])
if not os.path.exists(IP_NICKNAMES_FILE): write_json_file(IP_NICKNAMES_FILE, {})
if not os.path.exists(URLS_FILE): write_json_file(URLS_FILE, [])

# ===================================================
#             ★ 新增：认证 (登录/登出) API ★
# ===================================================

# ★★★ 新增：用户个人主页路由 ★★★
@app.route('/profile/<string:username>')
@login_required # 确保只有登录用户才能查看个人主页
def profile_page(username):
    # 根据 URL 传入的 username 查找用户
    # .first_or_404() 是一个很棒的快捷方式：
    # 如果找到用户，就返回它；如果找不到，就自动返回一个 404 Not Found 页面。
    user_to_view = User.query.filter_by(username=username).first_or_404()
    
    # 将查找到的用户对象传递给新的 profile.html 模板
    return render_template('profile.html', user=user_to_view)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index_page'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password') # 建议获取以进行密码一致性检查
        code = request.form.get('code')

        # ★★★ 核心修改：检查用户名是否已被占用 ★★★
        if User.query.filter_by(username=username).first():
            flash('该用户名已被使用，请换一个。', 'error')
            return render_template('register.html') # 返回页面，保留用户输入（需要前端配合）

        # 检查邮箱是否已被注册 (这个逻辑您已经有了)
        if User.query.filter_by(email=email).first():
            flash('该邮箱已被注册', 'error')
            return render_template('register.html')

        # 验证码校验 (这个逻辑您已经有了)
        stored_code_info = session.get('verification_code', {})
        # ... (您的验证码校验逻辑)
        
        # 建议增加密码一致性检查
        if password != confirm_password:
            flash('两次输入的密码不一致', 'error')
            return render_template('register.html')

        # 创建新用户
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        session.pop('verification_code', None)
        login_user(new_user)
        flash('注册成功！', 'success')
        return redirect(url_for('index_page'))
        
    return render_template('register.html')
# ★★★ 2. 修改：旧的 /login 路由，现在用于标准用户登录 ★★★
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index_page'))
    if request.method == 'POST':
        username = request.form.get('username') # 可以是用户名或邮箱
        password = request.form.get('password')
        user = User.query.filter((User.username == username) | (User.email == username)).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index_page'))
        else:
            flash('用户名或密码错误！', 'error')
            return render_template('login.html')
            
    return render_template('login.html')

# ★★★ 3. 新增：管理员专用登录路由 (逻辑来自你旧的 /login) ★★★
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        # 这里我们用明文密码比对，更简单。
        if password and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('index_page'))
        else:
            # 你提供的 admin_login.html 有一个 error 变量
            return render_template('admin_login.html', error="管理员密码错误！")
    return render_template('admin_login.html')

# ★★★ 新增：管理员面板主页 ★★★
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    # 权限检查：确保只有管理员能访问
    if not is_admin():
        abort(403)
    
    # 查询数据库中所有用户
    all_users = User.query.order_by(User.id.asc()).all()
    
    return render_template('admin_dashboard.html', users=all_users)



# ★★★ 新增：导出所有用户信息的 API ★★★
@app.route('/admin/export_users')
@login_required
def admin_export_users():
    if not is_admin():
        abort(403)

    string_io = io.StringIO()
    writer = csv.writer(string_io)
    
    writer.writerow(['ID', 'Username', 'Email'])
    
    users = User.query.all()
    for user in users:
        writer.writerow([user.id, user.username, user.email])
        
    string_io.seek(0)
    
    return Response(
        string_io,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=users_export.csv"}
    )

# ★★★ 4. 修改：/logout 路由，现在处理标准用户和管理员的同时登出 ★★★
@app.route('/logout')
@login_required # 确保只有登录用户才能访问
def logout():
    logout_user() # 登出标准用户
    session.pop('is_admin', None) # 同时清除管理员状态
    flash('您已成功注销。', 'info')
    return redirect(url_for('login'))

# ★★★ 5. 新增：专门的管理员登出路由 (用于彩蛋) ★★★
@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('管理员权限已注销。', 'info')
    return redirect(url_for('index_page'))


# ★★★ 6. 新增：发送验证码的API路由 (修改为最终版) ★★★
@app.route('/send_verification_code', methods=['POST'])
def send_verification_code():
    data = request.get_json()
    email = data.get('email')
    
    if not email:
        return jsonify({'success': False, 'message': '邮箱不能为空'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'success': False, 'message': '该邮箱已被注册'}), 400

    code = ''.join(random.choices(string.digits, k=6))
    
    # 存储验证码到 session，这部分你的代码已经很完善了
    session['verification_code'] = {
        'code': code,
        'email': email,
        'expiry': time.time() + 300  # 5分钟有效期
    }

    # ★★★ 核心修改：从打印到控制台改为发送邮件 ★★★
    try:
        # 1. 创建邮件消息对象
        msg = Message(
            subject="【马的个人空间】您的注册验证码",
            recipients=[email]
        )
        
        # 2. 编写美观的邮件内容 (HTML格式)
        msg.html = f"""
        <div style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
            <h2 style="color: #007bff;">欢迎注册马的个人空间！</h2>
            <p>您好！</p>
            <p>感谢您的注册（目前仅用于工程导论项目测试）。您的邮箱验证码是：</p>
            <p style="font-size: 24px; font-weight: bold; color: #6f42c1; letter-spacing: 2px; border: 1px dashed #ccc; padding: 10px; display: inline-block;">{code}</p>
            <p>该验证码将在5分钟内有效，请尽快完成注册。</p>
            <p>如果您没有请求此验证码，请忽略此邮件。</p>
            <hr style="border: none; border-top: 1px solid #eee;">
            <p style="font-size: 12px; color: #888;">此为系统自动发送的邮件，请勿直接回复。</p>
        </div>
        """
        
        # 3. 发送邮件
        mail.send(msg)
        
        # 4. 返回成功响应
        return jsonify({'success': True, 'message': '验证码已发送'})

    except Exception as e:
        # 捕获所有可能的异常，并记录错误
        print(f"!!! 邮件发送失败: {e}")
        # 返回一个对用户友好的错误信息
        return jsonify({'success': False, 'message': '邮件发送失败，请检查邮箱地址或联系管理员。'}), 500


# ★★★ 新增：用于前端实时检查用户名的 API ★★★
@app.route('/api/check_username', methods=['POST'])
def check_username():
    data = request.get_json()
    if not data or 'username' not in data:
        return jsonify({'exists': False, 'error': 'No username provided'}), 400

    username = data['username']
    user = User.query.filter_by(username=username).first()
    
    # 如果找到了用户，说明已存在
    return jsonify({'exists': user is not None})


# ===================================================
#             ★ 网址大全 API (权限检查已修改) ★
# ===================================================
@app.route('/api/urls', methods=['GET'])
def get_urls():
    urls = read_json_file(URLS_FILE)
    return jsonify(sorted(urls, key=lambda x: x.get('timestamp', 0), reverse=True))

@app.route('/api/urls', methods=['POST'])
def add_url():
    if not is_admin(): abort(403)
    data = request.json
    if not data or 'title' not in data or 'url' not in data or not data['title'] or not data['url']:
        return jsonify({'error': '标题和URL不能为空'}), 400
    urls = read_json_file(URLS_FILE)
    new_url = {'id': int(time.time() * 1000), 'title': data['title'][:100], 'url': data['url'][:500], 'timestamp': int(time.time())}
    urls.append(new_url)
    write_json_file(URLS_FILE, urls)
    return jsonify(new_url), 201

@app.route('/api/urls/<int:url_id>', methods=['DELETE'])
def delete_url(url_id):
    if not is_admin(): abort(403)
    urls = read_json_file(URLS_FILE)
    urls = [u for u in urls if u.get('id') != url_id]
    write_json_file(URLS_FILE, urls)
    return jsonify({'success': True})





# ===================================================
#             ★ 留言板 API (V3.0 - 用户系统集成版) ★
# ===================================================
print("正在注册留言板API路由...")

# --- 辅助 API ---
@app.route('/api/check_admin', methods=['GET'])
def check_admin_status():
    """检查当前会话是否为管理员。"""
    return jsonify({'isAdmin': is_admin()})

# --- 核心留言 API ---

@app.route('/api/messages', methods=['GET'])
def get_messages():
    """获取所有留言，并为每条留言和回复添加管理员标记。"""
    messages = read_json_file(MESSAGES_FILE)
    for msg in messages:
        # 为主留言添加标记 (兼容旧数据)
        if 'is_admin_post' not in msg:
            msg['is_admin_post'] = msg.get('ip_address') == ADMIN_IP

        # 为回复添加标记 (兼容旧数据)
        for reply in msg.get('replies', []):
            if 'is_admin_post' not in reply:
                reply['is_admin_post'] = reply.get('ip_address') == ADMIN_IP
                
    return jsonify(sorted(messages, key=lambda x: x.get('timestamp', 0), reverse=True))


@app.route('/api/messages', methods=['POST'])
@login_required
def post_message():
    """处理新留言的提交，仅限登录用户。"""
    data = request.json
    if not data or 'content' not in data or not data['content']:
        return jsonify({'error': '内容不能为空'}), 400
    
    messages = read_json_file(MESSAGES_FILE)
    
    new_message = {
        'id': int(time.time() * 1000),
        'author': current_user.username,
        'user_id': current_user.id,
        'content': data['content'][:500],
        'timestamp': int(time.time()),
        'likes': 0,
        'replies': [],
        'ip_address': request.remote_addr,
        'is_admin_post': is_admin()
    }
    
    messages.append(new_message)
    write_json_file(MESSAGES_FILE, messages)
    return jsonify(new_message), 201


@app.route('/api/messages/<int:message_id>', methods=['DELETE'])
@login_required
def delete_message(message_id):
    """删除指定ID的留言，仅限管理员。"""
    if not is_admin(): 
        abort(403)
        
    messages = read_json_file(MESSAGES_FILE)
    original_length = len(messages)
    messages = [m for m in messages if m.get('id') != message_id]
    
    if len(messages) < original_length:
        write_json_file(MESSAGES_FILE, messages)
        return jsonify({'success': True})
    else:
        return jsonify({'error': '留言未找到'}), 404


@app.route('/api/messages/<int:message_id>/like', methods=['POST'])
def like_message(message_id):
    """为指定ID的留言点赞。"""
    messages = read_json_file(MESSAGES_FILE)
    for msg in messages:
        if msg.get('id') == message_id:
            msg['likes'] = msg.get('likes', 0) + 1
            write_json_file(MESSAGES_FILE, messages)
            return jsonify({'success': True, 'likes': msg['likes']})
    return jsonify({'error': '留言未找到'}), 404


@app.route('/api/messages/<int:message_id>/reply', methods=['POST'])
@login_required
def reply_to_message(message_id):
    """为指定ID的留言添加回复，仅限登录用户。"""
    data = request.json
    if not data or 'content' not in data or not data['content']:
        return jsonify({'error': '内容不能为空'}), 400

    messages = read_json_file(MESSAGES_FILE)
    for msg in messages:
        if msg.get('id') == message_id:
            new_reply = {
                'id': int(time.time() * 1000),
                'author': current_user.username,
                'user_id': current_user.id,
                'content': data['content'][:300],
                'timestamp': int(time.time()),
                'ip_address': request.remote_addr,
                'is_admin_post': is_admin()
            }
            msg.setdefault('replies', []).append(new_reply)
            write_json_file(MESSAGES_FILE, messages)
            return jsonify({'success': True})
            
    return jsonify({'error': '留言未找到'}), 404

# --- 清理旧的、不再需要的路由 ---
# @app.route('/api/get_nickname', methods=['GET'])
# def get_nickname():
#     # 这个函数已不再需要，可以安全地注释掉或删除
#     return jsonify({}), 404

print("留言板API路由已注册完毕。")

# --- 文件保险箱配置 ---
FILE_VAULT_FOLDER = os.path.join(app.root_path, 'secure_uploads')
os.makedirs(FILE_VAULT_FOLDER, exist_ok=True)
FILE_VAULT_METADATA = 'file_vault.json'

if not os.path.exists(FILE_VAULT_METADATA):
    write_json_file(FILE_VAULT_METADATA, {})

# VVVV 新增：自动清理函数 VVVV
def cleanup_expired_files():
    """
    一个被定时任务调用的函数，用于清理已过期的文件。
    """
    # 使用 app.app_context() 确保在后台线程中也能访问应用配置
    with app.app_context():
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled cleanup for expired files...")
        
        # 使用您已有的带锁读写函数，确保线程安全
        all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
        
        # 不能在遍历字典时修改它，所以先收集要删除的键
        keys_to_delete = []
        # 24小时对应的秒数
        EXPIRATION_SECONDS = 24 * 60 * 60 

        for key, info in all_files.items():
            # 检查文件是否有首次下载记录
            if 'first_download_timestamp' in info:
                # 计算自首次下载以来经过了多少秒
                age_seconds = time.time() - info['first_download_timestamp']
                if age_seconds > EXPIRATION_SECONDS:
                    keys_to_delete.append(key)

        if keys_to_delete:
            print(f"Found {len(keys_to_delete)} expired file(s) to delete.")
            for key in keys_to_delete:
                file_info = all_files.get(key)
                if file_info:
                    # 1. 删除物理文件
                    try:
                        file_path = os.path.join(FILE_VAULT_FOLDER, file_info['stored_filename'])
                        os.remove(file_path)
                        print(f"  - Deleted file: {file_info['original_filename']}")
                    except FileNotFoundError:
                        print(f"  - File not found on disk, removing record: {file_info['original_filename']}")
                    except Exception as e:
                        print(f"  - Error deleting file {file_info['original_filename']}: {e}")
                    
                    # 2. 从元数据中删除记录
                    del all_files[key]
            
            # 3. 将更新后的元数据写回文件
            write_json_file(FILE_VAULT_METADATA, all_files)
        else:
            print("No expired files to clean up.")

# VVVV 新增：初始化并启动调度器 VVVV
scheduler = BackgroundScheduler(daemon=True)
# 添加任务：每隔1小时运行一次 cleanup_expired_files 函数
scheduler.add_job(cleanup_expired_files, 'interval', hours=1)
scheduler.start()
# AAAA 新增 AAAA


# --- 文件保险箱路由 (已更新) ---

@app.route('/file-vault.html')
def file_vault_page():
    all_files = {}
    if is_admin():
        all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
    return render_template('file-vault.html', all_files=all_files, is_admin=is_admin())

@app.route('/file-vault/upload', methods=['POST'])
def file_vault_upload():
    # ... 此函数内容保持不变 ...
    file = request.files.get('file')
    secret_key = request.form.get('secret_key')

    if not file or file.filename == '':
        flash('未选择文件！', 'error')
        return redirect(url_for('file_vault_page'))
    if not secret_key:
        flash('必须指定一个密钥！', 'error')
        return redirect(url_for('file_vault_page'))
    
    all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
    if secret_key in all_files:
        flash(f'密钥 "{secret_key}" 已被使用，请更换一个。', 'error')
        return redirect(url_for('file_vault_page'))

    original_filename = file.filename
    safe_server_filename = secure_filename(original_filename)
    stored_filename = f"{uuid.uuid4()}_{safe_server_filename}"
    file_path = os.path.join(FILE_VAULT_FOLDER, stored_filename)
    
    try:
        file.save(file_path)
        all_files[secret_key] = {
            'original_filename': original_filename,
            'stored_filename': stored_filename,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'uploader_ip': request.remote_addr
        }
        write_json_file(FILE_VAULT_METADATA, all_files)
        flash(f'文件上传成功！您的下载密钥是: {secret_key}', 'success')
    except Exception as e:
        flash(f'文件保存失败: {e}', 'error')
    return redirect(url_for('file_vault_page'))

@app.route('/file-vault/download', methods=['POST'])
def file_vault_download():
    """根据密钥下载文件，并记录首次下载时间"""
    secret_key = request.form.get('secret_key')
    if not secret_key:
        flash('请输入下载密钥！', 'error')
        return redirect(url_for('file_vault_page'))
        
    all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
    file_info = all_files.get(secret_key)
    
    if not file_info:
        flash('密钥无效或文件不存在！', 'error')
        return redirect(url_for('file_vault_page'))
        
    # ★★★ 核心修改：记录首次下载时间戳 ★★★
    # 检查 'first_download_timestamp' 字段是否存在
    if 'first_download_timestamp' not in file_info:
        # 如果不存在，说明这是第一次下载，记录当前时间戳
        file_info['first_download_timestamp'] = time.time()
        # 将更新后的信息写回 metadata 文件
        write_json_file(FILE_VAULT_METADATA, all_files)
        print(f"First download for file '{file_info['original_filename']}'. Expiration timer started.")

    try:
        return send_from_directory(
            FILE_VAULT_FOLDER,
            file_info['stored_filename'],
            as_attachment=True,
            download_name=file_info['original_filename']
        )
    except FileNotFoundError:
        flash('错误：文件记录存在但物理文件丢失，请联系管理员。', 'error')
        del all_files[secret_key]
        write_json_file(FILE_VAULT_METADATA, all_files)
        return redirect(url_for('file_vault_page'))

@app.route('/file-vault/delete/<string:key>', methods=['POST'])
def file_vault_delete(key):
    # ... 此函数内容保持不变 ...
    if not is_admin():
        flash('无权执行此操作！', 'error')
        abort(403)

    all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
    file_info = all_files.pop(key, None)

    if file_info:
        file_path = os.path.join(FILE_VAULT_FOLDER, file_info['stored_filename'])
        if os.path.exists(file_path):
            os.remove(file_path)
        write_json_file(FILE_VAULT_METADATA, all_files)
        flash(f'成功删除文件 (密钥: {key})。', 'success')
    else:
        flash('要删除的文件记录不存在。', 'error')
    return redirect(url_for('file_vault_page'))

@app.route('/file-vault/check-key', methods=['POST'])
def check_key_availability():
    data = request.get_json()
    if not data or 'key' not in data:
        return jsonify({'error': 'Missing key'}), 400
    secret_key = data['key']
    all_files = read_json_file(FILE_VAULT_METADATA, default_type='dict')
    if secret_key in all_files:
        return jsonify({'exists': True})
    else:
        return jsonify({'exists': False})

        

# --- 本地开发调试入口 ---
if __name__ == '__main__':
    # ★★★ 新增：在首次运行时创建数据库 ★★★
    with app.app_context():
        # 检查数据库文件是否存在
        if not os.path.exists(os.path.join(basedir, 'users.db')):
            print("数据库不存在，正在创建...")
            db.create_all()
            print("数据库 'users.db' 创建成功。")
    # 注意：在本地调试时，if 块外的线程启动逻辑可能不会被二次执行
    # 为了确保本地调试时线程能启动，我们在这里也加上启动代码
    if not thread_status["download_worker"]["alive"]:
        print("本地调试模式：启动后台线程...")
        monitor_thread = Thread(target=io_monitor_thread, daemon=True)
        monitor_thread.start()
        worker_thread = Thread(target=download_worker, daemon=True)
        worker_thread.start()
    app.run(host='0.0.0.0', port=5000, debug=True)