const apiBase='/harness-api/v1';
const el={
  agents:document.getElementById('agents'), agent:document.getElementById('agent'), tools:document.getElementById('tools'),
  system:document.getElementById('system'), memory:document.getElementById('memory'), tasks:document.getElementById('tasks'),
  artifacts:document.getElementById('artifacts'), contextStats:document.getElementById('contextStats'), attachments:document.getElementById('attachments'),
  attachBtn:document.getElementById('attachBtn'), fileInput:document.getElementById('fileInput'), chatForm:document.getElementById('chatForm'),
  promptInput:document.getElementById('prompt'), messages:document.getElementById('messages'), chatList:document.getElementById('chatList'),
  stopRunBtn:document.getElementById('stopRunBtn'), runBtn:document.getElementById('runBtn'),
  newConversation:document.getElementById('newConversation'), activeConversationTitle:document.getElementById('activeConversationTitle'), attachmentPreview:document.getElementById('attachmentPreview'),
  responseStatus:document.getElementById('responseStatus'), tokenUsage:document.getElementById('tokenUsage'), diagnosticPanel:document.getElementById('diagnosticPanel'),
  searchNav:document.getElementById('searchNav'), pluginsNav:document.getElementById('pluginsNav'), automationsNav:document.getElementById('automationsNav'), artifactsNav:document.getElementById('artifactsNav'), projectNav:document.getElementById('projectNav'),
  chatSearch:document.getElementById('chatSearch'), sidePanel:document.getElementById('sidePanel'), sidePanelTitle:document.getElementById('sidePanelTitle'), sidePanelKicker:document.getElementById('sidePanelKicker'), sidePanelBody:document.getElementById('sidePanelBody'), closeSidePanel:document.getElementById('closeSidePanel'), panelBackdrop:document.getElementById('panelBackdrop')
};
const AGENTS={
  planner:{icon:'🧭',label:'Planner',className:'agent-planner'},
  coding:{icon:'⌘',label:'Coding',className:'agent-coding'},
  finance:{icon:'📈',label:'Finance',className:'agent-finance'},
  polymrkt:{icon:'◇',label:'Polymrkt',className:'agent-polymrkt'},
  dexter:{icon:'DX',label:'Dexter Research',className:'agent-dexter'},
  research:{icon:'🔎',label:'Research',className:'agent-research'},
  validation:{icon:'✓',label:'Validation',className:'agent-validation'},
  execution:{icon:'⚙',label:'Execution',className:'agent-execution'}
};
const DEFAULT_AGENT_ORDER=['finance','polymrkt','dexter','coding','planner','research','validation','execution'];
let tokenSnapshot={last_prompt_tokens:0,last_completion_tokens:0,tokens_generated_total:0};
let agentTouched=false;
function orderedAgents(agentNames=DEFAULT_AGENT_ORDER){
  const incoming=[...(agentNames||[])];
  const known=DEFAULT_AGENT_ORDER;
  const unknown=incoming.filter(x=>!DEFAULT_AGENT_ORDER.includes(x));
  return [...new Set([...known,...unknown])];
}
function renderAgentSelect(agentNames=DEFAULT_AGENT_ORDER){
  const names=orderedAgents(agentNames);
  if(!el.agent)return;
  const previous=agentTouched?el.agent.value:'finance';
  el.agent.innerHTML=names.map(x=>{const m=AGENTS[x]||{icon:'✦',label:x};return `<option value="${x}">${m.icon} ${m.label}</option>`}).join('');
  el.agent.value=names.includes(previous)?previous:(names.includes('finance')?'finance':names[0]);
}
let attachedFiles=[], currentSessionId='default', selectedFileId=null, activeFileIds=new Set(), conversations=[], lastAgents=[], lastTools=[], lastSystem={}, lastMemory={}, lastStatus={};
let currentChatController=null, isRunning=false;
let lastRenderedSessionKey='';
async function api(path,opts={}){
  const r=await fetch(`${apiBase}${path}`,{credentials:'same-origin',redirect:'manual',...opts});
  const contentType=r.headers.get('content-type')||'';
  const text=await r.text();
  let data={};
  if(contentType.includes('application/json')){
    try{data=text?JSON.parse(text):{}}catch(e){throw new Error('Respuesta JSON inválida del Harness')}
  }else{
    const isHtml=/^\s*<!doctype|^\s*<html/i.test(text);
    if(r.status===0||r.status===401||r.status===403||r.type==='opaqueredirect'||isHtml){
      throw new Error('Sesión expirada o API no disponible. Recarga e inicia sesión nuevamente.');
    }
    throw new Error((text||`HTTP ${r.status}`).slice(0,180));
  }
  if(!r.ok)throw new Error(data.error||data.detail||`${r.status}`);
  return data
}
async function get(path){return api(path)}
function setResponseStatus(state='idle', label='Idle', detail='Listo para recibir prompt'){
  if(!el.responseStatus)return;
  el.responseStatus.className=`response-status ${state}`;
  el.responseStatus.innerHTML=`<span></span><strong>${esc(label)}</strong><small>${esc(detail)}</small>`;
}
function setRunningState(running=false){
  isRunning=running;
  if(el.stopRunBtn)el.stopRunBtn.hidden=!running;
  if(el.runBtn)el.runBtn.disabled=running;
  if(el.promptInput)el.promptInput.disabled=running;
}
function updateTokenUsage(meta={}){
  const pickToken=(keys,current)=>{for(const key of keys){const value=Number(meta[key]);if(Number.isFinite(value)&&value>0)return value}return current||0};
  const prompt=pickToken(['last_prompt_tokens','prompt_tokens','prompt'],tokenSnapshot.last_prompt_tokens);
  const completion=pickToken(['last_completion_tokens','completion_tokens','response_tokens','completion'],tokenSnapshot.last_completion_tokens);
  const total=pickToken(['tokens_generated_total','total_tokens','session_tokens','total'],tokenSnapshot.tokens_generated_total);
  tokenSnapshot={
    last_prompt_tokens:prompt||tokenSnapshot.last_prompt_tokens||0,
    last_completion_tokens:completion||tokenSnapshot.last_completion_tokens||0,
    tokens_generated_total:total||tokenSnapshot.tokens_generated_total||0
  };
  if(el.tokenUsage)el.tokenUsage.textContent=`prompt ${prompt.toLocaleString()} · respuesta ${completion.toLocaleString()} · sesión ${total.toLocaleString()}`;
}
function renderOperationalStatus(status={}){
  lastStatus=status||{};
  const task=status.latest_task||{};
  const tokens=status.tokens||{};
  const taskStatus=task.status||'idle';
  if(taskStatus==='running')setResponseStatus('request','Request','Tarea en proceso, esperando respuesta');
  else if(taskStatus==='failed')setResponseStatus('error','Error',task.last_error||'La última tarea falló');
  else if(taskStatus==='completed')setResponseStatus('response','Response','Última respuesta completada');
  else setResponseStatus('idle','Idle','Listo para recibir prompt');
  updateTokenUsage(tokens);
  if(!el.diagnosticPanel)return;
  const stateClass=taskStatus==='failed'?'diag-error':(taskStatus==='running'?'diag-warn':(taskStatus==='completed'?'diag-ok':''));
  const lastTool=task.last_tool?`${task.last_tool}${task.last_tool_ok===false?' · error':''}`:'—';
  const detail=task.last_error||task.objective||'Sin tarea registrada';
  const duration=task.duration_ms?formatDuration(task.duration_ms):taskDuration(task);
  el.diagnosticPanel.innerHTML=[
    `<div><small>Estado</small><strong class="${stateClass}">${esc(taskStatus)}</strong></div>`,
    `<div title="${esc(detail)}"><small>Última tarea</small><strong>${esc(task.agent||'Sin tarea')}</strong></div>`,
    `<div><small>Pasos</small><strong>${Number(task.steps_count||0).toLocaleString()} · ${duration}</strong></div>`,
    `<div title="${esc(task.last_tool_error||'')}"><small>Herramienta</small><strong>${esc(lastTool)}</strong></div>`
  ].join('');
}
function esc(v=''){return String(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function formatDuration(ms){
  const value=Number(ms||0);
  if(!Number.isFinite(value)||value<=0)return '—';
  if(value<1000)return `${Math.round(value)} ms`;
  if(value<60000)return `${(value/1000).toFixed(value<10000?1:0)} s`;
  const minutes=Math.floor(value/60000);
  const seconds=Math.round((value%60000)/1000);
  return `${minutes}m ${seconds}s`;
}
function taskDuration(task={}){
  const meta=task.metadata||{};
  const started=Date.parse(task.started_at||meta.started_at||'');
  const finished=Date.parse(task.finished_at||meta.finished_at||'');
  if(!Number.isFinite(started)||!Number.isFinite(finished))return '—';
  return formatDuration(finished-started);
}
function renderMessageBody(body=''){
  const lines=String(body).split('\n');
  const chunks=[];
  for(let i=0;i<lines.length;){
    if(isMarkdownTableStart(lines,i)){
      const table=[];
      while(i<lines.length && lines[i].trim().startsWith('|')) table.push(lines[i++]);
      chunks.push(renderMarkdownTable(table));
      continue;
    }
    const text=[];
    while(i<lines.length && !isMarkdownTableStart(lines,i)) text.push(lines[i++]);
    chunks.push(renderTextBlock(text.join('\n')));
  }
  return chunks.join('');
}
function isMarkdownTableStart(lines,i){
  return Boolean(lines[i]?.trim().startsWith('|') && lines[i+1]?.trim().startsWith('|') && /^(\|\s*:?-{3,}:?\s*)+\|?$/.test(lines[i+1].trim()));
}
function renderTextBlock(text=''){
  const trimmed=text.trim();
  if(!trimmed)return '';
  const decision=trimmed.match(/^(Decisión|Trade posible|Señal MEXC Spot):\s*(UP|DOWN|BUY|SELL|NO TRADE|NONE)/i);
  if(decision){
    const klass=decision[2].toLowerCase().replace(/\s+/g,'-');
    const html=inlineFormat(trimmed).replace(/\n/g,'<br>');
    return `<div class="decision-line ${klass}">${html}</div>`;
  }
  const jsonValue=parseJsonBlock(trimmed);
  if(jsonValue)return renderJsonBlock(jsonValue);
  if(/Reglas operativas vigentes:/i.test(trimmed))return renderOperationalRules(trimmed);
  if(/^Endpoints institucionales disponibles:/i.test(trimmed))return renderEndpointBlock(trimmed);
  if(isSimpleBulletList(trimmed))return renderBulletList(trimmed);
  return `<div class="text-block">${inlineFormat(trimmed).replace(/\n/g,'<br>')}</div>`;
}
function inlineFormat(text=''){
  return esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
}
function parseJsonBlock(text=''){
  if(!/^\s*[\[{]/.test(text) || !/[\]}]\s*$/.test(text))return null;
  try{
    const parsed=JSON.parse(text);
    return parsed && typeof parsed==='object'?parsed:null;
  }catch(e){return null;}
}
function renderJsonBlock(value){
  const entries=Array.isArray(value)?value.map((item,idx)=>[String(idx+1),item]):Object.entries(value);
  if(!entries.length)return '<div class="json-card"><strong>JSON</strong><div class="empty">Sin datos.</div></div>';
  const rows=entries.map(([key,val])=>{
    const primitive=val===null || ['string','number','boolean'].includes(typeof val);
    const content=primitive?formatJsonPrimitive(val):`<pre>${esc(JSON.stringify(val,null,2))}</pre>`;
    return `<div class="json-field"><span>${esc(formatJsonKey(key))}</span><strong>${content}</strong></div>`;
  }).join('');
  return `<div class="json-card"><strong>Salida JSON</strong><div class="json-grid">${rows}</div></div>`;
}
function formatJsonPrimitive(value){
  if(typeof value==='number')return Number.isInteger(value)?value.toLocaleString():value.toLocaleString(undefined,{maximumFractionDigits:4});
  if(typeof value==='boolean')return value?'true':'false';
  if(value===null)return 'null';
  return esc(value);
}
function formatJsonKey(key=''){
  return String(key).replace(/_/g,' ').replace(/\b\w/g,letter=>letter.toUpperCase());
}
function isSimpleBulletList(text=''){
  const lines=text.split('\n').map(line=>line.trim()).filter(Boolean);
  return lines.length>1 && lines.every(line=>line.startsWith('- '));
}
function renderBulletList(text=''){
  const items=text.split('\n').map(line=>line.trim()).filter(Boolean).map(line=>line.replace(/^-\s*/,''));
  return `<ul class="md-list">${items.map(item=>`<li>${inlineFormat(item)}</li>`).join('')}</ul>`;
}
function renderOperationalRules(text=''){
  const lines=text.split('\n').map(line=>line.trim()).filter(Boolean);
  const cards=[];
  const intro=[];
  const endings=[];
  lines.forEach(line=>{
    if(/^Reglas operativas vigentes:/i.test(line))return;
    if(/^Endpoints institucionales disponibles:/i.test(line)){endings.push(renderEndpointBlock(line));return;}
    const match=line.match(/^-\s*([^:]+):\s*(.+)$/);
    if(match){cards.push(renderRuleCard(match[1],match[2]));return;}
    intro.push(`<div class="text-block">${inlineFormat(line)}</div>`);
  });
  return `<div class="rules-block"><div class="rules-title">Reglas operativas vigentes</div>${intro.join('')}${cards.join('')}${endings.join('')}</div>`;
}
function renderRuleCard(name='',payload=''){
  const details=renderRuleDetails(payload);
  const label=name.replace(/_/g,' ');
  return `<section class="rule-card"><h4>${esc(label)}</h4>${details}</section>`;
}
function renderRuleDetails(payload=''){
  const groups=[];
  const pattern=/'([^']+)'\s*:\s*(\[[^\]]*\]|'[^']*'|[^,}]+)/g;
  let match;
  while((match=pattern.exec(payload))){
    const key=match[1];
    const raw=match[2].trim();
    const values=parseRuleValues(raw);
    groups.push(`<div class="rule-group"><span class="rule-key">${esc(key)}</span><div class="rule-values">${values.map(value=>`<span>${esc(value)}</span>`).join('')}</div></div>`);
  }
  if(groups.length)return `<div class="rule-details">${groups.join('')}</div>`;
  return `<pre class="rule-json">${esc(payload)}</pre>`;
}
function parseRuleValues(raw=''){
  const list=raw.match(/^\[(.*)\]$/s);
  if(list){
    const values=[...list[1].matchAll(/'([^']*)'/g)].map(item=>item[1]);
    return values.length?values:[raw];
  }
  return [raw.replace(/^'|'$/g,'')];
}
function renderEndpointBlock(text=''){
  const endpoints=[...text.matchAll(/\/(?:harness-api\/v1\/agents\/status|health|logs|performance|transactions|rules|report)\b/g)].map(match=>match[0]);
  const unique=[...new Set(endpoints)];
  if(!unique.length)return `<div class="text-block">${inlineFormat(text)}</div>`;
  return `<div class="endpoint-card"><strong>Endpoints institucionales</strong><div class="endpoint-chips">${unique.map(endpoint=>`<code>${esc(endpoint)}</code>`).join('')}</div></div>`;
}
function renderMarkdownTable(rows=[]){
  const parsed=rows.map(row=>row.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(cell=>cell.trim()));
  const headers=parsed[0]||[];
  const body=parsed.slice(2);
  const signalIdx=headers.findIndex(h=>/^señal$/i.test(h)||/^signal$/i.test(h));
  const rowClass=row=>{
    if(signalIdx<0)return '';
    const signal=String(row[signalIdx]||'').trim().toLowerCase();
    if(signal==='buy')return ' class="signal-buy"';
    if(signal==='sell')return ' class="signal-sell"';
    return signal==='none'?' class="signal-none"':'';
  };
  return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${body.map(row=>`<tr${rowClass(row)}>${headers.map((_,idx)=>`<td>${esc(row[idx]||'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function detectAgent(body=''){const match=String(body).match(/^\[([^\]]+)\]/);return match?match[1]:'agent'}
function isNearMessagesBottom(){return !el.messages||el.messages.scrollHeight-el.messages.scrollTop-el.messages.clientHeight<80}
function scrollMessagesToBottom(){if(el.messages)el.messages.scrollTop=el.messages.scrollHeight}
async function copyText(text){
  if(navigator.clipboard?.writeText)return navigator.clipboard.writeText(text);
  const area=document.createElement('textarea');
  area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();document.execCommand('copy');area.remove();
}
function messageActions(role){
  if(role==='user'){
    return '<div class="msg-actions"><button class="msg-action copy-message" title="Copiar petición" aria-label="Copiar petición">⧉</button><button class="msg-action retry-message" title="Volver a ejecutar" aria-label="Volver a ejecutar">↻</button><button class="msg-action edit-message" title="Editar prompt" aria-label="Editar prompt">✎</button></div>';
  }
  return '<div class="msg-actions"><button class="msg-action copy-message" title="Copiar respuesta" aria-label="Copiar respuesta">⧉</button></div>';
}
function addMessage(role,body,{autoScroll=true,duration=''}={}){
  const item=document.createElement('div');
  const agentName=role==='user'?'user':detectAgent(body);
  const meta=AGENTS[agentName]||{icon:'✦',label:'Agent',className:'agent-generic'};
  item.className=`msg ${role==='user'?'msg-user':`msg-agent ${meta.className}`}`;
  item.dataset.raw=body;
  item.dataset.role=role;
  const label=role==='user'?'Tu petición':`${meta.icon} ${meta.label}${duration?` · ${duration}`:''}`;
  item.innerHTML=`<div class="msg-top"><span class="msg-label">${label}</span>${messageActions(role)}</div><div class="msg-body">${renderMessageBody(body)}</div>`;
  el.messages.appendChild(item);
  if(autoScroll)scrollMessagesToBottom();
}
function tableScrollPositions(){
  return [...el.messages.querySelectorAll('.md-table-wrap')].map(table=>table.scrollLeft);
}
function restoreTableScrollPositions(positions=[]){
  [...el.messages.querySelectorAll('.md-table-wrap')].forEach((table,idx)=>{
    if(positions[idx])table.scrollLeft=positions[idx];
  });
}
function renderHistory(items=[],{preserveScroll=true}={}){
  const shouldStick=!preserveScroll||isNearMessagesBottom();
  const previousTop=el.messages.scrollTop;
  const previousHeight=el.messages.scrollHeight;
  const tableScrolls=preserveScroll?tableScrollPositions():[];
  const taskDurations=(lastMemory.tasks||[]).map(taskDuration);
  let assistantIndex=0;
  el.messages.innerHTML='';
  items.forEach(m=>{
    const role=m.role==='user'?'user':'assistant';
    const duration=role==='assistant'?taskDurations[assistantIndex++]||'':'';
    addMessage(role,m.content,{autoScroll:false,duration});
  });
  restoreTableScrollPositions(tableScrolls);
  if(shouldStick)scrollMessagesToBottom();
  else el.messages.scrollTop=previousTop+(el.messages.scrollHeight-previousHeight);
}
function renderConversationList(){
  const term=(el.chatSearch?.value||'').toLowerCase().trim();
  const visible=term?conversations.filter(c=>String(c.title||'').toLowerCase().includes(term)):conversations;
  el.chatList.innerHTML=visible.map(c=>`<div class="chat-row ${c.id===currentSessionId?'active':''}" data-id="${c.id}"><div class="chat-title">${esc(c.title)}${typeof c.messages==='number'?` · ${c.messages}`:''}</div><div class="chat-actions"><button class="icon-btn rename-chat" title="Renombrar">✎</button><button class="icon-btn delete-chat" title="Borrar">⌫</button></div></div>`).join('') || '<div class="empty">Sin resultados.</div>';
  const current=conversations.find(c=>c.id===currentSessionId); if(el.activeConversationTitle) el.activeConversationTitle.textContent=current?.title||'Nueva conversación';
}
function shouldAutoTitleCurrentChat(){
  const current=conversations.find(c=>c.id===currentSessionId);
  const title=String(current?.title||'').trim().toLowerCase();
  const storedMessages=Number(current?.messages||0);
  const loadedMessages=Number(lastMemory?.messages?.length||0);
  return (!title||title==='nueva conversación'||title==='nueva conversacion') && storedMessages===0 && loadedMessages===0;
}
function deriveChatTitle(prompt=''){
  const text=String(prompt)
    .replace(/^\s*agente\s+\w+\s*:\s*/i,'')
    .replace(/\[[^\]]+\]/g,' ')
    .replace(/https?:\/\/\S+/g,' ')
    .replace(/[`*_#>{}[\]()"']/g,' ')
    .replace(/\s+/g,' ')
    .trim();
  if(!text)return 'Nueva conversación';
  const lower=text.toLowerCase();
  const pairs=[...new Set((text.match(/\b[A-Z0-9]{2,12}\/USDT\b|\b[A-Z0-9]{2,12}USDT\b/g)||[]).slice(0,3))];
  if(lower.includes('mexc'))return `MEXC Spot ${pairs.join(', ')||'scanner'}`.slice(0,60);
  if(lower.includes('polymarket'))return `Polymarket ${/\bbtc|bitcoin\b/i.test(text)?'BTC':'mercados'}`.trim();
  if(lower.includes('harness'))return 'Mejoras del Harness';
  if(lower.includes('server')||lower.includes('servidor'))return 'Revisión del servidor';
  const stop=new Set(['analiza','analizar','ayudame','ayúdame','puedes','quiero','vamos','hacer','para','con','del','los','las','una','uno','que','como','cómo','por','favor','agente','finance','coding','planner']);
  const words=text.split(/\s+/).filter(w=>w.length>2&&!stop.has(w.toLowerCase())).slice(0,6);
  const title=(words.join(' ')||text).replace(/[.,;:!?]+$/,'');
  return title.charAt(0).toUpperCase()+title.slice(1,60);
}
async function autoTitleFromFirstPrompt(message){
  if(!shouldAutoTitleCurrentChat())return;
  const title=deriveChatTitle(message);
  const current=conversations.find(c=>c.id===currentSessionId);
  if(!title||title==='Nueva conversación'||!current)return;
  current.title=title;
  renderConversationList();
  try{
    await api(`/conversations/${currentSessionId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
  }catch(e){}
}
function renderAttachments(){
  el.attachments.innerHTML=attachedFiles.map(f=>`<span class="attachment-chip ${f.id===selectedFileId?'active':''} ${activeFileIds.has(f.id)?'in-context':''}" data-preview-id="${f.id}"><label><input type="checkbox" data-context-id="${f.id}" ${activeFileIds.has(f.id)?'checked':''}> contexto</label>📎 ${esc(f.name)}<button data-id="${f.id}" title="Borrar archivo">✕</button></span>`).join('');
  renderAttachmentPreview(selectedFileId?attachedFiles.find(f=>f.id===selectedFileId):attachedFiles[0]);
}
function renderAttachmentPreview(file){
  if(!file){el.attachmentPreview.className='attachment-preview empty';el.attachmentPreview.textContent='Selecciona un archivo adjunto para ver qué entendió Hermes.';return}
  selectedFileId=file.id;
  el.attachmentPreview.className='attachment-preview';
  el.attachmentPreview.innerHTML=`<div class="preview-head"><strong>📎 ${esc(file.name)}</strong><span>${esc(file.ext||'archivo')} · ${(file.size||0).toLocaleString()} bytes · ${activeFileIds.has(file.id)?'en contexto':'fuera de contexto'}</span></div><pre>${esc(file.summary||'Sin vista previa disponible todavía.')}</pre>`;
  [...el.attachments.children].forEach(ch=>ch.classList.toggle('active',ch.dataset.previewId===file.id));
}
function renderContext(meta={}){const used=meta.last_prompt_tokens||0, windowSize=meta.context_window||16384, generated=meta.last_completion_tokens||0, total=meta.tokens_generated_total||0, pct=Math.min(100,Math.round((used/windowSize)*100));el.contextStats.innerHTML=`<div class="context-kpi"><small>Ventana de contexto</small><strong>${used.toLocaleString()} / ${windowSize.toLocaleString()}</strong><div class="context-bar"><span style="width:${pct}%"></span></div></div><div class="context-kpi"><small>Tokens generados · última respuesta</small><strong>${generated.toLocaleString()}</strong></div><div class="context-kpi"><small>Tokens generados · sesión</small><strong>${total.toLocaleString()}</strong></div>`}
async function loadConversations(preferred){const data=await get('/conversations');let items=data.conversations||[];if(!items.length){const created=await api('/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Nueva conversación'})});items=[created.conversation]}conversations=items;currentSessionId=preferred&&items.some(c=>c.id===preferred)?preferred:items[0].id;renderConversationList()}
function sessionRenderKey(memory={}){
  const meta=memory.metadata||{};
  const messages=memory.messages||[];
  const tasks=memory.tasks||[];
  const latestTask=tasks.at(-1)||{};
  return [
    currentSessionId,
    messages.length,
    tasks.length,
    meta.updated_at||'',
    latestTask.status||'',
    latestTask.finished_at||latestTask.duration_ms||''
  ].join('|');
}
function renderSessionDetails(m={}){
  if(el.memory)el.memory.textContent=JSON.stringify({summary:m.summary,messages:m.messages?.length},null,2);
  if(el.tasks)el.tasks.innerHTML=(m.tasks||[]).map(x=>`<li>${esc(x.agent)}: ${esc(x.status)}</li>`).join('')||'<li>Sin tareas</li>';
  if(el.artifacts)el.artifacts.innerHTML=(m.artifacts||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>Sin artefactos</li>';
  if(el.contextStats)renderContext(m.metadata||{});
}
async function loadSession(sessionId,{preserveScroll=true,force=false}={}){
  currentSessionId=sessionId;
  renderConversationList();
  const m=await get(`/memory?session_id=${encodeURIComponent(currentSessionId)}`);
  const key=sessionRenderKey(m);
  lastMemory=m;
  if(force||key!==lastRenderedSessionKey){
    renderHistory(m.messages||[],{preserveScroll});
    renderSessionDetails(m);
    lastRenderedSessionKey=key;
  }else{
    updateTokenUsage(m.metadata||{});
  }
  const latest=(m.tasks||[]).at(-1);
  if(latest?.status==='running')setResponseStatus('request','Request','Procesando respuesta del agente');
}
async function refresh(){try{const status=await get(`/status?session_id=${encodeURIComponent(currentSessionId||'default')}`);renderOperationalStatus(status);lastTools=status.tools||[];lastSystem=status.system||{};if(el.tools)el.tools.innerHTML=lastTools.map(x=>`<li>${esc(x)}</li>`).join('');if(el.system)el.system.textContent=JSON.stringify(lastSystem,null,2);const a=await get('/agents');lastAgents=orderedAgents(a.agents||DEFAULT_AGENT_ORDER);renderAgentSelect(lastAgents);if(el.agents)el.agents.innerHTML=lastAgents.map(x=>{const m=AGENTS[x]||{icon:'✦',label:x};return `<li class="agent-item ${m.className||'agent-generic'}"><span>${m.icon}</span>${m.label}</li>`}).join('');if(currentSessionId&&!isRunning)await loadSession(currentSessionId,{preserveScroll:true});renderOperationalStatus(status)}catch(e){renderAgentSelect(DEFAULT_AGENT_ORDER);setResponseStatus('error','Error','No fue posible consultar el harness');if(el.system)el.system.textContent='No fue posible consultar el harness.'}}
el.attachBtn.onclick=()=>el.fileInput.click();
el.agent.onchange=()=>{agentTouched=true};
el.fileInput.onchange=async()=>{for(const file of el.fileInput.files){const fd=new FormData();fd.append('file',file);try{const data=await api('/files',{method:'POST',body:fd});attachedFiles.unshift(data.file);selectedFileId=data.file.id;activeFileIds.add(data.file.id)}catch(e){alert(e.message||'No fue posible subir el archivo')}}el.fileInput.value='';renderAttachments()};
el.attachments.onclick=async e=>{
  if(e.target.tagName==='BUTTON'){
    const id=e.target.dataset.id;
    if(!window.confirm('¿Borrar este archivo del servidor?')) return;
    await api(`/files/${id}`,{method:'DELETE'});
    attachedFiles=attachedFiles.filter(f=>f.id!==id);activeFileIds.delete(id);if(selectedFileId===id)selectedFileId=null;renderAttachments();return;
  }
  if(e.target.matches('[data-context-id]')){
    const id=e.target.dataset.contextId; if(e.target.checked) activeFileIds.add(id); else activeFileIds.delete(id); renderAttachments(); return;
  }
  const chip=e.target.closest('[data-preview-id]'); if(chip){selectedFileId=chip.dataset.previewId;renderAttachmentPreview(attachedFiles.find(f=>f.id===selectedFileId));}
};

function setActiveNav(button){[el.searchNav,el.pluginsNav,el.automationsNav,el.artifactsNav,el.projectNav].forEach(b=>b?.classList.remove('active'));button?.classList.add('active')}
function openPanel(kind,title,html){el.sidePanelKicker.textContent=kind;el.sidePanelTitle.textContent=title;el.sidePanelBody.innerHTML=html;el.sidePanel.classList.add('open');el.panelBackdrop.classList.add('open');el.sidePanel.setAttribute('aria-hidden','false')}
function closePanel(){el.sidePanel.classList.remove('open');el.panelBackdrop.classList.remove('open');el.sidePanel.setAttribute('aria-hidden','true');[el.searchNav,el.pluginsNav,el.automationsNav,el.artifactsNav,el.projectNav].forEach(b=>b?.classList.remove('active'))}
function openSearch(){setActiveNav(el.searchNav);el.chatSearch.hidden=false;el.chatSearch.focus();const hits=conversations.map(c=>`<div class="search-hit" data-open-chat="${c.id}"><strong>${esc(c.title)}</strong><div class="panel-mini">${c.messages||0} mensajes</div></div>`).join('')||'<div class="empty">No hay chats todavía.</div>';openPanel('Buscar','Buscar en chats',`<div class="panel-card"><h4>⌕ Búsqueda rápida</h4><p>Escribe en el campo de la barra izquierda para filtrar tus chats. También puedes abrir cualquiera desde aquí.</p></div><div class="search-results">${hits}</div>`)}
function openPlugins(){setActiveNav(el.pluginsNav);const tools=lastTools.map(t=>`<li class="panel-action"><span>◫ ${esc(t)}</span><span class="panel-mini">herramienta</span></li>`).join('')||'<li>Sin herramientas registradas</li>';const files=attachedFiles.map(f=>`<li class="panel-action"><span>📎 ${esc(f.name)}</span><span class="panel-mini">${esc(f.ext||'archivo')}</span></li>`).join('')||'<li>Sin archivos adjuntos</li>';openPanel('Complementos','Complementos del harness',`<div class="panel-card"><h4>◫ Herramientas conectadas</h4><ul class="panel-list">${tools}</ul></div><div class="panel-card"><h4>📎 Archivos disponibles</h4><ul class="panel-list">${files}</ul></div>`)}
function renderAutomationDashboard(data={}){
  const a=data.automation||data||{};
  const orders=a.orders||[];
  const errors=a.errors||[];
  const logs=a.logs||{};
  const status=a.status||'waiting';
  const age=a.last_age_seconds==null?'—':formatDuration(a.last_age_seconds*1000);
  const ordersHtml=orders.length?orders.map(o=>`<li class="trade-row"><span><strong>${esc(o.venue||'venue')}</strong> ${esc(o.market||o.ticker||'mercado')} ${esc(o.interval||'')} · ${esc(o.side||o.signal||o.preferred_side||'')}</span><span class="panel-mini">${Number(o.stake_usdt||0).toLocaleString()} USDT · p ${esc(o.price??'—')} · ${Math.round(Number(o.probability||0)*100)}%</span></li>`).join(''):'<li class="trade-row empty">No hubo trade simulado en el último ciclo.</li>';
  const errorHtml=errors.length?errors.map(x=>`<li>${esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join(''):'<li>Sin errores en el último ciclo.</li>';
  const stdout=(logs.stdout||[]).slice(-40).map(esc).join('\n')||'Sin salida reciente.';
  const stderr=(logs.stderr||[]).slice(-40).map(esc).join('\n')||'Sin errores recientes.';
  const files=(logs.files||[]).map(f=>`<li class="panel-action"><span>${esc(f.name)}</span><span class="panel-mini">${esc(f.size)} · ${esc(f.updated_at||'')}</span></li>`).join('')||'<li>Sin archivos de log todavía.</li>';
  return `<div class="automation-dashboard">
    <div class="automation-hero auto-${esc(status)}">
      <div><small>Automatización</small><strong>${esc(a.name||'Paper Trading')}</strong></div>
      <span>${esc(status).toUpperCase()}</span>
    </div>
    <div class="automation-grid">
      <div class="automation-kpi"><small>Mode</small><strong>${esc(a.mode||'paper').toUpperCase()}</strong></div>
      <div class="automation-kpi"><small>Último ciclo</small><strong>${esc(age)}</strong></div>
      <div class="automation-kpi"><small>Éxito</small><strong class="${a.success?'auto-ok-text':'auto-warn-text'}">${a.success?'Sí':'No'}</strong></div>
      <div class="automation-kpi"><small>Trades</small><strong>${orders.length.toLocaleString()}</strong></div>
      <div class="automation-kpi"><small>Bankroll</small><strong>${Number(a.bankroll_usdt||0).toLocaleString()} USDT</strong></div>
      <div class="automation-kpi"><small>Logs</small><strong>${esc(logs.total_size||'0 B')}</strong></div>
    </div>
    <div class="panel-card"><h4>Trades simulados</h4><ul class="panel-list">${ordersHtml}</ul></div>
    <div class="panel-card"><h4>Últimos logs</h4><div class="log-tabs"><div><small>Salida</small><pre class="log-box">${stdout}</pre></div><div><small>Errores</small><pre class="log-box">${stderr}</pre></div></div></div>
    <div class="panel-card"><h4>Salud de logs</h4><p>${esc(logs.retention||'Retención pendiente')}</p><ul class="panel-list">${files}</ul></div>
    <div class="panel-card"><h4>Errores del ciclo</h4><ul class="panel-list">${errorHtml}</ul></div>
  </div>`;
}
async function openAutomations(){
  setActiveNav(el.automationsNav);
  openPanel('Automatizaciones','Dashboard operativo','<div class="panel-card"><h4>Cargando</h4><p>Consultando estado de automatizaciones, trades simulados y logs.</p></div>');
  try{
    const data=await get('/automations/paper-trading');
    el.sidePanelBody.innerHTML=renderAutomationDashboard(data);
  }catch(e){
    const tasks=(lastMemory.tasks||[]).map(t=>`<li class="panel-action"><span>◌ ${esc(t.agent||'agente')}</span><span class="panel-mini">${esc(t.status||'sin estado')}</span></li>`).join('')||'<li>No hay automatizaciones/tareas activas en esta conversación.</li>';
    el.sidePanelBody.innerHTML=`<div class="panel-card"><h4>No disponible</h4><p>${esc(e.message||'No fue posible consultar automatizaciones.')}</p></div><ul class="panel-list">${tasks}</ul>`;
  }
}
function artifactName(value){
  const text=String(value||'');
  return text.split('/').filter(Boolean).pop()||text||'artefacto';
}
function artifactKind(value){
  const name=artifactName(value).toLowerCase();
  if(/\.(png|jpg|jpeg|webp|gif|svg)$/.test(name))return 'imagen';
  if(/\.(csv|xlsx|xls|tsv)$/.test(name))return 'datos';
  if(/\.(json|jsonl)$/.test(name))return 'json';
  if(/\.(md|txt|log)$/.test(name))return 'texto';
  if(/\.(pdf|docx|pptx)$/.test(name))return 'documento';
  if(/\.(joblib|pkl|pt|onnx|h5)$/.test(name))return 'modelo';
  return 'artefacto';
}
function collectArtifacts(){
  const found=[];
  const add=value=>{const raw=String(value||'').trim().replace(/[),.;]+$/,''); if(raw&&!found.includes(raw))found.push(raw)};
  (lastMemory.artifacts||[]).forEach(add);
  const scan=JSON.stringify({messages:lastMemory.messages||[],tasks:lastMemory.tasks||[]});
  const matches=scan.match(/(?:storage\/artifacts|artifacts)\/[^\s\"'<>]+?\.(?:joblib|pkl|pt|onnx|h5|csv|xlsx|xls|tsv|json|jsonl|md|txt|log|pdf|docx|pptx|png|jpg|jpeg|webp|gif|svg)/g)||[];
  matches.forEach(add);
  const known=['storage/artifacts/models/polymarket_btc_updown_5m_lstm_gate.joblib','storage/artifacts/models/polymarket_btc_updown_5m_lstm_gate_dataset.csv','storage/artifacts/models/polymarket_btc_updown_5m_lstm_gate_metadata.json'];
  if(scan.includes('polymarket_btc_updown_5m_lstm_gate'))known.forEach(add);
  return found;
}
function renderArtifactRows(artifacts=[]){
  if(!artifacts.length)return '<li class="empty">Sin artefactos en esta conversación.</li>';
  return artifacts.map((artifact,idx)=>{
    const raw=String(artifact||'');
    const name=artifactName(raw);
    const kind=artifactKind(raw);
    return `<li class="artifact-row"><div><strong>${esc(name)}</strong><span>${esc(raw)}</span></div><div class="artifact-actions"><span class="panel-mini">${esc(kind)}</span><button class="icon-btn copy-artifact" data-copy-artifact="${esc(raw)}" title="Copiar ruta">⧉</button></div></li>`;
  }).join('');
}
function openArtifacts(){
  setActiveNav(el.artifactsNav);
  const artifacts=collectArtifacts();
  const kinds=artifacts.reduce((acc,item)=>{const kind=artifactKind(item);acc[kind]=(acc[kind]||0)+1;return acc},{});
  const kindBadges=Object.entries(kinds).map(([kind,count])=>`<span>${esc(kind)} · ${Number(count).toLocaleString()}</span>`).join('')||'<span>Sin archivos</span>';
  const tasks=(lastMemory.tasks||[]).filter(t=>(t.artifacts||[]).length||String(t.status||'').toLowerCase()==='completed').slice(-6).map(t=>`<li class="panel-action"><span>${esc(t.agent||'agente')} · ${esc(t.status||'sin estado')}</span><span class="panel-mini">${taskDuration(t)}</span></li>`).join('')||'<li>Sin tareas recientes con artefactos.</li>';
  openPanel('Artefactos','Artefactos de la conversación',`<div class="artifact-dashboard"><div class="artifact-hero"><div><small>Total</small><strong>${Number(artifacts.length).toLocaleString()}</strong></div><div class="artifact-badges">${kindBadges}</div></div><div class="panel-card"><h4>◇ Artefactos generados</h4><ul class="panel-list artifact-list">${renderArtifactRows(artifacts)}</ul></div><div class="panel-card"><h4>Tareas relacionadas</h4><ul class="panel-list">${tasks}</ul></div><div class="panel-card"><h4>Contexto</h4><p>Estos artefactos pertenecen a la conversación activa y se actualizan cuando cargas otro chat o el agente genera nuevos archivos.</p></div></div>`);
}
function renderContextPanel(meta={}){const used=meta.last_prompt_tokens||0,windowSize=meta.context_window||16384,generated=meta.last_completion_tokens||0,total=meta.tokens_generated_total||0,pct=Math.min(100,Math.round((used/windowSize)*100));return `<div class="project-context"><div class="context-kpi"><small>Ventana de contexto</small><strong>${used.toLocaleString()} / ${windowSize.toLocaleString()}</strong><div class="context-bar"><span style="width:${pct}%"></span></div></div><div class="context-kpi"><small>Tokens generados · última respuesta</small><strong>${generated.toLocaleString()}</strong></div><div class="context-kpi"><small>Tokens generados · sesión</small><strong>${total.toLocaleString()}</strong></div></div>`}
function openProject(){setActiveNav(el.projectNav);const systemPayload={compute:{gpu:'Tesla T4',mode:'cuda'},...lastSystem,compute:{gpu:'Tesla T4',mode:'cuda',...(lastSystem.compute||{})}};const detectedArtifacts=collectArtifacts();const artifacts=renderArtifactRows(detectedArtifacts);const tasks=(lastMemory.tasks||[]).map(t=>`<li class="panel-action"><span>${esc(t.agent||'agente')}: ${esc(t.status||'sin estado')}</span><span class="panel-mini">${taskDuration(t)}</span></li>`).join('')||'<li>Sin tareas activas.</li>';openPanel('Proyecto','QuantLab AI Capital',`<div class="project-grid"><div class="panel-card"><h4>▣ Proyecto</h4><p>Agent Harness sobre QuantLab AI Capital, con memoria de sesión, artefactos, herramientas y agentes especializados.</p></div><div class="panel-card"><h4>Contexto</h4>${renderContextPanel(lastMemory.metadata||{})}</div><div class="panel-card"><h4>Sistema</h4><pre class="system-json">${esc(JSON.stringify(systemPayload,null,2))}</pre></div><div class="panel-card"><h4>Artefactos</h4><div class="panel-mini" style="margin-bottom:10px">${Number(detectedArtifacts.length).toLocaleString()} detectado${detectedArtifacts.length===1?'':'s'} en memoria y mensajes</div><ul class="panel-list artifact-list">${artifacts}</ul></div><div class="panel-card"><h4>Tareas · tiempo de respuesta</h4><ul class="panel-list">${tasks}</ul></div><div class="panel-card"><h4>Accesos</h4><div class="quick-row"><a href="/dashboard/">Dashboard</a><a href="/jupyter/" target="_blank">Jupyter</a><a href="/llm/" target="_blank">LLM Local</a><a href="/dashboard/api/">API</a></div></div></div>`)}
el.closeSidePanel.onclick=closePanel;el.panelBackdrop.onclick=closePanel;el.searchNav.onclick=openSearch;el.pluginsNav.onclick=openPlugins;el.automationsNav.onclick=openAutomations;el.artifactsNav.onclick=openArtifacts;el.projectNav.onclick=openProject;el.chatSearch.oninput=renderConversationList;el.sidePanelBody.onclick=e=>{const copy=e.target.closest('[data-copy-artifact]');if(copy){copyText(copy.dataset.copyArtifact||'');copy.textContent='✓';setTimeout(()=>copy.textContent='⧉',900);return}const hit=e.target.closest('[data-open-chat]');if(hit){loadSession(hit.dataset.openChat,{preserveScroll:false});closePanel()}};

el.newConversation.onclick=async()=>{const created=await api('/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Nueva conversación'})});await loadConversations(created.conversation.id);await loadSession(created.conversation.id,{preserveScroll:false})};
el.chatList.onclick=async e=>{
  const row=e.target.closest('.chat-row'); if(!row) return; const id=row.dataset.id;
  if(e.target.closest('.rename-chat')){const current=conversations.find(c=>c.id===id);const title=window.prompt('Nombre de la conversación',current?.title||'');if(!title)return;await api(`/conversations/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});await loadConversations(id);return}
  if(e.target.closest('.delete-chat')){if(!window.confirm('¿Borrar esta conversación?'))return;await api(`/conversations/${id}`,{method:'DELETE'});await loadConversations();await loadSession(currentSessionId,{preserveScroll:false});return}
  await loadSession(id,{preserveScroll:false});
};
el.messages.onclick=async e=>{
  const msg=e.target.closest('.msg'); if(!msg) return;
  const raw=msg.dataset.raw||'';
  if(e.target.closest('.copy-message')){
    await copyText(raw);
    const btn=e.target.closest('.copy-message'); if(btn){const original=btn.textContent;btn.textContent='✓';setTimeout(()=>btn.textContent=original,900)}
    return;
  }
  if(e.target.closest('.edit-message')){el.promptInput.value=raw;el.promptInput.focus();return}
  if(e.target.closest('.retry-message')){await sendPrompt(raw);return}
};
function routeAgentFromPrompt(message=''){
  const match=String(message||'').match(/^\s*(finance|polymrkt|dexter|coding|planner|research|validation|execution)\s*:\s*(.+)$/is);
  if(!match)return {agent:el.agent.value||'finance',message};
  const agent=match[1].toLowerCase();
  const routedMessage=match[2].trim();
  if(el.agent && [...el.agent.options].some(option=>option.value===agent))el.agent.value=agent;
  return {agent,message:routedMessage||message};
}
async function sendPrompt(message){
  if(isRunning)return;
  const route=routeAgentFromPrompt(message);
  const selectedAgent=route.agent;
  const outboundMessage=route.message;
  currentChatController=new AbortController();
  el.promptInput.value='';
  addMessage('user',message,{autoScroll:true});
  await autoTitleFromFirstPrompt(message);
  setRunningState(true);
  setResponseStatus('request','Request','Prompt recibido, esperando al LLM');
  try{
    const data=await api('/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal:currentChatController.signal,body:JSON.stringify({session_id:currentSessionId,message:outboundMessage,agent:selectedAgent,file_ids:[...activeFileIds]})});
    const latestTask=(data.tasks||[]).at(-1)||{};
    addMessage('assistant',data.response||data.final||JSON.stringify(data),{autoScroll:true,duration:taskDuration(latestTask)});
    renderContext(data.metadata||{});
    updateTokenUsage(data.metadata||{});
    setResponseStatus('response','Response','Respuesta recibida correctamente');
    await loadConversations(currentSessionId);
  }catch(err){
    if(err.name==='AbortError'){
      setResponseStatus('idle','Detenido','Ejecución detenida desde la interfaz');
      addMessage('assistant','Ejecución detenida por el usuario.',{autoScroll:true});
    }else{
      setResponseStatus('error','Error',err.message||'La petición no se completó');
      addMessage('assistant',`Error: ${err.message}`,{autoScroll:true});
    }
  }finally{
    currentChatController=null;
    setRunningState(false);
  }
  refresh();
}
el.stopRunBtn.onclick=()=>{
  if(!currentChatController)return;
  currentChatController.abort();
};
el.chatForm.onsubmit=async e=>{e.preventDefault();const message=el.promptInput.value.trim();if(!message)return;await sendPrompt(message)};
renderAgentSelect(DEFAULT_AGENT_ORDER);
(async()=>{try{setResponseStatus('idle','Idle','Listo para recibir prompt');attachedFiles=(await get('/files')).files||[];activeFileIds=new Set(attachedFiles.map(f=>f.id));renderAttachments();await loadConversations();await refresh()}catch(e){setResponseStatus('error','Error','No fue posible consultar el harness');renderAgentSelect(DEFAULT_AGENT_ORDER);if(el.system)el.system.textContent='No fue posible consultar el harness.'}})();setInterval(refresh,5000);
