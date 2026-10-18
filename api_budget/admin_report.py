"""Read-only reporting over the shared budget ledger."""
import os
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo


def budget_report(selected=None):
    day = selected or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('日期格式应为 YYYY-MM-DD')
    path = os.getenv('SITE_BUDGET_DB', '/home/myl/api-budget/usage.sqlite3')
    conn = sqlite3.connect('file:'+path+'?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        rows = [dict(r) for r in conn.execute('SELECT * FROM calls WHERE day=? ORDER BY rowid DESC',(day,))]
        history = [dict(r) for r in conn.execute('SELECT day,SUM(amount) AS amount,COUNT(*) AS count FROM calls GROUP BY day ORDER BY day DESC LIMIT 30')]
    finally:
        conn.close()
    projects, visitors = {}, {}
    for row in rows:
        for groups, key in ((projects,row['project']),(visitors,row['person'])):
            entry = groups.setdefault(key,{'name':key,'amount':0,'count':0})
            entry['amount'] += row['amount']
            entry['count'] += 1
    used = sum(r['amount'] for r in rows)
    return dict(day=day,used=used,remaining=max(0,3_000_000_000-used),
                reserved=sum(r['amount'] for r in rows if r['status']=='reserved'),
                count=len(rows),records=rows[:200],history=history,
                projects=sorted(projects.values(),key=lambda r:r['amount'],reverse=True),
                visitors=sorted(visitors.values(),key=lambda r:r['amount'],reverse=True))
