"""Discover installed DSH packages and persist compatible whale profile additions."""
import json
import os
import sqlite3
from pathlib import Path


RECIPES = {
    "dsh-tool-todo": ("待办 任务清单 多步骤计划 todo_write", {"allowParallelInProgress": False}),
    "dsh-compaction-tool-result-pruner": ("上下文 压缩 裁剪 过长工具结果", {}),
}


def packages():
    binary = Path(os.getenv("QQ_HARNESS_BIN", "/home/myl/qq-harness-runtime/dsh"))
    return binary.parent / "node_modules" / "@deepseek-ai"


def registry(root):
    db = sqlite3.connect(root / "harness-plugins.sqlite3", timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS enabled (name TEXT PRIMARY KEY)")
    return db


def search(root, query):
    words = str(query).casefold().split()
    with registry(root) as db:
        enabled = {r[0] for r in db.execute("SELECT name FROM enabled")}
    results = []
    for path in packages().glob("dsh-*/package.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        name = path.parent.name
        description = metadata.get("description", "")
        keywords = RECIPES.get(name, ("", {}))[0]
        if all(w in f"{name} {description} {keywords}".casefold() for w in words):
            results.append(dict(package=metadata["name"], description=description,
                                compatible=name in RECIPES, enabled=name in enabled))
    return sorted(results, key=lambda r: (not r["compatible"], r["package"]))[:20]


def enable(root, package):
    name = package.removeprefix("@deepseek-ai/")
    if name not in RECIPES or not (packages() / name / "package.json").is_file():
        raise ValueError("Package has no installed QQ integration")
    with registry(root) as db:
        db.execute("INSERT OR IGNORE INTO enabled VALUES (?)", (name,))
    return {"package": name, "enabled": True, "effective": "next_turn"}


def patches(root):
    with registry(root) as db:
        names = [r[0] for r in db.execute("SELECT name FROM enabled ORDER BY name")]
    return [{"insert": [{"id": "qq-" + n, "name": "@deepseek-ai/" + n,
                         "config": RECIPES[n][1]}]} for n in names if n in RECIPES]
