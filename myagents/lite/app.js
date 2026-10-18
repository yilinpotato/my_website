(() => {
  const key='myagents-lite-v1', api='/myagents/api';
  const defaults={agents:[
    {id:'coordinator',name:'小柚',role:'协调者',prompt:'你负责理解用户目标、拆分工作、分派给合适员工，并基于他们的结果给出清晰可执行的中文总结。'},
    {id:'researcher',name:'知远',role:'研究员',prompt:'你负责调研、分析、核对风险和事实。输出关键发现、依据、待确认项与建议。'},
    {id:'executor',name:'行知',role:'执行者',prompt:'你负责把子任务转成可执行方案。输出具体步骤、优先级、交付物和验收标准。'}],tasks:[]};
  let state; try{state=JSON.parse(localStorage.getItem(key))||defaults}catch{state=defaults}
  const $=s=>document.querySelector(s), esc=x=>String(x??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const agent=id=>state.agents.find(a=>a.id===id); const save=()=>localStorage.setItem(key,JSON.stringify(state));
  const request=async(path,options={})=>{const r=await fetch(api+path,options),d=await r.json();if(!r.ok)throw new Error(d.error||'请求失败');return d};
  function render(){
    $('#agents').innerHTML=state.agents.map(a=>`<article class="agent"><span class="avatar">${esc(a.name[0])}</span><div><strong>${esc(a.name)}</strong><small>${esc(a.role)}</small></div></article>`).join('');
    $('#agentDirectory').innerHTML=state.agents.map(a=>`<article class="agent-directory-card"><span class="avatar">${esc(a.name[0])}</span><div><strong>${esc(a.name)}</strong><small>${esc(a.role)}</small><p>${esc(a.prompt||'尚未配置专属提示词')}</p></div><div class="agent-card-actions"><span class="agent-state">可用</span><button data-edit-agent="${esc(a.id)}">编辑提示词</button></div></article>`).join('');
    $('#ownerSelect').innerHTML=state.agents.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.role)}</option>`).join('');
    ['todo','doing','done'].forEach(status=>{const list=state.tasks.filter(t=>t.status===status);document.querySelector(`.task-list[data-status="${status}"]`).innerHTML=list.map(t=>`<article class="task" data-id="${esc(t.id)}"><p>${esc(t.title)}</p><div><span class="owner">${esc(agent(t.owner_id||t.owner)?.name||'未分配')}</span><span class="task-actions"><button data-move="prev">‹</button><button data-move="next">›</button><button data-delete>×</button></span></div></article>`).join('');$(`#${status==='todo'?'todo':status==='doing'?'doing':'done'}Num`).textContent=list.length});
    $('#agentCount').textContent=state.agents.length;$('#taskCount').textContent=state.tasks.length;$('#doneCount').textContent=state.tasks.filter(t=>t.status==='done').length;save();
  }
  async function syncBackend(){try{const [agents,tasks]=await Promise.all([request('/agents'),request('/tasks')]);if(agents.length)state.agents=agents;state.tasks=tasks.map(t=>({...t,owner:t.owner_id}));render()}catch{render()}}
  const toggle=s=>$(s).classList.toggle('hidden');
  $('#addAgent').onclick=()=>toggle('#agentForm'); document.querySelector('.open-agent').onclick=()=>{switchView('chat');setTimeout(()=>toggle('#agentForm'),0)};
  $('#newTask').onclick=()=>toggle('#taskForm'); document.querySelectorAll('.cancel').forEach(b=>b.onclick=()=>b.closest('form').classList.add('hidden'));
  $('#agentForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{const created=await request('/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.get('name').trim(),role:f.get('role'),prompt:'请按照你的角色完成分配的任务，并输出可验证的成果。'})});state.agents.push(created);e.target.reset();e.target.classList.add('hidden');render()}catch(err){alert(err.message)}};
  $('#taskForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{const created=await request('/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:f.get('title').trim(),owner_id:f.get('owner')})});state.tasks.unshift({...created,owner:created.owner_id});e.target.reset();e.target.classList.add('hidden');render()}catch(err){alert(err.message)}};
  $('#columns').onclick=async e=>{const b=e.target.closest('button'),card=e.target.closest('.task');if(!b||!card)return;const task=state.tasks.find(t=>t.id===card.dataset.id);if(b.dataset.delete){state.tasks=state.tasks.filter(t=>t!==task);render();return}const stages=['todo','doing','done'],next=stages.indexOf(task.status)+(b.dataset.move==='next'?1:-1);task.status=stages[Math.max(0,Math.min(2,next))];render();try{await request('/tasks/'+task.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:task.status})})}catch(err){alert(err.message);syncBackend()}};
  let editing;
  $('#agentDirectory').onclick=e=>{const b=e.target.closest('[data-edit-agent]');if(!b)return;editing=agent(b.dataset.editAgent);if(!editing)return;$('#promptAgentName').value=`${editing.name} · ${editing.role}`;$('#promptText').value=editing.prompt||'';$('#promptForm').classList.remove('hidden');$('#promptText').focus()};
  $('#promptForm').onsubmit=async e=>{e.preventDefault();if(!editing)return;try{const updated=await request('/agents/'+editing.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:$('#promptText').value.trim()})});Object.assign(editing,updated);$('#promptForm').classList.add('hidden');render()}catch(err){$('#promptError').textContent=err.message}};
  $('#cancelPrompt').onclick=()=>$('#promptForm').classList.add('hidden');
  const meta={chat:['群聊协作','协调者会自动拆分、分派并汇总','产品策划协作群','发送目标后，协调者会调度各员工并汇总结果'],agents:['智能员工','数字员工管理','智能员工','可编辑每位员工的专属提示词和职责'],tasks:['任务中心','任务树与执行状态','任务中心','统一查看手工任务和协作自动生成的子任务'],knowledge:['知识库','项目知识与全文检索','知识库','录入项目资料，供 AI 对话与团队协作参考'],logs:['操作日志','协作活动审计','操作日志','查看消息、任务与知识库的轻量活动记录']};
  function switchView(name){document.querySelectorAll('.app-view').forEach(v=>v.classList.toggle('is-active',v.dataset.page===name));document.querySelectorAll('#appNav a[data-view]').forEach(a=>a.classList.toggle('active',a.dataset.view===name));const m=meta[name];$('#topTitle').textContent=m[0];$('#topSubtitle').textContent=m[1];$('#pageTitle').textContent=m[2];$('#pageDescription').textContent=m[3];$('#newTask').style.display=name==='tasks'?'inline-block':'none';if(name==='logs')window.dispatchEvent(new Event('load-activity'))}
  document.querySelectorAll('#appNav a[data-view]').forEach(a=>a.onclick=e=>{e.preventDefault();switchView(a.dataset.view)});window.switchMyAgentsView=switchView;window.refreshMyAgents=syncBackend;render();switchView('chat');syncBackend();
})();
