"""MyAgents Lite Plus: DeepSeek API + SQLite, designed for small servers."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

import requests
from site_budget import completion, install
from flask import Flask, jsonify, request

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "myagents.db"
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")

app = Flask(__name__)
install(app)

DEFAULT_AGENTS = [
    ("coordinator", "小柚", "协调者", "你负责理解用户目标、拆分工作、分派给合适员工，并基于他们的结果给出清晰可执行的中文总结。"),
    ("researcher", "知远", "研究员", "你负责调研、分析、核对风险和事实。输出关键发现、依据、待确认项与建议。"),
    ("executor", "行知", "执行者", "你负责把子任务转成可执行方案。输出具体步骤、优先级、交付物和验收标准。"),
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def setup_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT, status TEXT NOT NULL DEFAULT 'todo', created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, agent_id TEXT, room_id TEXT NOT NULL DEFAULT 'main', role TEXT NOT NULL, content TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, agent_id TEXT, room_id TEXT NOT NULL DEFAULT 'main', content TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(title, content);
        """)
        if conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0:
            conn.executemany("INSERT INTO agents VALUES (?, ?, ?, ?)", DEFAULT_AGENTS)


def rows(query, params=()):
    with db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def add_message(role, content, agent_id=None, room_id="main"):
    with db() as conn:
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), agent_id, room_id, role, content, int(time.time())))


def call_model(api_key, system, messages, temperature=0.4):
    payload = {"model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), "messages": [{"role": "system", "content": system}, *messages], "temperature": temperature}
    result = completion(api_key, payload, 'myagents', timeout=75)
    return result["choices"][0]["message"]["content"].strip()



@app.get("/api/health")
def health():
    return jsonify({"ok": True, "model_configured": bool(os.getenv("DEEPSEEK_API_KEY")), "storage": "sqlite + fts5"})


@app.route("/api/agents", methods=["GET", "POST"])
def agents():
    if request.method == "GET":
        return jsonify(rows("SELECT * FROM agents ORDER BY name"))
    data = request.get_json(force=True)
    agent = {"id": str(uuid.uuid4()), "name": data["name"].strip(), "role": data.get("role", "执行者"), "prompt": data.get("prompt", "")}
    with db() as conn:
        conn.execute("INSERT INTO agents VALUES (:id,:name,:role,:prompt)", agent)
    return jsonify(agent), 201


@app.patch("/api/agents/<agent_id>")
def update_agent(agent_id):
    data = request.get_json(force=True)
    current = rows("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not current:
        return jsonify(error="agent not found"), 404
    agent = current[0]
    for field in ("name", "role", "prompt"):
        if field in data:
            value = str(data[field]).strip()
            if field in ("name", "role") and not value:
                return jsonify(error=f"{field} cannot be empty"), 400
            agent[field] = value
    with db() as conn:
        conn.execute("UPDATE agents SET name=:name, role=:role, prompt=:prompt WHERE id=:id", agent)
    return jsonify(agent)


@app.route("/api/tasks", methods=["GET", "POST"])
def tasks():
    if request.method == "GET":
        return jsonify(rows("SELECT * FROM tasks ORDER BY created_at DESC"))
    data = request.get_json(force=True)
    task = {"id": str(uuid.uuid4()), "title": data["title"].strip(), "owner_id": data.get("owner_id"), "status": "todo", "created_at": int(time.time())}
    with db() as conn:
        conn.execute("INSERT INTO tasks VALUES (:id,:title,:owner_id,:status,:created_at)", task)
    return jsonify(task), 201


@app.patch("/api/tasks/<task_id>")
def update_task(task_id):
    data = request.get_json(force=True)
    allowed = {"todo", "doing", "done"}
    if data.get("status") not in allowed:
        return jsonify(error="invalid status"), 400
    with db() as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (data["status"], task_id))
    return jsonify(ok=True)


@app.route("/api/knowledge", methods=["GET", "POST"])
def knowledge():
    if request.method == "POST":
        data = request.get_json(force=True)
        title, content = data["title"].strip(), data["content"].strip()
        with db() as conn:
            conn.execute("INSERT INTO knowledge (title, content) VALUES (?, ?)", (title, content))
        return jsonify(ok=True), 201
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify(rows("SELECT rowid AS id,title,content FROM knowledge ORDER BY rowid DESC LIMIT 10"))
    terms = " ".join(part + "*" for part in query.split() if part)
    try:
        return jsonify(rows("SELECT rowid AS id,title,highlight(knowledge,1,'<mark>','</mark>') AS content FROM knowledge WHERE knowledge MATCH ? LIMIT 8", (terms,)))
    except sqlite3.OperationalError:
        return jsonify([])


@app.get("/api/messages")
def messages():
    return jsonify(rows("SELECT messages.*, agents.name AS agent_name FROM messages LEFT JOIN agents ON messages.agent_id=agents.id WHERE room_id=? ORDER BY created_at ASC LIMIT 100", (request.args.get("room", "main"),)))


@app.post("/api/chat")
def chat():
    data = request.get_json(force=True)
    text, agent_id, room = data["message"].strip(), data.get("agent_id"), data.get("room_id", "main")
    if not text:
        return jsonify(error="message required"), 400
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return jsonify(error="DEEPSEEK_API_KEY is not configured on the server"), 503
    all_agents = rows("SELECT * FROM agents ORDER BY name")
    agent = next((a for a in all_agents if a["id"] == agent_id), None)
    coordinator = agent if agent and agent["role"] == "协调者" else next((a for a in all_agents if a["role"] == "协调者"), all_agents[0] if all_agents else {"id": None, "name": "协调者", "role": "协调者", "prompt": ""})
    history = rows("SELECT role,content FROM messages WHERE room_id=? ORDER BY created_at DESC LIMIT 12", (room,))[::-1]
    notes = rows("SELECT content FROM memories WHERE room_id=? AND (agent_id=? OR agent_id IS NULL) ORDER BY created_at DESC LIMIT 5", (room, agent_id))
    knowledge_hits = []
    try:
        knowledge_hits = rows("SELECT title,content FROM knowledge WHERE knowledge MATCH ? LIMIT 4", (" ".join(p + "*" for p in text.split() if p),))
    except sqlite3.OperationalError:
        pass
    context = "\n".join(f"- {x['title']}: {x['content']}" for x in knowledge_hits)
    add_message("user", text, coordinator["id"], room)
    base_context = f"已有记忆：{' | '.join(x['content'] for x in notes)}\n可用知识：{context}"
    events = [{"type": "planning", "agent": coordinator["name"], "detail": "正在理解目标并拆分协作任务"}]
    workers = [a for a in all_agents if a["id"] != coordinator["id"]][:4]
    try:
        plan_system = f"你是{coordinator['name']}，角色：协调者。{coordinator['prompt']}\n{base_context}\n你正在组织多员工协作。请先在心中拆分任务，然后用简洁中文说明分工。"
        plan = call_model(api_key, plan_system, [{"role": x["role"], "content": x["content"]} for x in history] + [{"role": "user", "content": text}])
        results = []
        for worker in workers:
            instruction = f"用户目标：{text}\n协调者的工作分解：{plan}\n请完成最适合你角色的部分，输出可供协调者合并的具体结果。"
            events.append({"type": "delegated", "agent": worker["name"], "detail": "已接收子任务并开始执行"})
            task = {"id": str(uuid.uuid4()), "title": f"{worker['name']}：{text[:80]}", "owner_id": worker["id"], "status": "doing", "created_at": int(time.time())}
            with db() as conn:
                conn.execute("INSERT INTO tasks VALUES (:id,:title,:owner_id,:status,:created_at)", task)
            worker_system = f"你是{worker['name']}，角色：{worker['role']}。{worker['prompt']}\n{base_context}\n不要寒暄；只提交子任务成果。"
            output = call_model(api_key, worker_system, [{"role": "user", "content": instruction}])
            results.append((worker, output))
            add_message("assistant", output, worker["id"], room)
            with db() as conn:
                conn.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
            events.append({"type": "completed", "agent": worker["name"], "detail": "已完成子任务并回传结果"})
        evidence = "\n\n".join(f"【{worker['name']} / {worker['role']}】\n{output}" for worker, output in results)
        synthesis_system = f"你是{coordinator['name']}，角色：协调者。{coordinator['prompt']}\n{base_context}\n基于下列员工成果，给用户输出一份统一的中文答复。保留可行动的结论；若有不确定性要明确说明。\n\n{evidence}"
        answer = call_model(api_key, synthesis_system, [{"role": "user", "content": text}], temperature=0.35)
        events.append({"type": "synthesis", "agent": coordinator["name"], "detail": "已汇总各员工成果"})
    except (requests.RequestException, KeyError, IndexError) as exc:
        return jsonify(error=f"DeepSeek request failed: {exc}"), 502
    add_message("assistant", answer, coordinator["id"], room)
    with db() as conn:
        conn.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), coordinator["id"], room, f"用户请求：{text[:400]}；回复摘要：{answer[:600]}", int(time.time())))
    return jsonify(answer=answer, sources=[x["title"] for x in knowledge_hits], events=events)


setup_db()

if __name__ == "__main__":
    app.run("127.0.0.1", 5010)
