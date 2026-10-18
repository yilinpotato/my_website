import os
from providers import get_text_client
import random
import re
import secrets
import string
import io
import sys
import shutil
import tempfile
import uuid
import ast
import threading
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash


def _ensure_ffmpeg_on_path():
    """Best-effort ensure ffmpeg is discoverable.

    Some Windows launchers (VS Code tasks/services) may not inherit the same PATH
    as your interactive shell where `ffmpeg` works.
    Set env var FFMPEG_PATH to either:
    - full path to ffmpeg.exe, or
    - directory containing ffmpeg.exe
    """

    raw = (os.getenv('FFMPEG_PATH') or '').strip().strip('"')
    if not raw:
        return
    p = Path(raw)
    ffmpeg_dir: Path | None = None
    if p.is_file() and p.name.lower().startswith('ffmpeg'):
        ffmpeg_dir = p.parent
    elif p.is_dir():
        ffmpeg_dir = p
    if not ffmpeg_dir:
        return

    try:
        cur = os.environ.get('PATH', '')
        add = str(ffmpeg_dir)
        if add and add.lower() not in cur.lower():
            os.environ['PATH'] = add + os.pathsep + cur
    except Exception:
        return


_ensure_ffmpeg_on_path()


app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}, r"/ai/api/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
)

# --- Configuration -------------------------------------------------------
app.config.update(
    SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL', 'sqlite:///ai_study.db'),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAIL_SERVER=os.getenv('MAIL_SERVER', 'smtp.163.com'),
    MAIL_PORT=int(os.getenv('MAIL_PORT', 465)),
    MAIL_USE_SSL=True,
    MAIL_USERNAME=os.getenv('MAIL_USERNAME', ''),
    MAIL_PASSWORD=os.getenv('MAIL_PASSWORD', ''),
    MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER', ''),
    MAIL_SUPPRESS_SEND=os.getenv('MAIL_SUPPRESS_SEND', 'False').lower() == 'true',
    ERROR_BOOK_UPLOAD_DIR=os.getenv('ERROR_BOOK_UPLOAD_DIR', os.path.join(app.instance_path, 'uploads', 'error_book')),
    GEMINI_API_KEY=os.getenv('GEMINI_API_KEY') or os.getenv('DEEPSEEK_API_KEY', ''),
    GEMINI_MODEL=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),

    # PaddleOCR (PaddleOCR 3.x pipeline) optional overrides for offline/air-gapped env
    OCR_TEXT_DETECTION_MODEL_NAME=os.getenv('OCR_TEXT_DETECTION_MODEL_NAME'),
    OCR_TEXT_DETECTION_MODEL_DIR=os.getenv('OCR_TEXT_DETECTION_MODEL_DIR'),
    OCR_TEXT_RECOGNITION_MODEL_NAME=os.getenv('OCR_TEXT_RECOGNITION_MODEL_NAME'),
    OCR_TEXT_RECOGNITION_MODEL_DIR=os.getenv('OCR_TEXT_RECOGNITION_MODEL_DIR'),
    OCR_LANG=os.getenv('OCR_LANG'),
    OCR_VERSION=os.getenv('OCR_VERSION'),
)

db = SQLAlchemy(app)
mail = Mail(app)


# --- Subject normalization (dashboard-friendly) -------------------------
# Keep subject values stable so one concept doesn't fragment across labels.
SUBJECT_CHOICES: list[str] = [
    '语文',
    '数学',
    '英语',
    '物理',
    '化学',
    '生物',
    '历史',
    '地理',
    '政治',
    '道德与法治',
    '科学',
    '信息技术',
    '通用技术',
    '体育与健康',
    '音乐',
    '美术',
    '劳动',
    '心理健康',
    '书法',
    '综合实践',
    '研究性学习',
    '校本课程',
    '地方课程',
    '少儿编程',
    '日语',
    '俄语',
    '法语',
    '德语',
    '经济与金融',
    '未分类',
]

_SUBJECT_ALIASES: dict[str, str] = {
    '中文': '语文',
    '汉语': '语文',
    '国文': '语文',
    '语文作文': '语文',
    '作文': '语文',
    '数学(奥数)': '数学',
    '奥数': '数学',
    '英语口语': '英语',
    '英语听力': '英语',
    '生物学': '生物',
    '历史学': '历史',
    '地理学': '地理',
    '思想政治': '政治',
    '思政': '政治',
    '道法': '道德与法治',
    '品德与社会': '道德与法治',
    '品德与生活': '道德与法治',
    '信息科技': '信息技术',
    '计算机': '信息技术',
    '电脑': '信息技术',
    '体育': '体育与健康',
    '体育健康': '体育与健康',
    '健康': '体育与健康',
    '心理': '心理健康',
    '综合实践活动': '综合实践',
    '编程': '少儿编程',
    '其他': '未分类',
    '其它': '未分类',
}


def normalize_subject(subject: Any) -> str:
    if subject is None:
        return '未分类'
    s = str(subject).strip()
    if not s:
        return '未分类'

    # Common prefixes
    for prefix in ('学科：', '科目：', '科目:', '学科:', 'subject:', 'Subject:'):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
            break

    # Direct hit
    if s in SUBJECT_CHOICES:
        return s

    # Alias exact
    if s in _SUBJECT_ALIASES:
        return _SUBJECT_ALIASES[s]

    # English hints
    low = s.lower()
    if 'math' in low:
        return '数学'
    if 'english' in low:
        return '英语'
    if 'physics' in low:
        return '物理'
    if 'chem' in low:
        return '化学'
    if 'bio' in low:
        return '生物'
    if 'history' in low:
        return '历史'
    if 'geography' in low:
        return '地理'

    # Alias contains
    for key, value in _SUBJECT_ALIASES.items():
        if key and key in s:
            return value

    # Choice contains
    for choice in SUBJECT_CHOICES:
        if choice != '未分类' and choice in s:
            return choice

    return '未分类'


# --- Models --------------------------------------------------------------
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(20), default='student')
    age = db.Column(db.Integer)
    courses = db.Column(db.String(255))
    linked_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    linked_user = db.relationship('User', remote_side=[id], uselist=False)
    verified = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(10))
    verification_expires = db.Column(db.DateTime)
    auth_token = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def courses_list(self):
        if not self.courses:
            return []
        return [item.strip() for item in self.courses.split(',') if item.strip()]

    def set_courses(self, items):
        if isinstance(items, list):
            self.courses = ','.join(items)
        elif isinstance(items, str):
            self.courses = items

    def to_safe_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'username': self.username,
            'display_name': self.display_name,
            'role': self.role,
            'age': self.age,
            'courses': self.courses_list,
            'verified': self.verified,
            'linked_user': self.linked_user.to_linked_dict() if self.linked_user else None,
        }

    def to_linked_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'username': self.username,
            'display_name': self.display_name,
            'role': self.role,
        }


class ErrorBookEntry(db.Model):
    __tablename__ = 'error_book_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    user = db.relationship('User', backref=db.backref('error_book_entries', lazy=True))

    title = db.Column(db.String(200))
    subject = db.Column(db.String(80))
    status = db.Column(db.String(30), default='created')
    verdict = db.Column(db.String(255))

    image_original_name = db.Column(db.String(255))
    image_mimetype = db.Column(db.String(80))
    image_size = db.Column(db.Integer)
    image_sha256 = db.Column(db.String(64))
    image_blob = db.Column(db.LargeBinary)

    ocr_text = db.Column(db.Text)
    ocr_json = db.Column(db.Text)
    ai_analysis = db.Column(db.Text)

    quiz_json = db.Column(db.Text)
    quiz_created_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_summary(self):
        return {
            'id': self.id,
            'title': self.title or '未命名错题',
            'subject': self.subject or '',
            'status': self.status,
            'verdict': self.verdict or '',
            'created_at': isoformat_utc_z(self.created_at),
            'image_url': f"/api/error-book/entries/{self.id}/image" if self.image_blob else None,
        }

    def to_detail(self):
        payload = self.to_summary()
        quiz_payload = None
        if (self.quiz_json or '').strip():
            stored = _loads_lenient_object(self.quiz_json)
            if stored and isinstance(stored, dict):
                quiz_payload, _ = _validate_quiz_dict(stored)
        payload.update(
            {
                'ocr_text': self.ocr_text or '',
                'ai_analysis': self.ai_analysis or '',
                'ocr_json': self.ocr_json or '',
                'quiz_created_at': isoformat_utc_z(self.quiz_created_at),
                'quiz': quiz_payload,
            }
        )
        return payload


class NoteAssistantEntry(db.Model):
    __tablename__ = 'note_assistant_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    user = db.relationship('User', backref=db.backref('note_assistant_entries', lazy=True))

    # Recording session identifier for chunked "near real-time" transcription
    session_id = db.Column(db.String(64), unique=True, index=True)

    title = db.Column(db.String(200))
    subject = db.Column(db.String(80))
    focus_tag = db.Column(db.String(80))
    status = db.Column(db.String(30), default='created')

    audio_original_name = db.Column(db.String(255))
    audio_mimetype = db.Column(db.String(80))
    audio_size = db.Column(db.Integer)
    audio_sha256 = db.Column(db.String(64))

    transcript_text = db.Column(db.Text)
    summary_json = db.Column(db.Text)
    tasks_json = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_summary(self):
        transcript_preview = (self.transcript_text or '').strip().replace('\r', '')
        if len(transcript_preview) > 160:
            transcript_preview = transcript_preview[:160] + '…'
        return {
            'id': self.id,
            'title': self.title or '未命名笔记',
            'subject': self.subject or '',
            'focus_tag': self.focus_tag or '',
            'status': self.status or '',
            'created_at': isoformat_utc_z(self.created_at),
            'updated_at': isoformat_utc_z(self.updated_at),
            'transcript_preview': transcript_preview,
        }

    def to_detail(self):
        payload = self.to_summary()
        summary = _loads_lenient_object(self.summary_json or '') if (self.summary_json or '').strip() else None
        tasks = None
        if (self.tasks_json or '').strip():
            tasks_obj = _loads_lenient_object(self.tasks_json or '')
            if isinstance(tasks_obj, dict):
                tasks = tasks_obj.get('tasks')
        payload.update(
            {
                'transcript': (self.transcript_text or ''),
                'summary': summary,
                'tasks': tasks if isinstance(tasks, list) else [],
            }
        )
        return payload


class KnowledgeNode(db.Model):
    __tablename__ = 'knowledge_nodes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    user = db.relationship('User', backref=db.backref('knowledge_nodes', lazy=True))

    subject = db.Column(db.String(80), index=True)
    name = db.Column(db.String(80), nullable=False, index=True)
    kind = db.Column(db.String(30), default='concept')  # chapter|concept|method|mistake

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'subject', 'name', name='uq_knowledge_node_user_subject_name'),)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'subject': self.subject or '',
            'name': self.name or '',
            'kind': self.kind or '',
            'created_at': isoformat_utc_z(self.created_at),
            'last_seen_at': isoformat_utc_z(self.last_seen_at),
        }


class MindMapSnapshot(db.Model):
    __tablename__ = 'mind_map_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    user = db.relationship('User', backref=db.backref('mind_map_snapshots', lazy=True))

    source_type = db.Column(db.String(30), nullable=False)  # note|error_book
    source_id = db.Column(db.Integer, nullable=False)
    root_node_id = db.Column(db.Integer, db.ForeignKey('knowledge_nodes.id'))
    root_node = db.relationship('KnowledgeNode', foreign_keys=[root_node_id])

    map_json = db.Column(db.Text)  # {nodes:[...], edges:[...]}
    highlights_json = db.Column(db.Text)
    related_json = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BindRequest(db.Model):
    __tablename__ = 'bind_requests'

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    parent = db.relationship('User', foreign_keys=[parent_id])
    student = db.relationship('User', foreign_keys=[student_id])

    status = db.Column(db.String(20), default='pending')  # pending|approved|rejected|canceled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime)

    __table_args__ = (db.UniqueConstraint('parent_id', 'student_id', 'status', name='uq_bind_request_parent_student_status'),)

    def to_student_dict(self):
        return {
            'id': self.id,
            'status': self.status,
            'created_at': isoformat_utc_z(self.created_at),
            'parent': self.parent.to_linked_dict() if self.parent else None,
        }

    def to_parent_dict(self):
        return {
            'id': self.id,
            'status': self.status,
            'created_at': isoformat_utc_z(self.created_at),
            'student': self.student.to_linked_dict() if self.student else None,
        }


def ensure_error_book_entry_schema():
    """Best-effort migration for SQLite (development).

    SQLAlchemy create_all() won't add new columns to existing tables.
    This keeps existing user DBs working when we add quiz persistence.
    """

    try:
        if db.engine.dialect.name != 'sqlite':
            return
        with db.engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(error_book_entries)")).fetchall()
            existing = {row[1] for row in cols}  # row[1] is column name
            if 'quiz_json' not in existing:
                conn.execute(text('ALTER TABLE error_book_entries ADD COLUMN quiz_json TEXT'))
            if 'quiz_created_at' not in existing:
                conn.execute(text('ALTER TABLE error_book_entries ADD COLUMN quiz_created_at DATETIME'))
            conn.commit()
    except Exception as exc:
        app.logger.warning('Schema migration skipped/failed: %s', exc)


def ensure_note_assistant_schema():
    """Best-effort migration for SQLite (development) for note_assistant_entries."""

    try:
        if db.engine.dialect.name != 'sqlite':
            return
        with db.engine.connect() as conn:
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            names = {row[0] for row in tables.fetchall()}
            if 'note_assistant_entries' not in names:
                return

            cols = conn.execute(text('PRAGMA table_info(note_assistant_entries)')).fetchall()
            existing = {row[1] for row in cols}

            # Columns added incrementally
            if 'session_id' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN session_id VARCHAR(64)'))
            if 'title' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN title VARCHAR(200)'))
            if 'subject' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN subject VARCHAR(80)'))
            if 'focus_tag' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN focus_tag VARCHAR(80)'))
            if 'status' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN status VARCHAR(30)'))
            if 'audio_original_name' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN audio_original_name VARCHAR(255)'))
            if 'audio_mimetype' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN audio_mimetype VARCHAR(80)'))
            if 'audio_size' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN audio_size INTEGER'))
            if 'audio_sha256' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN audio_sha256 VARCHAR(64)'))
            if 'transcript_text' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN transcript_text TEXT'))
            if 'summary_json' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN summary_json TEXT'))
            if 'tasks_json' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN tasks_json TEXT'))
            if 'created_at' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN created_at DATETIME'))
            if 'updated_at' not in existing:
                conn.execute(text('ALTER TABLE note_assistant_entries ADD COLUMN updated_at DATETIME'))

            conn.commit()
    except Exception as exc:
        app.logger.warning('Note schema migration skipped/failed: %s', exc)


def ensure_knowledge_schema():
    """Best-effort migration for SQLite (development) for knowledge_nodes/mind_map_snapshots."""

    try:
        if db.engine.dialect.name != 'sqlite':
            return
        with db.engine.connect() as conn:
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            names = {row[0] for row in tables.fetchall()}
            if 'knowledge_nodes' in names:
                cols = conn.execute(text('PRAGMA table_info(knowledge_nodes)')).fetchall()
                existing = {row[1] for row in cols}
                if 'last_seen_at' not in existing:
                    conn.execute(text('ALTER TABLE knowledge_nodes ADD COLUMN last_seen_at DATETIME'))
            if 'mind_map_snapshots' in names:
                cols = conn.execute(text('PRAGMA table_info(mind_map_snapshots)')).fetchall()
                existing = {row[1] for row in cols}
                if 'highlights_json' not in existing:
                    conn.execute(text('ALTER TABLE mind_map_snapshots ADD COLUMN highlights_json TEXT'))
                if 'related_json' not in existing:
                    conn.execute(text('ALTER TABLE mind_map_snapshots ADD COLUMN related_json TEXT'))
            conn.commit()
    except Exception as exc:
        app.logger.warning('Knowledge schema migration skipped/failed: %s', exc)


def ensure_bind_schema():
    """Best-effort migration for bind_requests."""

    try:
        if db.engine.dialect.name != 'sqlite':
            return
        with db.engine.connect() as conn:
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            names = {row[0] for row in tables.fetchall()}
            if 'bind_requests' in names:
                cols = conn.execute(text('PRAGMA table_info(bind_requests)')).fetchall()
                existing = {row[1] for row in cols}
                if 'responded_at' not in existing:
                    conn.execute(text('ALTER TABLE bind_requests ADD COLUMN responded_at DATETIME'))
            conn.commit()
    except Exception as exc:
        app.logger.warning('Bind schema migration skipped/failed: %s', exc)


# --- Helpers -------------------------------------------------------------
def random_display_name():
    adjectives = ['星辰', '追光', '逐梦', '晨曦', '云杉', '青栀']
    nouns = ['学员', '同学', '少年', '伙伴', '行者']
    return f"{random.choice(adjectives)}{random.choice(nouns)}{random.randint(100, 999)}"


def generate_code(length=6):
    return ''.join(random.choices(string.digits, k=length))


def generate_token():
    return secrets.token_urlsafe(48)


def send_email(subject, recipients, body):
    try:
        msg = Message(subject=subject, recipients=recipients, body=body)
        mail.send(msg)
    except Exception as exc:  # pragma: no cover - 仅记录错误不影响主流程
        app.logger.error('邮件发送失败: %s', exc)


def get_auth_user():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ', 1)[1]
        if token:
            return User.query.filter_by(auth_token=token).first()
    return None


def require_auth():
    user = get_auth_user()
    if not user:
        return None, jsonify({'message': '未授权或登录已过期'}), 401
    return user, None, None


def get_error_book_access_user_ids(user: Any) -> list[int]:
    """Return user ids whose error-book entries are visible to current user.

    Rule:
    - student: only self
    - parent: self + linked children (students)
    """

    if not user:
        return []

    ids = [user.id]

    try:
        if (user.role or '') == 'parent':
            # Case A: link stored on parent side
            if user.linked_user and (user.linked_user.role or '') == 'student':
                ids.append(user.linked_user.id)

            # Case B: link stored on student side (potentially multiple children)
            children = User.query.filter_by(linked_user_id=user.id, role='student').all()
            for child in children:
                ids.append(child.id)
    except Exception:
        pass

    return sorted(set(int(x) for x in ids if x is not None))


def get_note_access_user_ids(user: Any) -> list[int]:
    # Same access rules as error book (self + children for parents)
    return get_error_book_access_user_ids(user)


def save_and_commit(entity):
    db.session.add(entity)
    db.session.commit()


def isoformat_utc_z(dt: Any) -> str | None:
    """Return ISO timestamp with explicit timezone (UTC, trailing 'Z').

    Our DB stores naive UTC datetimes (datetime.utcnow). Without a timezone
    suffix, browsers may parse them as local time and show a shifted value.
    """

    if not dt:
        return None
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _normalize_concept_name(value: Any) -> str:
    s = str(value or '').strip()
    if not s:
        return ''
    # collapse whitespace
    s = re.sub(r'\s+', ' ', s)
    # remove long punctuation tails
    s = s.strip(' \t\r\n，。；;：:、-—')
    if len(s) > 24:
        s = s[:24]
    return s


def get_or_create_knowledge_node(owner_user_id: int, subject: Any, name: Any, kind: str = 'concept') -> KnowledgeNode | None:
    subject_norm = normalize_subject(subject)
    name_norm = _normalize_concept_name(name)
    if not name_norm:
        return None

    node = KnowledgeNode.query.filter_by(user_id=owner_user_id, subject=subject_norm, name=name_norm).first()
    if node:
        node.last_seen_at = datetime.utcnow()
        if kind and (node.kind or '') == 'concept' and kind != 'concept':
            node.kind = kind
        save_and_commit(node)
        return node

    node = KnowledgeNode(
        user_id=owner_user_id,
        subject=subject_norm,
        name=name_norm,
        kind=kind or 'concept',
        last_seen_at=datetime.utcnow(),
    )
    save_and_commit(node)
    return node


def _extract_note_concepts(entry: 'NoteAssistantEntry') -> tuple[str, list[str]]:
    """Return (subject, concept_names) from a note entry.

    - key_terms are preferred as concept nodes
    - fallback: summary_points headings
    """

    subject = normalize_subject(entry.subject)
    concepts: list[str] = []

    summary_obj = _loads_lenient_object(entry.summary_json or '') if (entry.summary_json or '').strip() else None
    if isinstance(summary_obj, dict):
        key_terms = summary_obj.get('key_terms')
        if isinstance(key_terms, list):
            for t in key_terms:
                n = _normalize_concept_name(t)
                if n:
                    concepts.append(n)
        if not concepts:
            points = summary_obj.get('summary_points')
            if isinstance(points, list):
                for p in points:
                    s = str(p or '').strip()
                    if not s:
                        continue
                    head = s.split('：', 1)[0].strip() if '：' in s else s[:12]
                    n = _normalize_concept_name(head)
                    if n:
                        concepts.append(n)

    return subject, list(dict.fromkeys(concepts))[:16]


def _extract_error_concepts(entry: 'ErrorBookEntry') -> tuple[str, list[str]]:
    subject = normalize_subject(entry.subject)
    concepts: list[str] = []

    extracted = _extract_first_json_object(entry.ai_analysis or '')
    parsed = _loads_lenient_object(extracted) if extracted else None
    if isinstance(parsed, dict):
        mistakes = parsed.get('mistakes')
        if isinstance(mistakes, list):
            for m in mistakes:
                if not isinstance(m, dict):
                    continue
                c = _normalize_concept_name(m.get('concept'))
                if c:
                    concepts.append(c)
        key_points = parsed.get('key_points')
        if isinstance(key_points, list):
            for kp in key_points:
                c = _normalize_concept_name(kp)
                if c:
                    concepts.append(c)

    return subject, list(dict.fromkeys(concepts))[:16]


def upsert_knowledge_from_note(entry: 'NoteAssistantEntry'):
    try:
        subject, concepts = _extract_note_concepts(entry)
        for name in concepts:
            get_or_create_knowledge_node(entry.user_id, subject, name, kind='concept')
    except Exception:
        return


def upsert_knowledge_from_error(entry: 'ErrorBookEntry'):
    try:
        subject, concepts = _extract_error_concepts(entry)
        for name in concepts:
            get_or_create_knowledge_node(entry.user_id, subject, name, kind='concept')
    except Exception:
        return


USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{3,20}$')


_OCR_INSTANCE: Any = None
_WHISPER_PIPELINE: Any = None
_WHISPER_INIT_LOCK = threading.Lock()
_WHISPER_INIT_ERROR: str | None = None


class WhisperInitializingError(RuntimeError):
    pass


class WhisperUnavailableError(RuntimeError):
    pass


@app.route('/api/note/whisper/status', methods=['GET'])
def note_whisper_status():
    """Backend diagnostics for Whisper/ffmpeg.

    Use this to confirm the backend process environment (python/ffmpeg/model)
    when local scripts work but web requests fail.
    """

    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    resolved_model = os.getenv('WHISPER_MODEL') or 'openai/whisper-base'
    info: dict[str, Any] = {
        'whisper_model_env': os.getenv('WHISPER_MODEL') or '',
        'whisper_model_resolved': resolved_model,
        'ffmpeg_env': (os.getenv('FFMPEG_PATH') or '').strip(),
        'ffmpeg_in_path': bool(shutil.which('ffmpeg')),
        'ffmpeg_path': shutil.which('ffmpeg') or '',
        'pipeline_loaded': _WHISPER_PIPELINE is not None,
        'init_error': _WHISPER_INIT_ERROR or '',
        'python_executable': sys.executable,
    }

    try:
        import torch  # type: ignore

        info.update(
            {
                'torch_version': getattr(torch, '__version__', ''),
                'cuda_available': bool(torch.cuda.is_available()),
            }
        )
    except Exception as exc:
        info['torch_error'] = str(exc)

    if (request.args.get('init') or '').strip() == '1':
        try:
            _ = get_whisper_pipeline()
            info['pipeline_loaded'] = _WHISPER_PIPELINE is not None
            info['init_error'] = _WHISPER_INIT_ERROR or ''
        except Exception as exc:
            info['pipeline_loaded'] = _WHISPER_PIPELINE is not None
            info['init_error'] = str(exc)

    return jsonify(info)


def _strip_code_fence(text: str) -> str:
    trimmed = (text or '').strip()
    if trimmed.startswith('```') and trimmed.endswith('```'):
        lines = trimmed.splitlines()
        if len(lines) >= 2:
            return '\n'.join(lines[1:-1]).strip()
    return trimmed


def _extract_first_json_object(text: str) -> str | None:
    s = _strip_code_fence(text)
    start = s.find('{')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return s[start : i + 1].strip()
    return None


def _extract_first_json_array(text: str) -> str | None:
    s = _strip_code_fence(text)
    start = s.find('[')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return s[start : i + 1].strip()
    return None


def _loads_lenient_object(text: str) -> dict | None:
    """Parse a dict-like payload from LLM output.

    Tries strict JSON first, then falls back to Python-literal dict (single quotes, etc.).
    """

    if not text:
        return None
    s = text.strip()
    if not s:
        return None

    import json

    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    normalized = (
        s.replace('\u201c', '"')
        .replace('\u201d', '"')
        .replace('“', '"')
        .replace('”', '"')
        .strip()
    )

    try:
        obj = ast.literal_eval(normalized)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _validate_quiz_dict(parsed: dict) -> tuple[dict | None, str | None]:
    options = parsed.get('options')
    answer_index = parsed.get('answer_index')
    if not (isinstance(options, list) and len(options) == 4):
        return None, '练习题 options 格式错误'
    if not (isinstance(answer_index, int) and 0 <= answer_index <= 3):
        return None, '练习题 answer_index 错误'

    question = str(parsed.get('question') or '').strip()
    options_text = [str(item or '').strip() for item in options]
    if not question:
        return None, '练习题题干为空'
    if any(not item for item in options_text):
        return None, '练习题选项为空'

    payload = {
        'question': question,
        'options': options_text,
        'answer_index': answer_index,
        'explanation': str(parsed.get('explanation') or '').strip(),
        'topic': str(parsed.get('topic') or '').strip(),
    }
    return payload, None


def _generate_and_persist_quiz(entry: 'ErrorBookEntry') -> tuple[dict | None, str | None]:
    """Generate quiz from entry.ocr_text, persist to DB on success."""

    if not (entry.ocr_text or '').strip():
        return None, 'OCR 文本为空，无法生成练习题'

    try:
        quiz_text = run_gemini_quiz(entry.ocr_text or '')
    except Exception as exc:
        return None, f'练习题生成失败：{exc}'

    extracted = _extract_first_json_object(quiz_text)
    if not extracted:
        return None, '练习题返回格式非 JSON'

    parsed = _loads_lenient_object(extracted)
    if not parsed:
        return None, '练习题 JSON 解析失败'

    payload, err = _validate_quiz_dict(parsed)
    if err or not payload:
        return None, err or '练习题格式错误'

    try:
        import json

        entry.quiz_json = json.dumps(payload, ensure_ascii=False)
        entry.quiz_created_at = datetime.utcnow()
        save_and_commit(entry)
    except Exception as exc:
        app.logger.warning('Failed to persist quiz: %s', exc)

    return payload, None


def get_ocr_instance():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE

    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as exc:
        raise RuntimeError('PaddleOCR 未安装或不可用，请先安装 paddleocr/paddlepaddle') from exc

    kwargs: dict[str, Any] = {
        'use_doc_orientation_classify': False,
        'use_doc_unwarping': False,
        'use_textline_orientation': False,
    }

    # Allow overriding model download with local directories (recommended on Windows/offline)
    det_name = app.config.get('OCR_TEXT_DETECTION_MODEL_NAME')
    det_dir = app.config.get('OCR_TEXT_DETECTION_MODEL_DIR')
    rec_name = app.config.get('OCR_TEXT_RECOGNITION_MODEL_NAME')
    rec_dir = app.config.get('OCR_TEXT_RECOGNITION_MODEL_DIR')
    lang = app.config.get('OCR_LANG')
    ocr_version = app.config.get('OCR_VERSION')

    if det_name:
        kwargs['text_detection_model_name'] = det_name
    if det_dir:
        kwargs['text_detection_model_dir'] = det_dir
    if rec_name:
        kwargs['text_recognition_model_name'] = rec_name
    if rec_dir:
        kwargs['text_recognition_model_dir'] = rec_dir
    if lang:
        kwargs['lang'] = lang
    if ocr_version:
        kwargs['ocr_version'] = ocr_version

    try:
        _OCR_INSTANCE = PaddleOCR(**kwargs)
    except Exception as exc:
        raise RuntimeError(
            'OCR 初始化失败。若日志包含 “No available model hosting platforms detected”，说明模型下载被网络/代理阻断。\n'
            '解决方式：\n'
            '1) 确保能访问模型下载站点（需要外网/代理），或\n'
            '2) 手动下载模型到本地，并设置环境变量：\n'
            '   - OCR_TEXT_DETECTION_MODEL_DIR=...\n'
            '   - OCR_TEXT_RECOGNITION_MODEL_DIR=...\n'
            '（也可设置 *_MODEL_NAME 或 OCR_LANG/OCR_VERSION）'
        ) from exc
    return _OCR_INSTANCE


def get_whisper_pipeline():
    """Lazy init Whisper ASR pipeline.

    Uses Transformers Whisper models from a local directory by default.
    Requires torch + transformers installed, and ffmpeg available for mp3/webm decoding.
    """

    global _WHISPER_PIPELINE
    global _WHISPER_INIT_ERROR
    if _WHISPER_PIPELINE is not None:
        return _WHISPER_PIPELINE

    # If a previous init failed, fail fast unless user explicitly forces retry.
    if _WHISPER_INIT_ERROR and os.getenv('WHISPER_FORCE_RETRY', '').strip() != '1':
        raise WhisperUnavailableError(_WHISPER_INIT_ERROR)

    # Avoid multiple concurrent requests triggering large model downloads.
    if not _WHISPER_INIT_LOCK.acquire(blocking=False):
        raise WhisperInitializingError('Whisper 模型初始化/下载中，请稍后重试（首次下载可能较慢）')

    try:
        # Double-check after acquiring the lock.
        if _WHISPER_PIPELINE is not None:
            return _WHISPER_PIPELINE

        try:
            import torch  # type: ignore
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline  # type: ignore
        except Exception as exc:
            _WHISPER_INIT_ERROR = 'Whisper 依赖未安装：需要 torch + transformers'
            raise WhisperUnavailableError(_WHISPER_INIT_ERROR) from exc

        # Default to local model dir (Windows/offline); can be overridden via env WHISPER_MODEL.
        model_id = os.getenv('WHISPER_MODEL') or "openai/whisper-base"

        # If user points to a local path that doesn't exist, fail fast with a clear message.
        try:
            looks_like_path = (':' in model_id) or model_id.startswith('\\\\') or model_id.startswith('/')
            if looks_like_path and not Path(model_id).exists():
                _WHISPER_INIT_ERROR = f'Whisper 本地模型目录不存在：{model_id}'
                raise WhisperUnavailableError(_WHISPER_INIT_ERROR)
        except WhisperUnavailableError:
            raise
        except Exception:
            # If path probing fails, continue to let Transformers raise a more specific error.
            pass
        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32

        try:
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                model_id, torch_dtype=torch_dtype, low_cpu_mem_usage=True, use_safetensors=True
            )
            model.to(device)
            processor = AutoProcessor.from_pretrained(model_id)
        except Exception as exc:
            _WHISPER_INIT_ERROR = (
                'Whisper 模型下载/加载失败。常见原因：网络/代理/证书拦截导致无法访问 huggingface.co（日志里会出现 SSL EOF）。\n'
                '你可以尝试：\n'
                '1) 配置代理或允许访问 huggingface.co\n'
                '2) 设置 HF_ENDPOINT 为镜像（如国内镜像）\n'
                '3) 换小模型：设置环境变量 WHISPER_MODEL=openai/whisper-small 或 openai/whisper-base\n'
                '   或者指定本地目录：WHISPER_MODEL=D:\\models\\whisper-large-v3\n'
                '4) 若已下载但缓存损坏，删除 ~/.cache/huggingface/hub 下对应模型目录后重试\n'
                '（如需强制重试本次进程：设置 WHISPER_FORCE_RETRY=1 后重启服务）'
            )
            raise WhisperUnavailableError(_WHISPER_INIT_ERROR) from exc

        _WHISPER_PIPELINE = pipeline(
            'automatic-speech-recognition',
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            torch_dtype=torch_dtype,
            device=device,
        )
        _WHISPER_INIT_ERROR = None
        return _WHISPER_PIPELINE
    finally:
        try:
            _WHISPER_INIT_LOCK.release()
        except Exception:
            pass


def transcribe_audio_file(audio_path: str) -> str:
    pipe = get_whisper_pipeline()
    audio_path = str(audio_path)
    if not audio_path or not Path(audio_path).exists():
        raise RuntimeError(f'音频文件不存在：{audio_path}')
    try:
        result = pipe(audio_path, return_timestamps=True)
    except Exception as exc:
        ffmpeg_ok = bool(shutil.which('ffmpeg'))
        detail = str(exc or '').strip()
        if len(detail) > 400:
            detail = detail[:400] + '…'
        raise RuntimeError(
            '音频转写失败。若是 mp3/webm，请确认已安装 ffmpeg 且已加入 PATH。'
            + ('' if ffmpeg_ok else '（当前环境未检测到 ffmpeg）')
            + (f'\n详细：{detail}' if detail else '')
        ) from exc

    if isinstance(result, dict) and isinstance(result.get('text'), str):
        return result['text'].strip()
    return str(result).strip()


def _guess_audio_suffix(original_name: str, mimetype: str) -> str:
    """Infer a safe filename suffix for temp audio files.

    Some browsers upload files named "blob" without an extension; ffmpeg/decoders
    can fail if the temp file has no extension.
    """

    try:
        suffix = Path(secure_filename(original_name or '')).suffix.lower()
    except Exception:
        suffix = ''
    if suffix:
        return suffix

    mt = (mimetype or '').lower()
    if 'webm' in mt:
        return '.webm'
    if 'wav' in mt:
        return '.wav'
    if 'mpeg' in mt or 'mp3' in mt:
        return '.mp3'
    if 'ogg' in mt:
        return '.ogg'
    if 'mp4' in mt:
        return '.mp4'
    if 'm4a' in mt:
        return '.m4a'
    return '.webm'


def _note_session_dir(session_id: str) -> Path:
    base = Path(app.instance_path) / 'uploads' / 'note_sessions' / session_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save_note_session_chunk(session_id: str, original_name: str, mimetype: str, data: bytes) -> Path:
    import time

    suffix = _guess_audio_suffix(original_name, mimetype)
    ts = int(time.time() * 1000)
    out_path = _note_session_dir(session_id) / f'chunk_{ts}{suffix}'
    out_path.write_bytes(data)
    return out_path


def _ffmpeg_concat_to_wav(chunk_paths: list[Path], wav_out: Path) -> tuple[bool, str]:
    """Concat chunk files into a single WAV using ffmpeg concat demuxer."""

    import subprocess
    import tempfile

    if not chunk_paths:
        return False, '没有可用音频分片'

    # ffmpeg concat list file uses POSIX-like paths more reliably.
    try:
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt', encoding='utf-8') as f:
            list_path = Path(f.name)
            for p in chunk_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")
    except Exception as exc:
        return False, f'生成合并清单失败：{exc}'

    try:
        cmd = [
            'ffmpeg',
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-f',
            'concat',
            '-safe',
            '0',
            '-i',
            str(list_path),
            '-vn',
            '-ac',
            '1',
            '-ar',
            '16000',
            str(wav_out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or '').strip()[:600]
            return False, f'ffmpeg 合并失败：{stderr or "unknown"}'
        if not wav_out.exists() or wav_out.stat().st_size == 0:
            return False, 'ffmpeg 合并输出为空'
        return True, ''
    except FileNotFoundError:
        return False, '未找到 ffmpeg：请安装 ffmpeg 并加入 PATH（实时录音分片合并需要）'
    except Exception as exc:
        return False, f'ffmpeg 合并异常：{exc}'
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except Exception:
            pass


def _ffmpeg_to_wav(input_path: Path, wav_out: Path) -> tuple[bool, str]:
    """Decode a single audio file into 16kHz mono WAV via ffmpeg."""

    import subprocess

    try:
        _ensure_ffmpeg_on_path()
    except Exception:
        pass

    try:
        cmd = [
            'ffmpeg',
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            str(input_path),
            '-vn',
            '-ac',
            '1',
            '-ar',
            '16000',
            str(wav_out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or '').strip()[:600]
            return False, f'ffmpeg 转码失败：{stderr or "unknown"}'
        if not wav_out.exists() or wav_out.stat().st_size == 0:
            return False, 'ffmpeg 转码输出为空'
        return True, ''
    except FileNotFoundError:
        return False, '未找到 ffmpeg：请安装 ffmpeg 并加入 PATH（实时录音 webm 解码需要）'
    except Exception as exc:
        return False, f'ffmpeg 转码异常：{exc}'


def _ffmpeg_to_mp3(input_path: Path, mp3_out: Path) -> tuple[bool, str]:
    """Encode a single audio file into MP3 (16kHz mono) via ffmpeg."""

    import subprocess

    try:
        _ensure_ffmpeg_on_path()
    except Exception:
        pass

    try:
        cmd = [
            'ffmpeg',
            '-y',
            '-hide_banner',
            '-loglevel',
            'error',
            '-i',
            str(input_path),
            '-vn',
            '-ac',
            '1',
            '-ar',
            '16000',
            '-codec:a',
            'libmp3lame',
            '-b:a',
            '64k',
            str(mp3_out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or '').strip()[:600]
            return False, f'ffmpeg 转 mp3 失败：{stderr or "unknown"}'
        if not mp3_out.exists() or mp3_out.stat().st_size == 0:
            return False, 'ffmpeg 转 mp3 输出为空'
        return True, ''
    except FileNotFoundError:
        return False, '未找到 ffmpeg：请安装 ffmpeg 并加入 PATH（转 mp3 需要）'
    except Exception as exc:
        return False, f'ffmpeg 转 mp3 异常：{exc}'


def run_gemini_note_summary(transcript: str, focus_tag: str | None = None):
    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法进行智能摘要')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    client = get_text_client(api_key)
    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')

    allowed_subjects = '、'.join(SUBJECT_CHOICES)
    ft = (focus_tag or '').strip()
    focus_line = f'focus_tag：{ft}\n' if ft else ''

    prompt = (
        '只输出 JSON，禁止 markdown/解释文字/多余字符。\n'
        '你是课堂笔记助手。根据课堂转写文本，提炼结构化笔记与任务追踪。\n'
        'JSON schema（必须严格匹配）：\n'
        '{\n'
        '  "title": string,\n'
        '  "subject": string,\n'
        '  "summary_points": string[],\n'
        '  "tasks": [{"id": string, "text": string, "done": boolean}],\n'
        '  "key_terms": string[]\n'
        '}\n'
        '要求：\n'
        f'- subject 必须且只能从如下列表中选择其一：{allowed_subjects}。\n'
        '- title 简短（<= 18 字）。\n'
        '- summary_points 3-6 条，每条 <= 28 字；尽量包含关键公式/例题/典型句式等“可复用示例”。\n'
        '- tasks 2-6 条，都是可执行动作，text <= 30 字；done 默认 false；id 用短字符串（如 t1、t2）。\n'
        '- key_terms 3-8 个关键词，<= 8 字/个。\n'
        '- 如果转写内容信息不足：summary_points/tasks/key_terms 允许为空数组，但不要输出占位词。\n\n'
        f'{focus_line}'
        f'TRANSCRIPT:\n{transcript}'
    )

    response = client.models.generate_content(model=model, contents=prompt)
    text = getattr(response, 'text', None) or str(response)
    return text


def _validate_note_summary_dict(parsed: dict) -> tuple[dict | None, str | None]:
    title = str(parsed.get('title') or '').strip()
    subject = normalize_subject(parsed.get('subject'))
    summary_points = parsed.get('summary_points')
    tasks = parsed.get('tasks')
    key_terms = parsed.get('key_terms')

    if not isinstance(summary_points, list):
        summary_points = []
    summary_points_text = [str(x or '').strip() for x in summary_points if str(x or '').strip()]
    summary_points_text = summary_points_text[:8]

    if not isinstance(key_terms, list):
        key_terms = []
    key_terms_text = [str(x or '').strip() for x in key_terms if str(x or '').strip()]
    key_terms_text = key_terms_text[:12]

    clean_tasks: list[dict[str, Any]] = []
    if isinstance(tasks, list):
        for idx, item in enumerate(tasks[:12]):
            if not isinstance(item, dict):
                continue
            tid = str(item.get('id') or f't{idx+1}').strip() or f't{idx+1}'
            text_val = str(item.get('text') or '').strip()
            if not text_val:
                continue
            done_val = bool(item.get('done'))
            clean_tasks.append({'id': tid, 'text': text_val, 'done': done_val})

    payload = {
        'title': title or '课堂笔记',
        'subject': subject,
        'summary_points': summary_points_text,
        'tasks': clean_tasks,
        'key_terms': key_terms_text,
    }
    return payload, None


def _summarize_and_persist_note(entry: 'NoteAssistantEntry') -> tuple[dict | None, str | None]:
    if not (entry.transcript_text or '').strip():
        return None, '转写文本为空，无法摘要'

    try:
        raw = run_gemini_note_summary(entry.transcript_text or '', entry.focus_tag)
    except Exception as exc:
        return None, f'智能摘要失败：{exc}'

    extracted = _extract_first_json_object(raw)
    if not extracted:
        return None, '摘要返回格式非 JSON'

    parsed = _loads_lenient_object(extracted)
    if not parsed:
        return None, '摘要 JSON 解析失败'

    payload, err = _validate_note_summary_dict(parsed)
    if err or not payload:
        return None, err or '摘要格式错误'

    try:
        import json

        entry.title = payload.get('title') or entry.title
        entry.subject = normalize_subject(payload.get('subject'))
        entry.summary_json = json.dumps(
            {
                'title': payload.get('title'),
                'subject': payload.get('subject'),
                'summary_points': payload.get('summary_points', []),
                'key_terms': payload.get('key_terms', []),
            },
            ensure_ascii=False,
        )
        entry.tasks_json = json.dumps({'tasks': payload.get('tasks', [])}, ensure_ascii=False)
        entry.status = 'done'
        save_and_commit(entry)

        upsert_knowledge_from_note(entry)
    except Exception as exc:
        app.logger.warning('Failed to persist note summary: %s', exc)

    return payload, None


def run_gemini_parent_report(dashboard_payload: dict):
    import json

    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法生成家长周报')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    client = get_text_client(api_key)
    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')

    prompt = (
        '只输出 JSON，禁止 markdown/解释文字/多余字符。\n'
        '你是家长视图的学习周报助手。根据学习数据，生成简洁、友好、可执行的家长周报。\n'
        '需要综合：错题统计、课堂笔记（转写/总结）以及知识点掌握情况，体现学习进度与下一步行动。\n'
        'JSON schema（必须严格匹配）：\n'
        '{\n'
        '  "week": string,\n'
        '  "overallTone": string,\n'
        '  "aiSummary": string,\n'
        '  "encouragement": string,\n'
        '  "weakTopics": [{"subject": string, "issue": string, "suggestion": string}],\n'
        '  "highlightCards": [{"title": string, "detail": string}]\n'
        '}\n'
        '要求：\n'
        '- week 为近 7 天日期范围，格式如 "12.10 - 12.16"。\n'
        '- overallTone/aiSummary/encouragement 每段 1-2 句，避免夸张数字与空话。\n'
        '- weakTopics 2-4 条，建议必须可执行。\n'
        '- highlightCards 2-4 条，标题 <= 10 字，detail <= 24 字。\n'
        '输入数据（JSON）：\n'
        f'{json.dumps(dashboard_payload, ensure_ascii=False)}'
    )

    response = client.models.generate_content(model=model, contents=prompt)
    text = getattr(response, 'text', None) or str(response)
    return text


def _build_parent_report_fallback(dashboard_payload: dict) -> dict:
    # last 7 days range
    try:
        end = datetime.utcnow().date()
        start = end - timedelta(days=6)
        week = f"{start.strftime('%m.%d')} - {end.strftime('%m.%d')}"
    except Exception:
        week = '近 7 天'

    eb = dashboard_payload.get('error_book') if isinstance(dashboard_payload, dict) else None
    totals = eb.get('totals') if isinstance(eb, dict) else None
    total_entries = int((totals or {}).get('total_entries') or 0)
    done = int((totals or {}).get('done') or 0)
    with_quiz = int((totals or {}).get('with_quiz') or 0)
    top_subject = ''
    try:
        subjects = eb.get('subjects') or []
        if isinstance(subjects, list) and subjects:
            top_subject = str(subjects[0].get('subject') or '')
    except Exception:
        pass

    insights = dashboard_payload.get('insights') if isinstance(dashboard_payload, dict) else None
    insights_text = '；'.join([str(x) for x in (insights or []) if str(x).strip()][:3])
    if not insights_text:
        insights_text = f'近 7 天学习数据已汇总，错题总数 {total_entries} 条。'

    weak_concepts = []
    try:
        weak_concepts = eb.get('weak_concepts') or []
    except Exception:
        weak_concepts = []

    weak_topics = []
    if isinstance(weak_concepts, list):
        for c in weak_concepts[:3]:
            name = str(c or '').strip()
            if not name:
                continue
            subj = top_subject or '未分类'
            weak_topics.append(
                {
                    'subject': f'{subj} · {name}' if name else subj,
                    'issue': '高频薄弱点，需要反复巩固',
                    'suggestion': '用 3 道变式题 + 1 次口述复盘',
                }
            )

    if not weak_topics:
        weak_topics = [
            {
                'subject': top_subject or '综合',
                'issue': '需要保持稳定复习节奏',
                'suggestion': '每天 15 分钟错题回顾 + 当日小测',
            }
        ]

    highlight_cards = [
        {'title': '错题总量', 'detail': f'{total_entries} 条（已完成 {done}）'},
        {'title': '练习题', 'detail': f'已生成 {with_quiz} 份练习'},
    ]

    try:
        classroom = dashboard_payload.get('classroom_records') if isinstance(dashboard_payload, dict) else None
        note_items = classroom.get('items') if isinstance(classroom, dict) else None
        if isinstance(note_items, list) and note_items:
            highlight_cards.append({'title': '课堂笔记', 'detail': f'近 10 条有记录（共 {len(note_items)} 条）'})
    except Exception:
        pass

    try:
        knowledge = dashboard_payload.get('knowledge') if isinstance(dashboard_payload, dict) else None
        mastery = knowledge.get('mastery') if isinstance(knowledge, dict) else None
        if isinstance(mastery, list) and mastery:
            highlight_cards.append({'title': '知识点', 'detail': f'覆盖 {len(mastery)} 个节点'})
    except Exception:
        pass

    overall_tone = f'本周学习数据概览：{insights_text}'
    ai_summary = '建议围绕薄弱点做“短频快”复习：看一遍要点→做两题→讲给家长听。'
    encouragement = '只要每天稳定一点点，理解和自信都会积累起来。家长多关注过程，少盯结果。'

    return {
        'week': week,
        'overallTone': overall_tone,
        'aiSummary': ai_summary,
        'encouragement': encouragement,
        'weakTopics': weak_topics,
        'highlightCards': highlight_cards,
    }


_NOTE_SUMMARY_RUNNING: set[int] = set()
_NOTE_SUMMARY_LOCK = threading.Lock()


def _run_note_summary_job(entry_id: int) -> None:
    try:
        with app.app_context():
            entry = NoteAssistantEntry.query.filter_by(id=entry_id).first()
            if not entry:
                return

            payload, err = _summarize_and_persist_note(entry)
            if err or not payload:
                entry.status = 'summary_failed'
                save_and_commit(entry)
    except Exception as exc:
        try:
            with app.app_context():
                entry = NoteAssistantEntry.query.filter_by(id=entry_id).first()
                if entry:
                    entry.status = 'summary_failed'
                    save_and_commit(entry)
        except Exception:
            pass
        app.logger.warning('Note summary background job failed: %s', exc)
    finally:
        try:
            db.session.remove()
        except Exception:
            pass
        try:
            with _NOTE_SUMMARY_LOCK:
                _NOTE_SUMMARY_RUNNING.discard(entry_id)
        except Exception:
            pass


def start_note_summary_job(entry_id: int) -> bool:
    try:
        with _NOTE_SUMMARY_LOCK:
            if entry_id in _NOTE_SUMMARY_RUNNING:
                return False
            _NOTE_SUMMARY_RUNNING.add(entry_id)

        t = threading.Thread(target=_run_note_summary_job, args=(entry_id,), daemon=True)
        t.start()
        return True
    except Exception:
        try:
            with _NOTE_SUMMARY_LOCK:
                _NOTE_SUMMARY_RUNNING.discard(entry_id)
        except Exception:
            pass
        return False


def run_ocr(image_path: str):
    ocr = get_ocr_instance()
    result = ocr.predict(input=image_path)

    # PaddleX 风格结果对象最稳的方式：落盘 JSON 再读取
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        ocr_json_payload = None
        for res in result:
            try:
                res.save_to_json(str(tmp_dir))
            except Exception:
                # 兼容不同版本：可能支持 to_json()/json 属性
                if hasattr(res, 'json'):
                    ocr_json_payload = getattr(res, 'json')
                elif hasattr(res, 'to_json'):
                    ocr_json_payload = res.to_json()
            break

        if ocr_json_payload is None:
            json_files = list(tmp_dir.glob('*.json'))
            if json_files:
                import json

                with json_files[0].open('r', encoding='utf-8') as f:
                    ocr_json_payload = json.load(f)

    if not isinstance(ocr_json_payload, dict):
        ocr_json_payload = {'raw': ocr_json_payload}

    rec_texts = ocr_json_payload.get('rec_texts') or []
    if isinstance(rec_texts, list):
        ocr_text = '\n'.join(str(item) for item in rec_texts)
    else:
        ocr_text = str(rec_texts)

    return ocr_text, ocr_json_payload


def run_gemini_analysis(ocr_text: str):
    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法进行 AI 深度分析')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    client = get_text_client(api_key)
    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')

    allowed_subjects = '、'.join(SUBJECT_CHOICES)
    prompt = (
        '只输出 JSON，禁止 markdown/解释文字/多余字符。\n'
        '你是中学学习助教。下面是 OCR 提取的错题文本，请输出严格 JSON（不要 markdown），并尽量可解释、可核验。\n'
        'JSON schema（必须严格匹配）：\n'
        '{\n'
        '  "title": string,\n'
        '  "subject": string,\n'
        '  "verdict": string,\n'
        '  "mistakes": [{\n'
        '    "concept": string,\n'
        '    "reason": string,\n'
        '    "correct_approach": string,\n'
        '    "practice": string,\n'
        '    "evidence": string\n'
        '  }],\n'
        '  "key_points": string[],\n'
        '  "review_plan": string[],\n'
        '  "confidence": number\n'
        '}\n'
        '要求：\n'
        f'- subject 必须且只能从如下列表中选择其一：{allowed_subjects}。\n'
        '- title/subject 简短；verdict 一句话总结最主要错因；confidence 0~1。\n'
        '- mistakes 只在确实能提炼出错因时给出（最多 3 条）；如果无法判断，请输出空数组 []，不要输出占位词。\n'
        '- 每条 mistakes 中：concept <= 10 字；reason <= 30 字；correct_approach <= 40 字；practice <= 40 字；evidence <= 30 字。\n'
        '- key_points 建议 3-6 条，每条 <= 25 字。\n'
        '- review_plan 建议 3-6 条，每条 <= 28 字。\n'
        '- evidence 用 OCR 文本中的短片段引用（若无把握可留空字符串）。\n\n'
        f'OCR_TEXT:\n{ocr_text}'
    )

    response = client.models.generate_content(model=model, contents=prompt)
    text = getattr(response, 'text', None) or str(response)
    return text


def run_gemini_quiz(ocr_text: str):
    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法生成练习题')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    client = get_text_client(api_key)
    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')

    prompt = (
        '只输出 JSON，禁止 markdown/解释文字/多余字符。\n'
        '你是中学学习助教。根据 OCR 文本，生成一道“类似但更简单”的单选题（4 个选项），用于检验同一知识点。\n'
        '请输出严格 JSON（不要 markdown），schema：\n'
        '{"question": string, "options": [string,string,string,string], "answer_index": number, "explanation": string, "topic": string}.\n'
        '要求：\n'
        '- answer_index 必须是 0~3 的整数。\n'
        '- question 必须非空。\n'
        '- options 的 4 个字符串都必须非空，且相互区分（不要 4 个一样/近似）。\n'
        '- 题目要清晰、可独立作答；避免含糊引用“上题/图中”。\n'
        '- explanation 用 2-5 句解释即可。\n\n'
        f'OCR_TEXT:\n{ocr_text}'
    )

    response = client.models.generate_content(model=model, contents=prompt)
    text = getattr(response, 'text', None) or str(response)
    return text


# --- Routes: Public -----------------------------------------------------
@app.route('/ai/api/test', methods=['GET'])
def test_api():
    return jsonify({'message': 'Hello from Python Backend!'})


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    username = data.get('username', '').strip()
    password = data.get('password')
    role = data.get('role', 'student')
    display_name = data.get('display_name') or random_display_name()

    if not email or not password or not username:
        return jsonify({'message': '邮箱、账户名与密码必填'}), 400
    if not USERNAME_PATTERN.match(username):
        return jsonify({'message': '账户名需为 3-20 位字母、数字或 ._-'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'message': '该邮箱已注册'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'message': '该账户名已被占用'}), 400

    user = User(
        email=email,
        username=username,
        password_hash=generate_password_hash(password),
        role='parent' if role == 'parent' else 'student',
        display_name=display_name,
        verification_code=generate_code(),
        verification_expires=datetime.utcnow() + timedelta(minutes=10),
    )
    save_and_commit(user)

    send_email(
        'AI Study Assistant 验证码',
        [user.email],
        f"欢迎加入！您的验证码是 {user.verification_code} ，10 分钟内有效。",
    )

    return jsonify({'message': '注册成功，验证码已发送至邮箱'}), 201


@app.route('/api/auth/verify', methods=['POST'])
def verify_email():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    code = data.get('code', '')

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'message': '用户不存在'}), 404
    if not user.verification_code or user.verification_code != code:
        return jsonify({'message': '验证码不正确'}), 400
    if user.verification_expires and user.verification_expires < datetime.utcnow():
        return jsonify({'message': '验证码已过期'}), 400

    user.verified = True
    user.verification_code = None
    user.verification_expires = None
    user.auth_token = generate_token()
    save_and_commit(user)

    return jsonify({'message': '邮箱验证成功', 'token': user.auth_token})


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    password = data.get('password', '')

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': '邮箱或密码错误'}), 401
    if not user.verified:
        return jsonify({'message': '请先完成邮箱验证'}), 403

    user.auth_token = generate_token()
    save_and_commit(user)

    return jsonify({'message': '登录成功', 'token': user.auth_token})


@app.route('/api/auth/request-password-reset', methods=['POST'])
def request_password_reset():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'message': '如果邮箱存在，我们已发送验证码'}), 200

    user.verification_code = generate_code()
    user.verification_expires = datetime.utcnow() + timedelta(minutes=10)
    save_and_commit(user)

    send_email(
        'AI Study Assistant 密码重置',
        [user.email],
        f"您正在重置密码，验证码：{user.verification_code} ，10 分钟内有效。",
    )

    return jsonify({'message': '验证码已发送'}), 200


@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json() or {}
    email = data.get('email', '').lower().strip()
    code = data.get('code', '')
    new_password = data.get('new_password', '')

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'message': '用户不存在'}), 404
    if not user.verification_code or user.verification_code != code:
        return jsonify({'message': '验证码不正确'}), 400
    if user.verification_expires and user.verification_expires < datetime.utcnow():
        return jsonify({'message': '验证码已过期'}), 400

    user.password_hash = generate_password_hash(new_password)
    user.verification_code = None
    user.verification_expires = None
    save_and_commit(user)

    return jsonify({'message': '密码已重置'})


# --- Routes: Error Book -------------------------------------------------
@app.route('/api/error-book/entries', methods=['GET', 'POST'])
def error_book_entries():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_error_book_access_user_ids(user)

    if request.method == 'GET':
        entries = (
            ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids))
            .order_by(ErrorBookEntry.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify([entry.to_summary() for entry in entries])

    if 'image' not in request.files:
        return jsonify({'message': '请上传图片字段 image'}), 400
    image = request.files['image']
    if not image or not image.filename:
        return jsonify({'message': '图片不能为空'}), 400

    safe_name = secure_filename(image.filename)
    raw_bytes = image.read()
    if not raw_bytes:
        return jsonify({'message': '图片为空或读取失败'}), 400

    file_sha = sha256(raw_bytes).hexdigest()

    raw_form_subject = request.form.get('subject')
    entry = ErrorBookEntry(
        user_id=user.id,
        title=request.form.get('title') or None,
        subject=normalize_subject(raw_form_subject) if raw_form_subject else None,
        status='uploaded',
        image_original_name=safe_name or None,
        image_mimetype=image.mimetype,
        image_size=len(raw_bytes),
        image_sha256=file_sha,
        image_blob=raw_bytes,
    )
    save_and_commit(entry)

    # OCR
    try:
        # PaddleOCR 需要文件路径：写入临时文件
        ext = Path(safe_name).suffix.lower() or '.png'
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        try:
            with os.fdopen(fd, 'wb') as tmp_file:
                tmp_file.write(raw_bytes)
            ocr_text, ocr_json = run_ocr(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        import json

        entry.ocr_text = ocr_text
        entry.ocr_json = json.dumps(ocr_json, ensure_ascii=False)
        entry.status = 'ocr_done'
        save_and_commit(entry)
    except Exception as exc:
        entry.status = 'ocr_failed'
        entry.verdict = f'OCR 失败：{exc}'
        save_and_commit(entry)
        return jsonify(entry.to_detail()), 200

    # AI analysis (optional)
    try:
        analysis_text = run_gemini_analysis(entry.ocr_text or '')
        entry.ai_analysis = analysis_text
        entry.status = 'done'

        # Try to extract title/subject/verdict from returned JSON
        import json

        try:
            extracted = _extract_first_json_object(analysis_text) or analysis_text
            parsed = json.loads(extracted)
            if isinstance(parsed, dict):
                entry.title = entry.title or parsed.get('title')
                if not entry.subject or normalize_subject(entry.subject) == '未分类':
                    entry.subject = normalize_subject(parsed.get('subject'))
                else:
                    entry.subject = normalize_subject(entry.subject)
                entry.verdict = entry.verdict or parsed.get('verdict')
        except Exception:
            # non-JSON response is acceptable; keep as-is
            if not entry.verdict:
                entry.verdict = 'AI 已生成解析（非结构化输出）'

        save_and_commit(entry)
        upsert_knowledge_from_error(entry)
    except Exception as exc:
        entry.status = 'ai_failed'
        entry.verdict = entry.verdict or f'AI 分析失败：{exc}'
        save_and_commit(entry)

    # Quiz (best-effort, persisted)
    if not (entry.quiz_json or '').strip():
        try:
            _generate_and_persist_quiz(entry)
        except Exception:
            pass

    return jsonify(entry.to_detail()), 201


@app.route('/api/error-book/entries/<int:entry_id>', methods=['GET', 'DELETE'])
def error_book_entry_detail(entry_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_error_book_access_user_ids(user)

    entry = ErrorBookEntry.query.filter(ErrorBookEntry.id == entry_id).filter(
        ErrorBookEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到错题记录'}), 404

    if request.method == 'DELETE':
        db.session.delete(entry)
        db.session.commit()
        return jsonify({'message': '已删除', 'id': entry_id})

    # Ensure quiz is available without requiring client to call /quiz.
    quiz_error = None
    quiz_payload = None
    if (entry.quiz_json or '').strip():
        quiz_payload = entry.to_detail().get('quiz')
    else:
        quiz_payload, quiz_error = _generate_and_persist_quiz(entry)

    detail = entry.to_detail()
    if quiz_payload:
        detail['quiz'] = quiz_payload
    if quiz_error:
        detail['quiz_error'] = quiz_error

    return jsonify(detail)


@app.route('/api/error-book/entries/<int:entry_id>/image', methods=['GET'])
def error_book_entry_image(entry_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_error_book_access_user_ids(user)
    entry = ErrorBookEntry.query.filter(ErrorBookEntry.id == entry_id).filter(
        ErrorBookEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry or not entry.image_blob:
        return jsonify({'message': '未找到图片'}), 404

    mimetype = entry.image_mimetype or 'image/png'
    download_name = entry.image_original_name or f"error-book-{entry.id}.png"
    return send_file(io.BytesIO(entry.image_blob), mimetype=mimetype, download_name=download_name)


@app.route('/api/error-book/entries/<int:entry_id>/quiz', methods=['GET'])
def error_book_entry_quiz(entry_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_error_book_access_user_ids(user)
    entry = ErrorBookEntry.query.filter(ErrorBookEntry.id == entry_id).filter(
        ErrorBookEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到错题记录'}), 404
    if not (entry.ocr_text or '').strip():
        return jsonify({'message': 'OCR 文本为空，无法生成练习题'}), 400

    # Backward compatible endpoint
    if (entry.quiz_json or '').strip():
        detail = entry.to_detail()
        if detail.get('quiz'):
            return jsonify(detail['quiz'])

    payload, err = _generate_and_persist_quiz(entry)
    if err or not payload:
        return jsonify({'message': err or '练习题生成失败'}), 502
    return jsonify(payload)


# --- Routes: Dashboard --------------------------------------------------
@app.route('/api/dashboard/summary', methods=['GET'])
def dashboard_summary():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    payload = _compute_dashboard_summary_payload(user)
    return jsonify(payload)


def _compute_dashboard_summary_payload(user: 'User') -> dict:
    access_user_ids = get_error_book_access_user_ids(user)

    # Recent error-book entries (for lists + analysis mining)
    recent = (
        ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids))
        .order_by(ErrorBookEntry.created_at.desc())
        .limit(80)
        .all()
    )

    total_entries = ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids)).count()
    done_count = ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids)).filter(
        ErrorBookEntry.status == 'done'
    ).count()
    ocr_failed = ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids)).filter(
        ErrorBookEntry.status == 'ocr_failed'
    ).count()
    ai_failed = ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids)).filter(
        ErrorBookEntry.status == 'ai_failed'
    ).count()
    with_quiz = (
        ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids))
        .filter(ErrorBookEntry.quiz_json.isnot(None))
        .count()
    )

    # Subject distribution (from recent window for speed)
    subject_counts: dict[str, int] = {}
    for entry in recent:
        subject = normalize_subject(entry.subject)
        subject_counts[subject] = subject_counts.get(subject, 0) + 1
    subjects_sorted = sorted(subject_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    # Daily counts (last 7 days) based on created_at
    today = datetime.utcnow().date()
    last_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    daily_map = {d.isoformat(): 0 for d in last_days}
    for entry in recent:
        try:
            d = (entry.created_at or datetime.utcnow()).date().isoformat()
        except Exception:
            continue
        if d in daily_map:
            daily_map[d] += 1
    daily_counts = [{'date': d, 'count': daily_map[d]} for d in daily_map]

    # Mine structured AI analysis JSON (best-effort)
    import json

    key_point_counts: dict[str, int] = {}
    review_plan_counts: dict[str, int] = {}
    concept_counts: dict[str, int] = {}

    for entry in recent:
        raw = (entry.ai_analysis or '').strip()
        if not raw:
            continue
        extracted = _extract_first_json_object(raw) or ''
        if not extracted:
            continue
        try:
            parsed = json.loads(extracted)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue

        key_points = parsed.get('key_points')
        if isinstance(key_points, list):
            for item in key_points:
                s = str(item or '').strip()
                if s:
                    key_point_counts[s] = key_point_counts.get(s, 0) + 1

        review_plan = parsed.get('review_plan')
        if isinstance(review_plan, list):
            for item in review_plan:
                s = str(item or '').strip()
                if s:
                    review_plan_counts[s] = review_plan_counts.get(s, 0) + 1

        mistakes = parsed.get('mistakes')
        if isinstance(mistakes, list):
            for m in mistakes:
                if not isinstance(m, dict):
                    continue
                concept = str(m.get('concept') or '').strip()
                if concept:
                    concept_counts[concept] = concept_counts.get(concept, 0) + 1

    top_key_points = [k for k, _ in sorted(key_point_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]]
    top_review_plan = [k for k, _ in sorted(review_plan_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]]
    weak_concepts = [k for k, _ in sorted(concept_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]]

    # Insights (deterministic, no LLM required)
    top_subject = subjects_sorted[0][0] if subjects_sorted else '未分类'
    seven_day_total = sum(item['count'] for item in daily_counts)
    insights = [
        f'近 7 天新增错题：{seven_day_total} 条',
        f'错题总数：{total_entries} 条（完成：{done_count} / OCR失败：{ocr_failed} / AI失败：{ai_failed}）',
        f'高频科目：{top_subject}',
    ]
    if weak_concepts:
        insights.append('高频薄弱知识点：' + '、'.join(weak_concepts[:3]))
    if top_review_plan:
        insights.append('建议优先复习：' + '；'.join(top_review_plan[:2]))

    # Classroom records (note assistant entries)
    note_access_user_ids = get_note_access_user_ids(user)
    note_recent = (
        NoteAssistantEntry.query.filter(NoteAssistantEntry.user_id.in_(note_access_user_ids))
        .order_by(NoteAssistantEntry.created_at.desc())
        .limit(10)
        .all()
    )

    # Knowledge mastery (node_id-based) - lightweight stats
    knowledge_nodes = (
        KnowledgeNode.query.filter(KnowledgeNode.user_id.in_(access_user_ids))
        .order_by(KnowledgeNode.last_seen_at.desc())
        .limit(120)
        .all()
    )

    # Count mistake hits for recent error-book entries only (speed)
    recent_for_mastery = (
        ErrorBookEntry.query.filter(ErrorBookEntry.user_id.in_(access_user_ids))
        .order_by(ErrorBookEntry.created_at.desc())
        .limit(120)
        .all()
    )
    mistake_counts_by_name: dict[tuple[int, str, str], int] = {}
    for e in recent_for_mastery:
        subj, concepts = _extract_error_concepts(e)
        for c in concepts:
            key = (e.user_id, normalize_subject(subj), _normalize_concept_name(c))
            mistake_counts_by_name[key] = mistake_counts_by_name.get(key, 0) + 1

    # Count note hits (progress proxy) from recent note entries
    recent_notes_for_mastery = (
        NoteAssistantEntry.query.filter(NoteAssistantEntry.user_id.in_(note_access_user_ids))
        .order_by(NoteAssistantEntry.created_at.desc())
        .limit(160)
        .all()
    )
    note_counts_by_name: dict[tuple[int, str, str], int] = {}
    for n in recent_notes_for_mastery:
        subj, concepts = _extract_note_concepts(n)
        for c in concepts:
            key = (n.user_id, normalize_subject(subj), _normalize_concept_name(c))
            note_counts_by_name[key] = note_counts_by_name.get(key, 0) + 1

    knowledge_mastery = []
    for node in knowledge_nodes:
        key = (node.user_id, normalize_subject(node.subject), _normalize_concept_name(node.name))
        mistake_count = mistake_counts_by_name.get(key, 0)
        note_count = note_counts_by_name.get(key, 0)
        knowledge_mastery.append(
            {
                'node_id': node.id,
                'subject': node.subject or '',
                'name': node.name or '',
                'mistake_count': mistake_count,
                'note_count': note_count,
            }
        )
    knowledge_mastery.sort(
        key=lambda x: (-int(x.get('mistake_count') or 0), -int(x.get('note_count') or 0), str(x.get('name') or ''))
    )

    payload = {
        'generated_at': isoformat_utc_z(datetime.now(timezone.utc)),
        'classroom_records': {
            'status': 'ready' if note_recent else 'empty',
            'message': '来自笔记助手的课堂转写与摘要。' if note_recent else '暂无课堂记录，请先在笔记助手完成一次转写。',
            'items': [e.to_summary() for e in note_recent],
        },
        'error_book': {
            'totals': {
                'total_entries': total_entries,
                'done': done_count,
                'ocr_failed': ocr_failed,
                'ai_failed': ai_failed,
                'with_quiz': with_quiz,
            },
            'subjects': [{'subject': s, 'count': c} for s, c in subjects_sorted],
            'daily_counts': daily_counts,
            'recent_entries': [e.to_summary() for e in recent[:8]],
            'top_key_points': top_key_points,
            'top_review_plan': top_review_plan,
            'weak_concepts': weak_concepts,
        },
        'knowledge': {
            'mastery': knowledge_mastery[:40],
        },
        'insights': insights,
    }
    return payload


@app.route('/api/parent/report', methods=['GET'])
def parent_report():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    if (user.role or '') != 'parent':
        return jsonify({'message': '仅家长可用'}), 403

    dashboard_payload = _compute_dashboard_summary_payload(user)

    report = None
    try:
        raw = run_gemini_parent_report(dashboard_payload)
        extracted = _extract_first_json_object(raw) or ''
        parsed = _loads_lenient_object(extracted) if extracted else None
        if isinstance(parsed, dict):
            report = {
                'week': str(parsed.get('week') or '').strip() or _build_parent_report_fallback(dashboard_payload)['week'],
                'overallTone': str(parsed.get('overallTone') or '').strip(),
                'aiSummary': str(parsed.get('aiSummary') or '').strip(),
                'encouragement': str(parsed.get('encouragement') or '').strip(),
                'weakTopics': parsed.get('weakTopics') if isinstance(parsed.get('weakTopics'), list) else [],
                'highlightCards': parsed.get('highlightCards') if isinstance(parsed.get('highlightCards'), list) else [],
            }
    except Exception as exc:
        app.logger.warning('Parent report generation failed, using fallback: %s', exc)

    if not report or not (report.get('overallTone') or '').strip():
        report = _build_parent_report_fallback(dashboard_payload)

    # Hard cap + sanitize lists
    weak_topics = []
    for item in (report.get('weakTopics') or [])[:6]:
        if not isinstance(item, dict):
            continue
        weak_topics.append(
            {
                'subject': str(item.get('subject') or '').strip()[:40],
                'issue': str(item.get('issue') or '').strip()[:60],
                'suggestion': str(item.get('suggestion') or '').strip()[:60],
            }
        )
    highlight_cards = []
    for item in (report.get('highlightCards') or [])[:6]:
        if not isinstance(item, dict):
            continue
        highlight_cards.append(
            {
                'title': str(item.get('title') or '').strip()[:16],
                'detail': str(item.get('detail') or '').strip()[:80],
            }
        )

    return jsonify(
        {
            'week': str(report.get('week') or '').strip() or _build_parent_report_fallback(dashboard_payload)['week'],
            'overallTone': str(report.get('overallTone') or '').strip(),
            'aiSummary': str(report.get('aiSummary') or '').strip(),
            'encouragement': str(report.get('encouragement') or '').strip(),
            'weakTopics': weak_topics,
            'highlightCards': highlight_cards,
        }
    )


def _build_cooccurrence_related(owner_user_id: int) -> dict[int, dict[int, int]]:
    """Build node co-occurrence graph from recent history for the owner user."""

    related: dict[int, dict[int, int]] = {}

    notes = (
        NoteAssistantEntry.query.filter_by(user_id=owner_user_id)
        .order_by(NoteAssistantEntry.created_at.desc())
        .limit(120)
        .all()
    )
    errors = (
        ErrorBookEntry.query.filter_by(user_id=owner_user_id)
        .order_by(ErrorBookEntry.created_at.desc())
        .limit(120)
        .all()
    )

    def add_pairs(node_ids: list[int]):
        uniq = list(dict.fromkeys([int(x) for x in node_ids if x]))
        if len(uniq) < 2:
            return
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                related.setdefault(a, {})[b] = related.setdefault(a, {}).get(b, 0) + 1
                related.setdefault(b, {})[a] = related.setdefault(b, {}).get(a, 0) + 1

    for n in notes:
        subj, concepts = _extract_note_concepts(n)
        ids: list[int] = []
        for c in concepts:
            node = get_or_create_knowledge_node(owner_user_id, subj, c, kind='concept')
            if node:
                ids.append(node.id)
        add_pairs(ids)

    for e in errors:
        subj, concepts = _extract_error_concepts(e)
        ids = []
        for c in concepts:
            node = get_or_create_knowledge_node(owner_user_id, subj, c, kind='concept')
            if node:
                ids.append(node.id)
        add_pairs(ids)

    return related


def _validate_mind_tree_dict(obj: Any) -> tuple[dict | None, str | None]:
    if not isinstance(obj, dict):
        return None, 'mind tree 结果不是对象'
    tree = obj.get('tree')
    if not isinstance(tree, dict):
        return None, 'mind tree 缺少 tree'
    if not str(tree.get('name') or '').strip():
        return None, 'mind tree 根节点缺少 name'
    return obj, None


def run_gemini_mindmap_tree(subject: str, title: str, source_text: str, seed_concepts: list[str]):
    import json
    import time

    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法生成知识树')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    subject_norm = normalize_subject(subject)
    seed = [c for c in [str(x or '').strip() for x in (seed_concepts or [])] if c]
    seed = list(dict.fromkeys(seed))[:18]

    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')

    # Keep prompt bounded to reduce cost.
    text = (source_text or '').strip()
    if len(text) > 4500:
        text = text[:4500]

    prompt = f"""
你是学习助手。请基于输入内容生成“更完善”的知识树：

要求：
1) 必须包含上级知识（更宏观的章节/主题）与下级细分（更具体的子概念/方法/易错点）。
1.1) 允许适度补充“输入中未直接出现但强相关”的知识点（例如前置概念/常见方法/典型易错点），不要只局限于种子概念。
2) 输出必须是严格 JSON（不要 markdown、不要代码块、不要多余解释）。
3) 节点数量控制在 12-30 个；层级深度 2-4；尽量形成树（一个子节点只有一个父节点）。
4) 节点 name 用中文短语，尽量 <= 12 字。
5) kind 只能是：chapter / concept / method / mistake

返回 JSON schema：
{{
  "tree": {{
    "name": "{title}",
    "kind": "chapter",
    "children": [
      {{"name": "...", "kind": "chapter|concept|method|mistake", "children": [ ... ]}}
    ]
  }},
  "subject": "{subject_norm}",
  "seed_concepts": {json.dumps(seed, ensure_ascii=False)}
}}

输入：
科目：{subject_norm}
标题：{title}
关键概念（种子，仅供参考，不是限制）：{', '.join(seed) if seed else '无'}
内容（截断）：
""" + text

    raw = ''
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            client = get_text_client(api_key)
            response = client.models.generate_content(model=model, contents=prompt)
            raw = (getattr(response, 'text', None) or '').strip()
            if raw:
                break
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            transient = any(
                s in msg
                for s in (
                    'server disconnected',
                    'timed out',
                    'timeout',
                    'connection reset',
                    'temporarily unavailable',
                    'ssl',
                    'tls',
                )
            )
            if attempt < 2 and transient:
                time.sleep(0.6 + attempt * 0.9)
                continue
            raise
    if not raw and last_exc:
        raise last_exc
    extracted = _extract_first_json_object(raw) or raw
    parsed = _loads_lenient_object(extracted)
    ok, err = _validate_mind_tree_dict(parsed)
    if err:
        raise RuntimeError(err)
    return ok


def _flatten_mind_tree(tree: dict) -> list[tuple[str, str, str]]:
    """Return list of (parent_name, child_name, child_kind)."""

    edges: list[tuple[str, str, str]] = []

    def walk(node: dict):
        parent_name = str(node.get('name') or '').strip()
        children = node.get('children')
        if not isinstance(children, list):
            return
        for child in children:
            if not isinstance(child, dict):
                continue
            child_name = _normalize_concept_name(child.get('name'))
            child_kind = str(child.get('kind') or 'concept').strip() or 'concept'
            if child_name and parent_name and child_name != parent_name:
                edges.append((parent_name, child_name, child_kind))
            walk(child)

    walk(tree)
    return edges


def _build_history_index(owner_user_id: int) -> tuple[dict[str, list[NoteAssistantEntry]], dict[str, list[ErrorBookEntry]]]:
    notes = (
        NoteAssistantEntry.query.filter_by(user_id=owner_user_id)
        .order_by(NoteAssistantEntry.created_at.desc())
        .limit(220)
        .all()
    )
    errors = (
        ErrorBookEntry.query.filter_by(user_id=owner_user_id)
        .order_by(ErrorBookEntry.created_at.desc())
        .limit(220)
        .all()
    )

    concept_to_notes: dict[str, list[NoteAssistantEntry]] = {}
    for n in notes:
        _, concepts = _extract_note_concepts(n)
        for c in concepts:
            key = _normalize_concept_name(c)
            if not key:
                continue
            concept_to_notes.setdefault(key, []).append(n)

    concept_to_errors: dict[str, list[ErrorBookEntry]] = {}
    for e in errors:
        _, concepts = _extract_error_concepts(e)
        for c in concepts:
            key = _normalize_concept_name(c)
            if not key:
                continue
            concept_to_errors.setdefault(key, []).append(e)

    return concept_to_notes, concept_to_errors


def run_gemini_mindmap_compare(subject: str, title: str, items: list[dict[str, Any]]):
    import json
    import time

    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        raise RuntimeError('未配置 GEMINI_API_KEY，无法生成对比分析')

    try:
        from google import genai  # type: ignore
    except Exception as exc:
        raise RuntimeError('google-genai 未安装，请先安装 google-genai') from exc

    model = app.config.get('GEMINI_MODEL', 'gemini-2.5-flash')
    subject_norm = normalize_subject(subject)

    prompt = f"""
你是学习分析助手。给你一些知识点以及它们关联的“笔记要点/错题错因”，请输出对比分析。

输出必须是严格 JSON（不要 markdown、不要代码块）：
{{
  "comparisons": [
    {{
      "name": "知识点名称",
      "summary": "一句话总结差距/误区",
      "gaps": ["差距1", "差距2"],
      "actions": ["可执行建议1", "可执行建议2"]
    }}
  ]
}}

约束：
- 每个知识点 gaps/actions 各 1-3 条，中文短句。
- 不要虚构具体题号；引用内容用概括表达。

科目：{subject_norm}
标题：{title}
输入 items JSON：
{json.dumps(items, ensure_ascii=False)}
"""

    raw = ''
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            client = get_text_client(api_key)
            response = client.models.generate_content(model=model, contents=prompt)
            raw = (getattr(response, 'text', None) or '').strip()
            if raw:
                break
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            transient = any(
                s in msg
                for s in (
                    'server disconnected',
                    'timed out',
                    'timeout',
                    'connection reset',
                    'temporarily unavailable',
                    'ssl',
                    'tls',
                )
            )
            if attempt < 2 and transient:
                time.sleep(0.6 + attempt * 0.9)
                continue
            raise
    if not raw and last_exc:
        raise last_exc
    extracted = _extract_first_json_object(raw) or raw
    parsed = _loads_lenient_object(extracted)
    if not isinstance(parsed, dict) or not isinstance(parsed.get('comparisons'), list):
        raise RuntimeError('对比分析返回格式不正确')
    return parsed


@app.route('/api/mind-map/generate', methods=['POST'])
def mind_map_generate():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    data = request.get_json() or {}
    source_type = str(data.get('source_type') or '').strip()
    source_id = data.get('source_id')
    if source_type not in ('note', 'error_book'):
        return jsonify({'message': 'source_type 必须为 note 或 error_book'}), 400
    try:
        source_id_int = int(source_id)
    except Exception:
        return jsonify({'message': 'source_id 无效'}), 400

    access_user_ids = get_error_book_access_user_ids(user)

    owner_user_id: int
    subject: str
    title: str
    concept_names: list[str]

    if source_type == 'note':
        entry = NoteAssistantEntry.query.filter(NoteAssistantEntry.id == source_id_int).filter(
            NoteAssistantEntry.user_id.in_(access_user_ids)
        ).first()
        if not entry:
            return jsonify({'message': '未找到笔记记录'}), 404
        owner_user_id = int(entry.user_id)
        subject, concept_names = _extract_note_concepts(entry)
        title = (entry.title or '').strip() or '课堂笔记'
    else:
        entry = ErrorBookEntry.query.filter(ErrorBookEntry.id == source_id_int).filter(
            ErrorBookEntry.user_id.in_(access_user_ids)
        ).first()
        if not entry:
            return jsonify({'message': '未找到错题记录'}), 404
        owner_user_id = int(entry.user_id)
        subject, concept_names = _extract_error_concepts(entry)
        title = (entry.title or '').strip() or '错题知识树'

    subject = normalize_subject(subject)

    # Build a bounded source text for AI
    source_text = ''
    if source_type == 'note':
        try:
            source_text = (getattr(entry, 'transcript_text', None) or '').strip()
            if not source_text:
                source_text = (getattr(entry, 'summary_json', None) or '').strip()
        except Exception:
            source_text = ''
    else:
        try:
            source_text = (getattr(entry, 'ocr_text', None) or '').strip()
            if not source_text:
                source_text = (getattr(entry, 'ai_analysis', None) or '').strip()
        except Exception:
            source_text = ''

    root_node = get_or_create_knowledge_node(owner_user_id, subject, title, kind='chapter')
    if not root_node:
        return jsonify({'message': '无法生成根节点'}), 500

    nodes: dict[int, KnowledgeNode] = {root_node.id: root_node}
    edges: list[dict[str, int]] = []

    mode = str(data.get('mode') or 'ai').strip()  # ai|simple

    # Prefer AI hierarchical expansion; fallback to deterministic.
    ai_tree = None
    if mode == 'ai':
        try:
            ai_tree = run_gemini_mindmap_tree(subject, title, source_text, concept_names)
        except Exception as exc:
            app.logger.warning('Mind map AI tree failed, fallback to simple: %s', exc)
            ai_tree = None

    if ai_tree and isinstance(ai_tree, dict) and isinstance(ai_tree.get('tree'), dict):
        root_name = _normalize_concept_name(ai_tree['tree'].get('name')) or title
        # Ensure root uses existing root_node but keep name stable
        if root_name and root_name != (root_node.name or ''):
            try:
                root_node.name = _normalize_concept_name(root_name) or root_node.name
                save_and_commit(root_node)
            except Exception:
                pass

        pairs = _flatten_mind_tree(ai_tree['tree'])
        # Create nodes and edges. Parent/child resolved by name, with root as anchor.
        name_to_id: dict[str, int] = {_normalize_concept_name(root_node.name): root_node.id}

        def get_id_for_name(nm: str, kind: str) -> int | None:
            key = _normalize_concept_name(nm)
            if not key:
                return None
            if key in name_to_id:
                return name_to_id[key]
            node = get_or_create_knowledge_node(owner_user_id, subject, key, kind=kind if kind in ('chapter', 'concept', 'method', 'mistake') else 'concept')
            if not node:
                return None
            nodes[node.id] = node
            name_to_id[key] = node.id
            return node.id

        # Bound size
        for parent_name, child_name, child_kind in pairs[:80]:
            if len(nodes) >= 34:
                break
            p_id = get_id_for_name(parent_name, kind='chapter')
            c_id = get_id_for_name(child_name, kind=child_kind)
            if not p_id or not c_id or p_id == c_id:
                continue
            edges.append({'from': p_id, 'to': c_id})

        # Ensure seeds appear under root if missing
        for name in concept_names:
            if len(nodes) >= 34:
                break
            node = get_or_create_knowledge_node(owner_user_id, subject, name, kind='concept')
            if not node:
                continue
            nodes[node.id] = node
            if not any(e.get('to') == node.id for e in edges):
                edges.append({'from': root_node.id, 'to': node.id})
    else:
        # Flat tree (root -> concepts). Deterministic fallback.
        for name in concept_names:
            node = get_or_create_knowledge_node(owner_user_id, subject, name, kind='concept')
            if not node:
                continue
            nodes[node.id] = node
            edges.append({'from': root_node.id, 'to': node.id})

    # Highlight from error-book mistake tags across owner's history
    highlight_counts: dict[int, int] = {}
    owner_errors = (
        ErrorBookEntry.query.filter_by(user_id=owner_user_id)
        .order_by(ErrorBookEntry.created_at.desc())
        .limit(160)
        .all()
    )
    concept_counter: dict[str, int] = {}
    for e in owner_errors:
        _, cs = _extract_error_concepts(e)
        for c in cs:
            c2 = _normalize_concept_name(c)
            if c2:
                concept_counter[c2] = concept_counter.get(c2, 0) + 1
    for node_id, node in nodes.items():
        c = concept_counter.get(_normalize_concept_name(node.name), 0)
        if c > 0:
            highlight_counts[node_id] = c

    # Related links by co-occurrence
    co = _build_cooccurrence_related(owner_user_id)
    related_payload: dict[str, list[dict[str, Any]]] = {}
    for node_id in nodes.keys():
        rel = co.get(int(node_id), {})
        top = sorted(rel.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        items = []
        for rid, cnt in top:
            other = KnowledgeNode.query.filter_by(id=rid).first()
            if other:
                items.append({'node_id': other.id, 'name': other.name, 'count': cnt})
        related_payload[str(node_id)] = items

    # Evidence from history: related notes + errors for each concept node
    concept_to_notes, concept_to_errors = _build_history_index(owner_user_id)
    evidence: dict[str, dict[str, Any]] = {}
    for node_id, node in nodes.items():
        nm = _normalize_concept_name(node.name)
        if not nm or node.kind == 'chapter':
            continue
        notes_hit = concept_to_notes.get(nm, [])[:5]
        errors_hit = concept_to_errors.get(nm, [])[:5]
        if not notes_hit and not errors_hit:
            continue
        evidence[str(node_id)] = {
            'notes': [
                {
                    'id': n.id,
                    'title': (n.title or '').strip() or '课堂笔记',
                    'created_at': isoformat_utc_z(n.created_at),
                }
                for n in notes_hit
            ],
            'errors': [
                {
                    'id': e.id,
                    'title': (e.title or '').strip() or '错题',
                    'created_at': isoformat_utc_z(e.created_at),
                    'verdict': (e.verdict or '').strip(),
                }
                for e in errors_hit
            ],
        }

    # Comparative analysis (LLM best-effort) for top highlighted nodes
    analysis: dict[str, Any] = {}
    try:
        ranked = sorted(
            [
                {
                    'node_id': nid,
                    'name': nodes.get(nid).name if nodes.get(nid) else '',
                    'highlight': int(highlight_counts.get(nid, 0)),
                }
                for nid in nodes.keys()
                if str(nid) in evidence
            ],
            key=lambda x: (-int(x.get('highlight') or 0), str(x.get('name') or '')),
        )
        targets = ranked[:8]
        items = []
        for t in targets:
            nid = t['node_id']
            ev = evidence.get(str(nid), {})
            items.append(
                {
                    'name': t.get('name') or '',
                    'highlight': t.get('highlight') or 0,
                    'notes_titles': [x.get('title') for x in (ev.get('notes') or []) if isinstance(x, dict)],
                    'errors_titles': [x.get('title') for x in (ev.get('errors') or []) if isinstance(x, dict)],
                    'errors_verdicts': [x.get('verdict') for x in (ev.get('errors') or []) if isinstance(x, dict) and x.get('verdict')],
                }
            )
        if items:
            comp = run_gemini_mindmap_compare(subject, title, items)
            for c in comp.get('comparisons', []) if isinstance(comp, dict) else []:
                if not isinstance(c, dict):
                    continue
                nm = _normalize_concept_name(c.get('name'))
                if not nm:
                    continue
                # Map to node ids by name
                for node_id, node in nodes.items():
                    if _normalize_concept_name(node.name) == nm:
                        analysis[str(node_id)] = {
                            'summary': str(c.get('summary') or '').strip(),
                            'gaps': c.get('gaps') if isinstance(c.get('gaps'), list) else [],
                            'actions': c.get('actions') if isinstance(c.get('actions'), list) else [],
                        }
                        break
    except Exception as exc:
        app.logger.warning('Mind map comparison analysis skipped: %s', exc)

    # Persist snapshot (best-effort)
    try:
        import json

        snap = MindMapSnapshot(
            user_id=owner_user_id,
            source_type=source_type,
            source_id=source_id_int,
            root_node_id=root_node.id,
            map_json=json.dumps(
                {
                    'root_id': root_node.id,
                    'nodes': [n.to_dict() for n in nodes.values()],
                    'edges': edges,
                    'evidence': evidence,
                    'analysis': analysis,
                },
                ensure_ascii=False,
            ),
            highlights_json=json.dumps(highlight_counts, ensure_ascii=False),
            related_json=json.dumps(related_payload, ensure_ascii=False),
        )
        save_and_commit(snap)
    except Exception:
        pass

    return jsonify(
        {
            'generated_at': isoformat_utc_z(datetime.now(timezone.utc)),
            'source': {
                'type': source_type,
                'id': source_id_int,
                'user_id': owner_user_id,
                'title': title,
                'subject': subject,
            },
            'root_id': root_node.id,
            'nodes': [n.to_dict() for n in nodes.values()],
            'edges': edges,
            'highlights': [{'node_id': nid, 'count': cnt} for nid, cnt in sorted(highlight_counts.items(), key=lambda kv: -kv[1])],
            'related': related_payload,
            'evidence': evidence,
            'analysis': analysis,
        }
    )


# --- Routes: Note Assistant --------------------------------------------
@app.route('/api/note/entries', methods=['GET', 'POST'])
def note_entries():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_note_access_user_ids(user)

    if request.method == 'GET':
        items = (
            NoteAssistantEntry.query.filter(NoteAssistantEntry.user_id.in_(access_user_ids))
            .order_by(NoteAssistantEntry.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify([e.to_summary() for e in items])

    # POST: upload audio file (mp3/wav/webm) and create a note entry.
    audio_file = request.files.get('audio')
    if not audio_file:
        return jsonify({'message': '缺少音频文件字段 audio'}), 400

    focus_tag = (request.form.get('focus_tag') or '').strip()
    title = (request.form.get('title') or '').strip()
    subject = normalize_subject(request.form.get('subject')) if request.form.get('subject') else ''

    original_name = audio_file.filename or 'audio'
    mimetype = audio_file.mimetype or ''

    # Basic sanity: allow empty mimetype (some clients), but block obvious non-audio.
    if mimetype and not mimetype.lower().startswith('audio/'):
        # Some mobile browsers may use video/webm for audio-only recordings.
        if 'webm' not in mimetype.lower() and 'mp4' not in mimetype.lower():
            return jsonify({'message': f'不支持的文件类型：{mimetype}（请上传音频）'}), 400

    data = audio_file.read()
    if not data:
        return jsonify({'message': '音频内容为空'}), 400
    if len(data) > 80 * 1024 * 1024:
        return jsonify({'message': '音频过大（>80MB），请切分后上传'}), 400

    digest = sha256(data).hexdigest()

    # Write to a temp file (Windows requires closing the handle before ffmpeg/decoder reads it)
    suffix = _guess_audio_suffix(original_name, mimetype)
    fd, tmp_path = tempfile.mkstemp(prefix='note_audio_', suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)

        entry = NoteAssistantEntry(
            user_id=user.id,
            title=title or None,
            subject=subject or None,
            focus_tag=focus_tag or None,
            status='transcribing',
            audio_original_name=original_name,
            audio_mimetype=mimetype,
            audio_size=len(data),
            audio_sha256=digest,
        )
        save_and_commit(entry)

        try:
            transcript = transcribe_audio_file(tmp_path)
        except Exception as exc:
            entry.status = 'transcribe_failed'
            entry.transcript_text = ''
            save_and_commit(entry)
            # Do not hard-fail the upload; return entry id + status for UI to display.
            return (
                jsonify(
                    {
                        'id': entry.id,
                        'status': entry.status,
                        'message': str(exc),
                        'hint': '请检查音频格式/文件扩展名是否正确，并确认已安装 ffmpeg。浏览器录音建议使用 webm。',
                    }
                ),
                201,
            )

        entry.transcript_text = transcript
        entry.status = 'transcribed'
        save_and_commit(entry)
        return (
            jsonify(
                {
                    'id': entry.id,
                    'status': entry.status,
                    'transcript': transcript,
                    'summary': None,
                    'tasks': [],
                }
            ),
            201,
        )
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception('Note upload/processing failed: %s', exc)
        return (
            jsonify(
                {
                    'message': '上传音频后处理失败',
                    'detail': str(exc),
                }
            ),
            500,
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.route('/api/note/entries/<int:entry_id>', methods=['GET', 'DELETE'])
def note_entry_detail(entry_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_note_access_user_ids(user)
    entry = NoteAssistantEntry.query.filter(NoteAssistantEntry.id == entry_id).filter(
        NoteAssistantEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到笔记记录'}), 404

    if request.method == 'DELETE':
        db.session.delete(entry)
        db.session.commit()
        return jsonify({'message': '已删除', 'id': entry_id})

    return jsonify(entry.to_detail())


@app.route('/api/note/session', methods=['POST'])
def note_create_session():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    data = request.get_json() or {}
    focus_tag = str(data.get('focus_tag') or '').strip()
    title = str(data.get('title') or '').strip()
    subject = normalize_subject(data.get('subject')) if data.get('subject') else ''

    session_id = uuid.uuid4().hex
    entry = NoteAssistantEntry(
        user_id=user.id,
        session_id=session_id,
        title=title or None,
        subject=subject or None,
        focus_tag=focus_tag or None,
        status='recording',
        transcript_text='',
    )
    save_and_commit(entry)
    return jsonify({'session_id': session_id, 'entry_id': entry.id})


@app.route('/api/note/session/<session_id>/chunk', methods=['POST'])
def note_append_chunk(session_id: str):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_note_access_user_ids(user)
    entry = NoteAssistantEntry.query.filter_by(session_id=session_id).filter(
        NoteAssistantEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到会话'}), 404

    audio_file = request.files.get('audio')
    if not audio_file:
        return jsonify({'message': '缺少音频分片字段 audio'}), 400

    data = audio_file.read()
    if not data:
        return jsonify({'message': '音频分片为空'}), 400
    if len(data) > 10 * 1024 * 1024:
        return jsonify({'message': '单个分片过大（>10MB）'}), 400

    original_name = audio_file.filename or 'chunk.webm'
    mimetype = audio_file.mimetype or ''

    # Always persist chunk for finalize fallback
    try:
        _save_note_session_chunk(session_id, original_name, mimetype, data)
    except Exception as exc:
        app.logger.warning('Failed to save note chunk: %s', exc)

    # 当前策略：不做实时转写，仅保存分片；停止录音后统一合并+转写。
    return jsonify({'ok': True, 'entry_id': entry.id})


@app.route('/api/note/session/<session_id>/finalize', methods=['POST'])
def note_finalize_session(session_id: str):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_note_access_user_ids(user)
    entry = NoteAssistantEntry.query.filter_by(session_id=session_id).filter(
        NoteAssistantEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到会话'}), 404

    # Prefer merged transcription when chunks exist (chunk-level text is often inaccurate).
    try:
        session_dir = _note_session_dir(session_id)
        chunks = sorted(
            [p for p in session_dir.glob('chunk_*') if p.is_file()],
            key=lambda p: p.name,
        )
    except Exception:
        chunks = []

    if chunks:
        with tempfile.TemporaryDirectory() as tmp:
            wav_out = Path(tmp) / 'session.wav'
            ok, err = _ffmpeg_concat_to_wav(chunks, wav_out)
            if not ok:
                return (
                    jsonify(
                        {
                            'message': '暂无可用转写文本（分片合并失败）',
                            'detail': err,
                        }
                    ),
                    400,
                )
            try:
                mp3_out = Path(tmp) / 'session.mp3'
                mp3_ok, mp3_err = _ffmpeg_to_mp3(wav_out, mp3_out)
                audio_for_asr = mp3_out if mp3_ok else wav_out
                transcript = transcribe_audio_file(str(audio_for_asr))
            except Exception as exc:
                return jsonify({'message': f'合并后转写失败：{exc}'}), 400

            if not mp3_ok:
                app.logger.warning('Finalize mp3 encode failed, fell back to wav ASR: %s', mp3_err)

        entry.transcript_text = (transcript or '').strip()
        if not (entry.transcript_text or '').strip():
            return jsonify({'message': '合并后转写结果为空，请检查录音是否有声音或格式是否可解码'}), 400
        entry.status = 'transcribed'
        save_and_commit(entry)
    elif not (entry.transcript_text or '').strip():
        return jsonify({'message': '暂无可用转写文本，请先录音一段或等待模型初始化完成'}), 400
    return jsonify(
        {
            'id': entry.id,
            'status': entry.status,
            'transcript': entry.transcript_text or '',
            'summary': None,
            'tasks': [],
        }
    )


@app.route('/api/note/entries/<int:entry_id>/summarize', methods=['POST'])
def note_entry_summarize(entry_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    access_user_ids = get_note_access_user_ids(user)
    entry = NoteAssistantEntry.query.filter(NoteAssistantEntry.id == entry_id).filter(
        NoteAssistantEntry.user_id.in_(access_user_ids)
    ).first()
    if not entry:
        return jsonify({'message': '未找到笔记记录'}), 404

    data = request.get_json(silent=True) or {}
    new_transcript = str(data.get('transcript') or '').strip()
    new_focus_tag = str(data.get('focus_tag') or '').strip()

    if new_transcript:
        entry.transcript_text = new_transcript
    if new_focus_tag:
        entry.focus_tag = new_focus_tag

    if not (entry.transcript_text or '').strip():
        return jsonify({'message': '暂无可用于总结的转写文本'}), 400

    entry.summary_json = ''
    entry.tasks_json = ''
    entry.status = 'summarizing'
    save_and_commit(entry)

    start_note_summary_job(entry.id)
    return jsonify({'id': entry.id, 'status': entry.status})


# --- Routes: Profile ----------------------------------------------------
@app.route('/api/profile', methods=['GET', 'PUT'])
def profile():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    if request.method == 'GET':
        return jsonify(user.to_safe_dict())

    data = request.get_json() or {}
    if 'username' in data:
        new_username = (data['username'] or '').strip()
        if not new_username:
            return jsonify({'message': '账户名不能为空'}), 400
        if not USERNAME_PATTERN.match(new_username):
            return jsonify({'message': '账户名需为 3-20 位字母、数字或 ._-'}), 400
        if new_username != user.username and User.query.filter_by(username=new_username).first():
            return jsonify({'message': '该账户名已被占用'}), 400
        user.username = new_username
    if 'display_name' in data:
        user.display_name = data['display_name'] or user.display_name
    if 'age' in data:
        user.age = data['age'] if data['age'] is not None else None
    if 'role' in data:
        user.role = 'parent' if data['role'] == 'parent' else 'student'
    if 'courses' in data:
        courses = data['courses']
        if isinstance(courses, list):
            user.set_courses(courses)
        elif isinstance(courses, str):
            user.set_courses([item.strip() for item in courses.split(',') if item.strip()])

    linked_email = data.get('linked_email')
    if linked_email:
        other = User.query.filter_by(email=linked_email.lower().strip()).first()
        if not other:
            return jsonify({'message': '未找到要绑定的账号'}), 404
        if other.id == user.id:
            return jsonify({'message': '不能绑定自己'}), 400
        if other.role == user.role:
            return jsonify({'message': '绑定角色需要一方为学生另一方为家长'}), 400

        # Multi-child friendly: store relation on student side (student.linked_user_id = parent.id)
        if (user.role or '') == 'parent' and (other.role or '') == 'student':
            other.linked_user_id = user.id
            save_and_commit(other)
        elif (user.role or '') == 'student' and (other.role or '') == 'parent':
            user.linked_user_id = other.id
        else:
            # fallback
            user.linked_user = other
    elif linked_email == '':
        # Clear binding
        if (user.role or '') == 'student':
            user.linked_user_id = None
        else:
            # parent side: unbind all children
            try:
                children = User.query.filter_by(linked_user_id=user.id, role='student').all()
                for child in children:
                    child.linked_user_id = None
                    db.session.add(child)
                db.session.commit()
            except Exception:
                pass

    save_and_commit(user)
    return jsonify(user.to_safe_dict())


@app.route('/api/parent/children', methods=['GET'])
def parent_children():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    if (user.role or '') != 'parent':
        return jsonify({'message': '仅家长可用'}), 403

    children = User.query.filter_by(linked_user_id=user.id, role='student').order_by(User.created_at.desc()).all()
    return jsonify({'items': [c.to_linked_dict() for c in children]})


@app.route('/api/bind/requests', methods=['GET', 'POST'])
def bind_requests():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    if request.method == 'GET':
        # Student inbox
        if (user.role or '') != 'student':
            return jsonify({'items': []})
        items = (
            BindRequest.query.filter_by(student_id=user.id, status='pending')
            .order_by(BindRequest.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({'items': [r.to_student_dict() for r in items]})

    # Create request (parent)
    if (user.role or '') != 'parent':
        return jsonify({'message': '仅家长可发起绑定请求'}), 403

    data = request.get_json() or {}
    method = str(data.get('method') or '').strip()
    value = str(data.get('value') or '').strip()
    if method not in ('email', 'nickname'):
        return jsonify({'message': 'method 必须为 email 或 nickname'}), 400
    if not value:
        return jsonify({'message': '请输入 email 或 昵称'}), 400

    target_student: User | None = None
    if method == 'email':
        target_student = User.query.filter_by(email=value.lower()).first()
    else:
        # nickname binding: match display_name first, then username
        candidates = User.query.filter_by(display_name=value, role='student').all()
        if len(candidates) == 1:
            target_student = candidates[0]
        elif len(candidates) > 1:
            return jsonify({'message': '该昵称匹配到多个学生，请改用邮箱绑定'}), 409
        else:
            target_student = User.query.filter_by(username=value, role='student').first()

    if not target_student:
        return jsonify({'message': '未找到学生账号'}), 404
    if target_student.id == user.id:
        return jsonify({'message': '不能绑定自己'}), 400
    if (target_student.role or '') != 'student':
        return jsonify({'message': '目标账号不是学生'}), 400

    # Avoid duplicate pending
    existing = BindRequest.query.filter_by(parent_id=user.id, student_id=target_student.id, status='pending').first()
    if existing:
        return jsonify({'request': existing.to_parent_dict(), 'message': '已发送过请求，等待学生同意'}), 200

    req = BindRequest(parent_id=user.id, student_id=target_student.id, status='pending')
    save_and_commit(req)
    return jsonify({'request': req.to_parent_dict()}), 201


@app.route('/api/bind/requests/<int:request_id>/respond', methods=['POST'])
def bind_request_respond(request_id: int):
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    if (user.role or '') != 'student':
        return jsonify({'message': '仅学生可处理绑定请求'}), 403

    req = BindRequest.query.filter_by(id=request_id).first()
    if not req or req.student_id != user.id:
        return jsonify({'message': '未找到绑定请求'}), 404
    if (req.status or '') != 'pending':
        return jsonify({'message': '该请求已处理'}), 400

    data = request.get_json() or {}
    action = str(data.get('action') or '').strip()
    if action not in ('approve', 'reject'):
        return jsonify({'message': 'action 必须为 approve 或 reject'}), 400

    if action == 'approve':
        # Bind: student -> parent
        user.linked_user_id = req.parent_id
        save_and_commit(user)
        req.status = 'approved'
    else:
        req.status = 'rejected'

    req.responded_at = datetime.utcnow()
    save_and_commit(req)
    return jsonify({'message': '已处理', 'request': req.to_student_dict()})


@app.route('/api/profile/password', methods=['PUT'])
def update_password():
    user, error_response, status = require_auth()
    if error_response:
        return error_response, status

    data = request.get_json() or {}
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({'message': '请输入当前密码与新密码'}), 400
    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'message': '当前密码不正确'}), 400

    user.password_hash = generate_password_hash(new_password)
    save_and_commit(user)

    return jsonify({'message': '密码已更新'})


if __name__ == '__main__':
    with app.app_context():
        ensure_error_book_entry_schema()
        ensure_note_assistant_schema()
        ensure_knowledge_schema()
        ensure_bind_schema()
        db.create_all()
    app.run(host='0.0.0.0', port=3000, debug=True)