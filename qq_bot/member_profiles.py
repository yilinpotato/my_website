"""Group-specific member memory indexed by account ID, independent of display name."""
try:
    from .agent_workspace import Workspace
except ImportError:
    from agent_workspace import Workspace


def member_info(group, actor, sender):
    if not str(group).isascii() or not str(group).isdigit() or not str(actor).isascii() or not str(actor).isdigit():
        raise ValueError('Invalid group or account ID')
    store = Workspace()
    path = f'用户记忆/{group}/{actor}.md'
    name = str(sender.get('card') or sender.get('nickname') or actor)[:80]
    try:
        content = store.read(path)['content']
    except FileNotFoundError:
        content = f'# 用户记忆\n\n群号 {group}，QQ {actor}，建档时显示名 {name}\n'
        try:
            store.write(path, content, '')
        except ValueError:
            content = store.read(path)['content']
    return dict(display_name=name, nickname=str(sender.get('nickname',''))[:80], qq=str(actor),
                memory_path=path, memory=content[:2500])


def roster_path(group):
    if not str(group).isascii() or not str(group).isdigit():
        raise ValueError('Invalid group ID')
    return Workspace().root / '.rosters' / (str(group)+'.json')


def roster(group):
    import json
    try:
        return json.loads(roster_path(group).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []


def seed_members(group, members):
    import json
    import uuid
    path=roster_path(group)
    path.parent.mkdir(exist_ok=True)
    names=[{'qq':str(m['user_id']), 'name':str(m.get('card') or m.get('nickname') or m['user_id'])[:80]}
           for m in members if str(m.get('user_id','')).isdigit()]
    temp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(names,ensure_ascii=False),encoding='utf-8')
    temp.replace(path)
    for member in members:
        if str(member.get('user_id','')).isdigit():
            member_info(group, member['user_id'], member)

def named_messages(messages, names):
    def label(account):
        return '大肥鱼' if account=='bot' else names.get(str(account),'群成员')
    return [dict(speaker=row.get('display_name') or label(row.get('speaker')), text=row.get('text',''),
                 **({'to':label(row['to'])} if row.get('to') else {})) for row in messages]
