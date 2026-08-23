#app.py
import os
import shutil
import time
import subprocess
import json
import io
import sys
import traceback
import typing
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, jsonify, request # ... and other flask imports
import json
import numpy as np
import asyncio
import websockets
from threading import Thread
import csv
import random  # ★★★ 新增这一行 ★★★
import string  # ★★★ 新增这一行 ★★★
from threading import Lock, Thread
from collections import defaultdict
from types import SimpleNamespace
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

try:
    import jmcomic  # type: ignore
except ImportError:
    jmcomic = None  # 动态下载模块，不一定存在
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone
from google import genai
from dotenv import load_dotenv

load_dotenv()

# ★★★ torch/push_up_analysis 改为可选导入，避免服务器无法安装 torch ★★★
try:
    from push_up_analysis import analyze_pushup_frames
    PUSHUP_ANALYSIS_AVAILABLE = True
except ImportError:
    analyze_pushup_frames = None
    PUSHUP_ANALYSIS_AVAILABLE = False
    print("[WARNING] push_up_analysis 模块不可用（可能缺少 torch），俯卧撑分析功能将被禁用")

# --- Gemini AI 配置 ---
# 请注意：在实际生产环境中，建议将 API Key 存储在环境变量中，而不是直接写在代码里
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
ENV_DOWNLOAD_DIR = os.getenv("JM_DOWNLOAD_DIR")
if ENV_DOWNLOAD_DIR:
    DOWNLOAD_DIR = ENV_DOWNLOAD_DIR
else:
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
POSE_EXPORT_DIR = os.path.join(basedir, 'secure_uploads', 'pose_exports')
os.makedirs(POSE_EXPORT_DIR, exist_ok=True)

# --- SVG Data Loading ---
SVG_DATA_FILE = os.path.join(basedir, 'svg_data.json')
SVG_DATA = {}
try:
    with open(SVG_DATA_FILE, 'r', encoding='utf-8') as f:
        SVG_DATA = json.load(f)
    print(f"Loaded SVG data from {SVG_DATA_FILE}")
except Exception as e:
    print(f"Error loading SVG data: {e}")

ACTIONS_MUSCLE_LIBRARY = {
    "squat": {
        "aliases": ["squat", "深蹲"],
        "muscles": ["quadriceps", "hamstring", "gluteal", "calves", "tibialis"]
    },
    "pushup": {
        "aliases": ["pushup", "俯卧撑"],
        "muscles": ["chest", "triceps", "deltoids", "biceps", "abs"]
    },
    "jumping_jack": {
        "aliases": ["jumping_jack", "jumping jack", "开合跳"],
        "muscles": ["deltoids", "quadriceps", "calves", "trapezius", "upper-back"]
    },
    "plank": {
        "aliases": ["plank", "平板支撑", "平板"],
        "muscles": ["abs", "obliques", "gluteal", "deltoids", "trapezius"]
    },
    "lunge": {
        "aliases": ["lunge", "弓步蹲", "弓步"],
        "muscles": ["quadriceps", "hamstring", "gluteal", "calves"]
    }
}

SAMPLE_HISTORY_PRESETS = [
    {
        "action_name": "深蹲节奏训练",
        "duration_seconds": 480,
        "rep_count": 36,
        "avg_score": 92.5,
        "offset": timedelta(days=3, hours=2)
    },
    {
        "action_name": "俯卧撑稳定性挑战",
        "duration_seconds": 360,
        "rep_count": 28,
        "avg_score": 88.0,
        "offset": timedelta(days=2, hours=6)
    },
    {
        "action_name": "核心平板支撑",
        "duration_seconds": 540,
        "rep_count": 0,
        "avg_score": 94.2,
        "offset": timedelta(days=1, hours=4)
    }
]

SAMPLE_MUSCLE_PAYLOAD = {
    "gender": "female",
    "side": "front",
    "action": "squat",
    "targets": [
        "quadriceps",
        "hamstring",
        "gluteal",
        "deltoids",
        "abs"
    ],
    "issues": [
        "deltoids",
        "upper-back"
    ],
    "stats": {
        "quadriceps": {"sessions": 6, "avg_score": 92.0},
        "deltoids": {"sessions": 4, "avg_score": 72.0},
        "upper-back": {"sessions": 4, "avg_score": 68.5}
    },
    "source": "sample",
    "range_days": 7
}


def build_sample_sessions():
    """Generate lightweight sample sessions for UI previews."""
    now = datetime.now()
    sessions = []
    for preset in SAMPLE_HISTORY_PRESETS:
        sessions.append(
            SimpleNamespace(
                action_name=preset["action_name"],
                duration_seconds=preset["duration_seconds"],
                rep_count=preset["rep_count"],
                avg_score=preset["avg_score"],
                start_time=now - preset["offset"],
                end_time=now - preset["offset"] + timedelta(seconds=preset["duration_seconds"])
            )
        )
    return sessions


def resolve_action_key(action_name):
    """Map user-provided action names (English or Chinese) to canonical keys."""
    if not action_name:
        return None
    candidate = action_name.strip().lower()
    if not candidate:
        return None
    for key, meta in ACTIONS_MUSCLE_LIBRARY.items():
        if candidate == key:
            return key
        if any(alias in candidate for alias in meta.get("aliases", [])):
            return key
    return None


def get_muscles_for_action(action_key):
    if not action_key:
        return []
    return ACTIONS_MUSCLE_LIBRARY.get(action_key, {}).get("muscles", [])


def get_sessions_by_range(range_type: str):
    query = TrainingSession.query.filter_by(user_id=current_user.id)
    if range_type == 'last':
        last_session = query.order_by(TrainingSession.start_time.desc()).first()
        user_sessions = [last_session] if last_session else []
        range_label = "上一次训练"
    elif range_type == 'week':
        one_week_ago = datetime.now() - timedelta(days=7)
        user_sessions = (
            query.filter(TrainingSession.start_time >= one_week_ago)
            .order_by(TrainingSession.start_time.desc())
            .all()
        )
        range_label = "本周"
    else:
        user_sessions = query.order_by(TrainingSession.start_time.desc()).all()
        range_label = "所有记录"
    return user_sessions, range_label


def build_ai_history_report(range_type: str):
    user_sessions, range_label = get_sessions_by_range(range_type)
    if not user_sessions:
        return None, range_label, []

    prompt_sessions = user_sessions
    if range_type not in ('last', 'week'):
        prompt_sessions = user_sessions[:20]

    training_data_summary = [
        f"- 日期: {s.start_time.strftime('%Y-%m-%d %H:%M')}, 动作: {s.action_name}, 时长: {s.duration_seconds}秒, 次数: {s.rep_count}, 得分: {s.avg_score}"
        for s in prompt_sessions
    ]
    data_str = "\n".join(training_data_summary)

    prompt = f"""
    请你作为一名专业的运动康复与训练分析师，根据以下用户的【{range_label}】训练数据，生成一份详细的分析报告。

    用户: {current_user.username} (年龄: {current_user.age or '未知'}, 性别: {current_user.gender or '未知'}, 体重: {current_user.weight or '未知'}kg)

    训练记录:
    {data_str}

    请在报告中包含以下内容：
    1. **总体表现评估**
    2. **动作质量分析**
    3. **个性化建议**
    4. **鼓励与总结**

    使用Markdown格式输出，语气专业、亲切且具有建设性。
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        report_content = response.text
    except Exception as e:
        print(f"AI Report Generation Error: {e}")
        report_content = f"抱歉，智能报告生成失败。错误信息: {str(e)}\n\n以下是您的原始训练数据:\n{data_str}"

    return report_content, range_label, user_sessions




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
    # ★★★ 新增：用户身体数据 ★★★
    age = db.Column(db.Integer, nullable=True)
    gender = db.Column(db.String(10), nullable=True) # 'male', 'female', 'other'
    weight = db.Column(db.Float, nullable=True) # kg

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256:260000')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class TrainingSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    action_name = db.Column(db.String(50), nullable=False, default='深蹲')
    rep_count = db.Column(db.Integer, nullable=False)
    avg_score = db.Column(db.Float, nullable=False)

    user = db.relationship('User', backref=db.backref('training_sessions', lazy=True))

# ★★★ 生产环境自动初始化数据库（模型定义后）★★★
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"数据库初始化失败: {e}")

    def __repr__(self):
        return f'<TrainingSession {self.id} for User {self.user_id}>'
        
        
# ★★★ 新增：Flask-Login 需要的回调函数，用于从 session 中加载用户 ★★★
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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


@app.route('/tech-overview')
def tech_overview_page():
    """展示技术方案与团队介绍的独立页面"""
    return render_template('tech_overview.html', is_admin=is_admin())

@app.route('/training')
@login_required # 推荐加上这个装饰器，确保只有登录用户才能访问训练页面
def training_page():
    """渲染实时训练仪表盘页面"""
    # current_user 可以直接在模板中使用，无需手动传递
    # 我们只需要传递 session 中的 is_admin 状态
    is_admin_status = session.get('is_admin', False)
    gender_value = (current_user.gender or 'male').lower()
    user_gender = gender_value if gender_value in ('male', 'female') else 'male'
    return render_template('training.html',
                           is_admin=is_admin_status,
                           svg_data=SVG_DATA,
                           user_gender=user_gender)

@app.route('/api/process_pose', methods=['POST'])
@login_required
def process_pose_api():
    payload = request.get_json(silent=True) or {}
    try:
        result = evaluate_pose_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
    
# 也为 save_session 添加一个简单的占位符
@app.route('/api/save_session', methods=['POST'])
def save_session_api_placeholder():
    print(f"Received session data to save: {request.get_json()}")
    return jsonify({"status": "success"}), 200
    
@app.route('/api/save_training_session', methods=['POST'])
@login_required
def save_training_session():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid data"}), 400

    try:
        # 解析从前端传来的 ISO 格式时间字符串
        start_time_utc = datetime.fromisoformat(data['start_time'].replace('Z', '+00:00'))
        end_time_utc = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
        
        duration = (end_time_utc - start_time_utc).total_seconds()

        new_session = TrainingSession(
            user_id=current_user.id,
            start_time=start_time_utc.astimezone(timezone.utc).replace(tzinfo=None), # 存储不含时区的UTC时间
            end_time=end_time_utc.astimezone(timezone.utc).replace(tzinfo=None),
            duration_seconds=int(duration),
            action_name=data.get('action_name', '未知动作'),
            rep_count=int(data['rep_count']),
            avg_score=float(data['avg_score'])
        )
        db.session.add(new_session)
        db.session.commit()

        return jsonify({"message": "Session saved successfully"}), 201

    except Exception as e:
        db.session.rollback()
        print(f"Error saving session: {e}") # 在服务器日志中打印错误
        return jsonify({"error": "An error occurred while saving the session."}), 500

@app.route('/history')
@login_required
def history_page():
    range_type = request.args.get('range', 'all') # 'last', 'week', 'all'
    user_sessions, range_label = get_sessions_by_range(range_type)
    display_sessions = list(user_sessions)
    using_sample_data = False

    if not display_sessions:
        display_sessions = build_sample_sessions()
        using_sample_data = True

    # 2. User stats (sample-aware)
    total_sessions = len(display_sessions)
    total_duration = sum([s.duration_seconds for s in display_sessions])
    avg_score = sum([s.avg_score for s in display_sessions]) / total_sessions if total_sessions > 0 else 0
    
    # ★★★ 新增：卡路里计算 ★★★
    # 简单估算：MET * Weight(kg) * Time(hr)
    # 假设平均 MET = 5.0 (中等强度), 如果有 action_name 可以更细致
    user_weight = current_user.weight if current_user.weight else 70.0
    total_calories = 0
    METS = {'squat': 5.0, 'pushup': 8.0, 'jumping_jack': 8.0, 'plank': 3.5, 'lunge': 4.0}
    
    for s in display_sessions:
        # 尝试匹配动作名称，如果找不到默认 5.0
        # 注意：数据库存的 action_name 可能是中文或英文，这里做简单处理
        met = 5.0
        action_lower = s.action_name.lower() if s.action_name else ''
        if 'squat' in action_lower or '深蹲' in action_lower: met = METS['squat']
        elif 'pushup' in action_lower or '俯卧撑' in action_lower: met = METS['pushup']
        elif 'jumping' in action_lower or '开合跳' in action_lower: met = METS['jumping_jack']
        elif 'plank' in action_lower or '平板' in action_lower: met = METS['plank']
        elif 'lunge' in action_lower or '弓步' in action_lower: met = METS['lunge']
        
        duration_hours = s.duration_seconds / 3600.0
        total_calories += met * user_weight * duration_hours

    # 3. Global stats for comparison (All time)
    global_avg_score = db.session.query(db.func.avg(TrainingSession.avg_score)).scalar() or 0
    
    # 4. Prepare data for charts
    # 如果是 'last'，图表可能没意义，或者只显示那一次
    chart_sessions = display_sessions[:20][::-1] # 最多显示最近20条
    dates = [s.start_time.strftime('%m-%d %H:%M') for s in chart_sessions]
    scores = [s.avg_score for s in chart_sessions]
    
    gender_value = (current_user.gender or 'male').lower()
    user_gender = gender_value if gender_value in ('male', 'female') else 'male'

    return render_template('history.html', 
                           sessions=user_sessions, 
                           total_sessions=total_sessions,
                           total_duration=total_duration,
                           avg_score=round(avg_score, 1),
                           global_avg_score=round(global_avg_score, 1),
                           total_calories=round(total_calories, 1), # ★ Pass calories
                           dates=dates,
                           scores=scores,
                           current_range=range_type,
                           range_label=range_label,
                           report_content=None,
                           svg_data=SVG_DATA,
                           user_gender=user_gender,
                           can_generate_report=(len(user_sessions) > 0),
                           using_sample_data=using_sample_data,
                           sample_muscle_payload=SAMPLE_MUSCLE_PAYLOAD if using_sample_data else None) # 初始不生成报告，需点击按钮


@app.route('/api/muscle_usage')
@login_required
def muscle_usage_api():
    """Aggregate user muscle usage stats for highlighting."""
    action_param = request.args.get('action', '').strip()
    action_key = resolve_action_key(action_param) if action_param else None

    lookback_days = request.args.get('days', type=int)
    if not lookback_days or lookback_days <= 0:
        lookback_days = 30
    lookback_days = min(lookback_days, 180)
    since_ts = datetime.utcnow() - timedelta(days=lookback_days)

    sessions = (
        TrainingSession.query
        .filter(TrainingSession.user_id == current_user.id,
                TrainingSession.start_time >= since_ts)
        .order_by(TrainingSession.start_time.desc())
        .limit(200)
        .all()
    )

    muscle_usage = defaultdict(lambda: {"count": 0, "score_total": 0.0})
    action_histogram = defaultdict(int)

    for session in sessions:
        session_key = resolve_action_key(session.action_name)
        if not session_key:
            continue
        action_histogram[session_key] += 1
        if action_key and session_key != action_key:
            continue
        for slug in get_muscles_for_action(session_key):
            entry = muscle_usage[slug]
            entry["count"] += 1
            entry["score_total"] += session.avg_score or 0.0

    has_historical = any(entry["count"] > 0 for entry in muscle_usage.values())

    action_muscles_list = get_muscles_for_action(action_key) if action_key else None
    action_muscle_set = set(action_muscles_list) if action_muscles_list else None

    if action_key:
        base_muscles = list(action_muscles_list)
    elif muscle_usage:
        base_muscles = list(muscle_usage.keys())
    elif action_histogram:
        top_action = max(action_histogram.items(), key=lambda item: item[1])[0]
        base_muscles = get_muscles_for_action(top_action)
    else:
        base_muscles = []

    targets = list(base_muscles)
    if has_historical and targets:
        base_order = {slug: idx for idx, slug in enumerate(targets)}
        targets.sort(key=lambda slug: (
            -muscle_usage.get(slug, {}).get('count', 0),
            base_order.get(slug, 0)
        ))
    targets = list(dict.fromkeys(targets))

    issue_threshold = request.args.get('issue_threshold', type=int) or 75
    issues = []
    for slug, stats in muscle_usage.items():
        if stats['count'] == 0:
            continue
        avg_score = stats['score_total'] / stats['count']
        if avg_score < issue_threshold:
            if not action_key or (action_muscle_set and slug in action_muscle_set):
                issues.append(slug)

    stats_payload = {}
    for slug, stats in muscle_usage.items():
        if stats['count'] == 0:
            continue
        stats_payload[slug] = {
            "sessions": stats['count'],
            "avg_score": round(stats['score_total'] / stats['count'], 1)
        }

    return jsonify({
        "gender": current_user.gender or 'male',
        "side": "front",
        "action": action_key,
        "targets": targets,
        "issues": issues,
        "stats": stats_payload,
        "source": "historical" if has_historical else "default",
        "range_days": lookback_days
    })


@app.route('/api/pushup/analyze', methods=['POST'])
@login_required
def pushup_analysis_api():
    # ★★★ 检查 push_up_analysis 模块是否可用 ★★★
    if not PUSHUP_ANALYSIS_AVAILABLE or analyze_pushup_frames is None:
        return jsonify({"error": "俯卧撑分析功能暂不可用（服务器未安装 torch）"}), 503
    
    payload = request.get_json(silent=True) or {}
    frames = payload.get('frames')
    if not isinstance(frames, list) or len(frames) < 15:
        return jsonify({"error": "缺少有效的俯卧撑动作帧，请先完成一组俯卧撑。"}), 400

    max_frames = 1800
    if len(frames) > max_frames:
        frames = frames[-max_frames:]

    try:
        result = analyze_pushup_frames(frames, gemini_client=gemini_client)
    except FileNotFoundError:
        return jsonify({"error": "服务器缺少 push_up_modal.pth 模型文件，请联系管理员部署。"}), 500
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"分析失败: {exc}"}), 500

    for key in ("reps", "avg_score", "duration_seconds"):
        if key in payload:
            result[key] = payload[key]
    result["frames_used"] = len(frames)
    return jsonify(result)


def format_pose_export_payload(payload: dict[str, typing.Any], frames_per_block: int = 33) -> str:
    """Pretty print pose export JSON so frames stay readable."""
    items = list(payload.items())
    lines: list[str] = ["{"]
    for idx, (key, value) in enumerate(items):
        is_last = idx == len(items) - 1
        if key == "frames":
            frames = value or []
            lines.append('  "frames": [')
            for frame_idx, frame in enumerate(frames):
                frame_json = json.dumps(frame, ensure_ascii=False)
                frame_suffix = "," if frame_idx < len(frames) - 1 else ""
                lines.append(f"    {frame_json}{frame_suffix}")
                if frames_per_block and (frame_idx + 1) % frames_per_block == 0 and frame_idx < len(frames) - 1:
                    lines.append("")
            closing = "  ]" + ("" if is_last else ",")
            lines.append(closing)
        else:
            value_json = json.dumps(value, ensure_ascii=False)
            suffix = "," if not is_last else ""
            lines.append(f'  "{key}": {value_json}{suffix}')
    lines.append("}")
    return "\n".join(lines)


@app.route('/api/training/export_frames', methods=['POST'])
@login_required
def export_training_frames():
    data = request.get_json(silent=True) or {}
    frames = data.get('frames')
    action_name = data.get('action_name') or data.get('action_summary')
    if not isinstance(frames, list) or not frames:
        return jsonify({"error": "未找到可导出的帧数据"}), 400
    if not action_name:
        return jsonify({"error": "缺少动作名称，无法导出数据"}), 400

    normalized_frames = []
    for frame in frames:
        if not isinstance(frame, (list, tuple)):
            continue
        clean_points = []
        for point in frame:
            if isinstance(point, (list, tuple)) and len(point) >= 3:
                try:
                    clean_points.append([
                        round(float(point[0]), 5),
                        round(float(point[1]), 5),
                        round(float(point[2]), 5)
                    ])
                except (TypeError, ValueError):
                    clean_points.append([0.0, 0.0, 0.0])
        if clean_points:
            normalized_frames.append(clean_points)

    if not normalized_frames:
        return jsonify({"error": "动作帧数据格式不正确"}), 400

    timestamp = datetime.now(timezone.utc)
    username_slug = secure_filename(current_user.username) or f"user_{current_user.id}"
    action_slug = secure_filename(action_name) or "unknown"
    user_dir = os.path.join(POSE_EXPORT_DIR, username_slug, action_slug)
    os.makedirs(user_dir, exist_ok=True)

    record_id = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{username_slug}_{action_slug}"
    filename = f"{record_id}.jsonl"
    filepath = os.path.join(user_dir, filename)

    export_payload = {
        "id": record_id,
        "username": current_user.username,
        "action_name": action_name,
        "captured_at": timestamp.isoformat(),
        "duration_seconds": data.get('duration_seconds'),
        "reps": data.get('reps'),
        "frame_count": len(normalized_frames),
        "frames": normalized_frames
    }

    try:
        formatted_payload = format_pose_export_payload(export_payload, frames_per_block=33)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(formatted_payload + "\n")
    except OSError as exc:
        print(f"Export frames failed: {exc}")
        return jsonify({"error": "服务器保存动作数据失败"}), 500

    relative_dir = os.path.relpath(user_dir, POSE_EXPORT_DIR)
    return jsonify({
        "message": "动作数据已导出",
        "filename": filename,
        "action_name": action_name,
        "path": relative_dir.replace('\\', '/')
    })

@app.route('/generate_report')
@login_required
def generate_report():
    range_type = request.args.get('range', 'all')
    report_content, range_label, user_sessions = build_ai_history_report(range_type)
    if not user_sessions:
        flash('暂无训练记录，无法生成报告。', 'warning')
        return redirect(url_for('history_page', range=range_type))

    # 添加文件头信息
    final_content = f"AI 智能运动分析报告\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{report_content}"
    
    return Response(
        final_content,
        mimetype="text/plain", # 或者 "text/markdown" 如果用户下载后用支持md的编辑器查看
        headers={"Content-Disposition": f"attachment;filename=AI_Report_{current_user.username}_{datetime.now().strftime('%Y%m%d')}.md"}
    )


@app.route('/history/report_page')
@login_required
def history_report_page():
    range_type = request.args.get('range', 'all')
    report_content, range_label, user_sessions = build_ai_history_report(range_type)
    if not user_sessions:
        flash('暂无训练记录，无法生成报告。', 'warning')
        return redirect(url_for('history_page', range=range_type))

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return render_template(
        'history_report.html',
        report_content=report_content,
        range_label=range_label,
        generated_at=generated_at,
        range_type=range_type
    )

# ★★★ 新增：更新个人资料路由 ★★★
@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    age = request.form.get('age')
    gender = request.form.get('gender')
    weight = request.form.get('weight')

    try:
        if age: current_user.age = int(age)
        if gender: current_user.gender = gender
        if weight: current_user.weight = float(weight)
        
        db.session.commit()
        flash('个人资料已更新', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败: {str(e)}', 'error')
    
    return redirect(url_for('profile_page', username=current_user.username))

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
        if jmcomic is None:
            return False, "服务器缺少 jmcomic 依赖，无法执行下载"
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


# --- 启动后台线程 ---
redis_lock_key = "jm:worker:startup_lock"
try:
    # 尝试连接 Redis
    redis_client.ping()
    
    if redis_client.set(redis_lock_key, "locked", nx=True, ex=30):
        print("获取到启动锁，正在启动后台线程...")
        monitor_thread = Thread(target=io_monitor_thread, daemon=True)
        monitor_thread.start()
        worker_thread = Thread(target=download_worker, daemon=True)
        worker_thread.start()
    else:
        print("启动锁已被其他worker持有，跳过后台线程启动。")
except (redis.exceptions.ConnectionError, redis.exceptions.BusyLoadingError) as e:
    print(f"警告: 无法连接到 Redis 服务器 ({e})。")
    print("后台任务处理线程将不会启动。如果您不需要后台下载功能，可以忽略此警告。")
    print("系统将以【无Redis模式】运行。")
    
    # 创建一个 Mock Redis 客户端以防止视图函数报错
    class MockRedis:
        def __getattr__(self, name):
            def method(*args, **kwargs):
                return None
            return method
        def llen(self, key): return 0
        def get(self, key): return None
        def set(self, key, value, **kwargs): return True
        
    redis_client = MockRedis()

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



# --- 辅助 API ---
@app.route('/api/check_admin', methods=['GET'])
def check_admin_status():
    """检查当前会话是否为管理员。"""
    return jsonify({'isAdmin': is_admin()})


# ------------------------------------------------------------------- #
# ★ 1. 将您伙伴的 WebSocket 逻辑封装起来 ★
# ------------------------------------------------------------------- #

def calculate_angle(p1_coords, p2_coords, p3_coords):
    p1 = np.array(p1_coords)
    p2 = np.array(p2_coords)
    p3 = np.array(p3_coords)
    v1 = p1 - p2
    v2 = p3 - p2
    dot_product = np.dot(v1, v2)
    magnitude_v1 = np.linalg.norm(v1)
    magnitude_v2 = np.linalg.norm(v2)
    if magnitude_v1 == 0 or magnitude_v2 == 0:
        return 180.0
    cosine_angle = np.clip(dot_product / (magnitude_v1 * magnitude_v2), -1.0, 1.0)
    angle_degrees = np.degrees(np.arccos(cosine_angle))
    return angle_degrees


LEFT_LANDMARK_INDICES = {
    "shoulder": 11,
    "elbow": 13,
    "wrist": 15,
    "hip": 23,
    "knee": 25,
    "ankle": 27
}


def ensure_triplet(value):
    """Normalize incoming landmark formats into simple XYZ triplets."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [float(value[0]), float(value[1]), float(value[2])]
    if isinstance(value, dict):
        return [
            float(value.get('x', 0.0)),
            float(value.get('y', 0.0)),
            float(value.get('z', 0.0))
        ]
    return [0.0, 0.0, 0.0]


def evaluate_pose_payload(payload: dict) -> dict:
    """Shared scoring routine for both HTTP and WebSocket transports."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid pose payload")

    action = payload.get("action") or "pushup"
    if isinstance(action, str):
        action = action.strip().lower() or "pushup"
    else:
        action = "pushup"

    landmarks = payload.get("landmarks") if isinstance(payload.get("landmarks"), list) else None

    def from_landmarks(index):
        if landmarks and 0 <= index < len(landmarks):
            return ensure_triplet(landmarks[index])
        return [0.0, 0.0, 0.0]

    def point_for(name):
        if name in payload:
            return ensure_triplet(payload[name])
        idx = LEFT_LANDMARK_INDICES.get(name)
        if idx is not None:
            return from_landmarks(idx)
        return [0.0, 0.0, 0.0]

    shoulder = point_for("shoulder")
    elbow = point_for("elbow")
    wrist = point_for("wrist")
    hip = point_for("hip")
    knee = point_for("knee")
    ankle = point_for("ankle")

    errors = []
    angles = {}

    if action in ('pushup', 'plank'):
        elbow_angle = calculate_angle(shoulder, elbow, wrist)
        p_virtual = [hip[0], hip[1] + 10, hip[2]]
        torso_angle = calculate_angle(shoulder, hip, p_virtual)
        angles["elbow_s_e_w"] = round(elbow_angle, 2)
        angles["torso_flatness"] = round(torso_angle, 2)
        if elbow_angle > 120.0:
            errors.append("ERROR_ELBOW_FLARE: 手肘外扩，请夹紧手臂")
        if torso_angle < 160.0:
            errors.append("ERROR_TORSO_FLATNESS: 身体未成直线(塌腰/翘臀)")
    elif action == 'squat':
        knee_angle = calculate_angle(hip, knee, ankle)
        angles["knee_angle"] = round(knee_angle, 2)

    score = max(60, 100 - len(errors) * 20)
    return {
        "score": score,
        "errors": errors,
        "action": action,
        "angles": angles
    }

async def websocket_handler(websocket):
    """Handle incoming realtime scoring streams."""
    print("WebSocket client connected.")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                result = evaluate_pose_payload(data)
                await websocket.send(json.dumps(result))

            except json.JSONDecodeError:
                print("WebSocket received invalid JSON.")
            except Exception as e:
                print(f"An error occurred in WebSocket handler: {e}")
    finally:
        print("WebSocket client disconnected.")

# =================================================================== #
#                  ★★ WebSocket Server Logic (Final Version) ★★
# =================================================================== #

# 全局变量来持有 WebSocket 服务器线程的引用
ws_thread = None

def run_websocket_server():
    """此函数在独立的线程中运行，负责启动和维持 asyncio WebSocket 服务器。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def main_server():
        print("[WebSocket Server] Starting...")
        async with websockets.serve(websocket_handler, "0.0.0.0", 14427) as server:
            print(f"[WebSocket Server] Successfully started and listening on 0.0.0.0:14427")
            await asyncio.Future()  # Run forever

    try:
        loop.run_until_complete(main_server())
    except OSError as e:
        print(f"!!! [WebSocket Server] CRITICAL ERROR: Could not bind to port 14427. Error: {e}")
        traceback.print_exc()
        # 异常退出，让 systemd 知道启动失败
        os._exit(1)
    except Exception as e:
        print(f"!!! [WebSocket Server] An unexpected critical error occurred: {e}")
        traceback.print_exc()
        os._exit(1)

def start_websocket_server_once():
    """检查并启动 WebSocket 服务器线程（如果它还没运行）。"""
    global ws_thread
    if ws_thread is None or not ws_thread.is_alive():
        print("[Main Thread] Creating and starting WebSocket server thread...")
        ws_thread = Thread(target=run_websocket_server, daemon=True)
        ws_thread.start()
        print("[Main Thread] WebSocket server thread has been started.")

# =================================================================== #
#                        ★★ Script Entry Point ★★
# =================================================================== #

if __name__ == '__main__':
    # ★★★ 确保数据库表已创建 ★★★
    with app.app_context():
        db.create_all()
        print("Database tables created (if not existed).")

    print("==============================================")
    print(">>> Script is being run directly.            <<<")
    print(">>> Launching WebSocket server & Flask App...<<<")
    print("==============================================")
    
    # 1. 启动 WebSocket 服务器 (后台线程)
    start_websocket_server_once()
    
    # 2. 启动 Flask Web 服务器 (主线程阻塞)
    # host='0.0.0.0' 允许局域网访问
    # port=5000 是默认端口
    # debug=True 方便开发调试
    app.run(host='0.0.0.0', port=5000, debug=False)