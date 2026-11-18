"""Owner-private file tools and live, bounded agent settings; never shell access."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import ast
import subprocess
import sys
from filelock import FileLock

DEFAULTS = {'context_seconds':300, 'casual_interval_seconds':3600,
            'context_daily_checks':200, 'max_messages':3, 'max_model_calls':10,
            'max_tool_calls':12, 'persona_note':'', 'history_messages':32, 'history_chars':18000}
BOUNDS = {'context_seconds':(30,900), 'casual_interval_seconds':(1800,86400),
          'context_daily_checks':(0,500), 'max_messages':(1,8),
          'max_model_calls':(1,36), 'max_tool_calls':(1,60), 'history_messages':(4,100), 'history_chars':(2000,48000)}

def root():
    return Path(os.getenv('QQ_AGENT_WORKSPACE', '/home/myl/qq-companion/agent-workspace'))

def settings():
    try:
        result=json.loads((root()/'agent-config.json').read_text(encoding='utf-8'))
        validate(result)
        return dict(DEFAULTS,**result)
    except (OSError,ValueError,TypeError):return dict(DEFAULTS)

def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unsupported agent setting')
    for key,value in values.items():
        if key=='persona_note':
            if not isinstance(value,str) or len(value)>1500:raise ValueError('Persona note exceeds 1500 characters')
        elif type(value) is not int or not BOUNDS[key][0]<=value<=BOUNDS[key][1]:
            raise ValueError('Setting outside allowed range: '+key)

def allowed(actor, group='', initiative=None, scope=''):
    if initiative:return False
    groups={g.strip() for g in os.getenv('QQ_AGENT_ADMIN_GROUPS','').split(',') if g.strip()}
    if group:
        return bool(str(group) in groups and str(actor).isdigit()
                    and scope=='group:'+str(group)+':'+str(actor))
    owner=os.getenv('QQ_AGENT_OWNER_ID','')
    return bool(owner and str(actor)==owner and not group and not initiative
                and scope in ('private:'+owner,'test:private:'+owner))

MEMORY_FILES = {'记忆.md':'# 长期记忆\n\n记录主人明确要求记住的事实、偏好和更正；不保存密码或密钥。\n',
                '人物与特征.MD':'# 人物与特征\n\n仅记录明确提供的信息，区分事实与推测，不凭几句话给人贴标签。\n',
                '行为规则.md':'# 可编辑行为规则\n\n自然轻松地聊天；不知道的关键问题继续检索核实，不编造。\n'}

def memory_context(owner=False):
    w=Workspace();parts=[]
    for name in (list(MEMORY_FILES) if owner else ['行为规则.md']):
        try:parts.append(name+'\n'+w.read(name)['content'][:6000])
        except (OSError,ValueError):continue
    return '\n\n'.join(parts)

def project_call(name,args):
    w=Workspace(Path(__file__).parent)
    if name=='project_list':
        return {'files':[p.name for p in w.root.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt') and not p.name.startswith('.')]}
    if name in ('project_read','project_write'):
        path=args['path'];target=w.path(path)
        if target.parent!=w.root or target.suffix not in ('.py','.md','.txt'):
            raise ValueError('Only top-level bot Python, Markdown and text files')
        if name=='project_read':return w.read(path)
        if target.suffix=='.py':ast.parse(args['content'],filename=path)
        return w.write(path,args['content'],args['expected_sha256'])
    if name=='project_check':
        result=subprocess.run([sys.executable,'-m','unittest','qq_bot.test_agent_workspace','qq_bot.test_harness'],cwd=w.root.parent,capture_output=True,text=True,timeout=90)
        return {'passed':result.returncode==0,'output':(result.stdout+result.stderr)[-6000:]}
    if name=='project_restart':
        # Return acknowledgement before the service containing this turn exits.
        subprocess.Popen([sys.executable,'-c',"import time,subprocess;time.sleep(15);subprocess.run(['systemctl','--user','restart','qq-companion'],check=True)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return {'scheduled':True,'delay_seconds':15,'note':'Check service after restart; this is not a success receipt'}
    raise ValueError('Unknown project tool')

class Workspace:
    def __init__(self, directory=None):
        self.root=Path(directory or root()).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=FileLock(str(self.root/'.write.lock'),timeout=5)

    def path(self, relative):
        if not isinstance(relative,str) or not relative or len(relative)>240 or '\\' in relative:
            raise ValueError('Use a relative workspace path')
        p=Path(relative)
        if p.is_absolute() or any(x.startswith('.') or ':' in x for x in p.parts):
            raise ValueError('Hidden, parent, and absolute paths are not allowed')
        target=self.root
        for part in p.parts:
            target=target/part
            if target.is_symlink():raise ValueError('Symlinks are not allowed')
        if not target.resolve().is_relative_to(self.root):raise ValueError('Path escapes workspace')
        return target

    def read(self, path):
        target=self.path(path)
        if target.stat().st_size>65536:raise ValueError('File exceeds 64 KiB')
        data=target.read_bytes()
        return {'path':path,'content':data.decode('utf-8'),'sha256':hashlib.sha256(data).hexdigest(),
                'untrusted_content':True}

    def write(self, path, content, expected_sha256):
        if not isinstance(content,str) or len(content.encode('utf-8'))>65536:raise ValueError('Text exceeds 64 KiB')
        target=self.path(path)
        if target.name=='agent-config.json':validate(json.loads(content))
        if target.name=='persona.txt' and not content.strip():raise ValueError('Persona must not be empty')
        with self.lock:
            prior=target.read_bytes() if target.exists() else None
            actual=hashlib.sha256(prior).hexdigest() if prior is not None else ''
            if expected_sha256!=actual:raise ValueError('File changed; read it again before writing')
            if sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())+len(content.encode())>10*1024*1024:
                raise ValueError('Workspace quota reached (10 MiB including backups)')
            if prior is not None:
                backups=self.root/'.backups';backups.mkdir(exist_ok=True)
                backup=backups/(uuid.uuid4().hex+'.bak');backup.write_bytes(prior);backup.chmod(0o600)
            target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_name('.'+uuid.uuid4().hex+'.tmp')
            temp.write_text(content,encoding='utf-8');temp.chmod(0o600);temp.replace(target)
            with (self.root/'.audit.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps({'at':time.time(),'path':path,'previous_sha256':actual,
                                     'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})+'\n')
        return {'written':True,'path':path,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}

    def call(self,name,args):
        if name=='agent_persona_get':return self.read('persona.txt')
        if name=='agent_persona_set':
            return self.write('persona.txt',args['content'],args['expected_sha256'])
        if name=='workspace_list':
            files=[{'path':str(p.relative_to(self.root)),'bytes':p.stat().st_size}
                   for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()
                   and not any(x.startswith('.') for x in p.relative_to(self.root).parts)]
            return {'files':files[:100],'truncated':len(files)>100}
        if name=='workspace_read':return self.read(args['path'])
        if name=='workspace_write':return self.write(args['path'],args['content'],args['expected_sha256'])
        if name=='agent_config_get':
            p=self.root/'agent-config.json'
            return {'settings':dict(DEFAULTS,**(json.loads(p.read_text()) if p.exists() else {})),
                    'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else '',
                    'ranges':BOUNDS,'persona_note_max_chars':1500}
        if name=='agent_config_set':
            validate(args['settings'])
            return self.write('agent-config.json',json.dumps(args['settings'],ensure_ascii=False,indent=2),args['expected_sha256'])
        raise ValueError('Unknown workspace tool')
