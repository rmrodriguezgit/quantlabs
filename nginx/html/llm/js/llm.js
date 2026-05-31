const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
let activeModel='', selectedRuntimeModel='', gpuBusy=false, modelBusy=false, gpuState=null, modelState=null, currentController=null, lastPrompt='', modelCatalog=[];

async function jsonFetch(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
  return data;
}
const row=(label,value)=>`<div class="server-row"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;

function modelTemplate(name){
  return 'chatml';
}

function modelDefaults(name){
  if(/^ollama:/i.test(name))return {tokens:768,temp:0.45,specialist:'ollama'};
  if(/coder/i.test(name))return {tokens:1024,temp:0.2,specialist:'codex4u'};
  if(/phi-4/i.test(name))return {tokens:768,temp:0.35,specialist:'planner'};
  if(/qwen2\.5-14b/i.test(name))return {tokens:768,temp:0.45,specialist:'coding'};
  return {tokens:384,temp:0.7,specialist:'coding'};
}

async function loadModelCatalog(){
  const localPayload=await fetch('./models.json?ts='+Date.now(),{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():{models:[]}).catch(()=>({models:[]}));
  const local=(localPayload.models||[]).map(name=>({name,backend:'llama.cpp',value:name}));
  const ollamaPayload=await fetch('/ollama/models?ts='+Date.now(),{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():{data:[]}).catch(()=>({data:[]}));
  const ollama=(ollamaPayload.data||ollamaPayload.models||[])
    .map(item=>item.id||item.name||item.model||item)
    .filter(Boolean)
    .map(name=>({name,backend:'Ollama',value:`ollama:${name}`}));
  return [...local,...ollama];
}

async function loadActiveModel(){
  try{
    modelState=await jsonFetch('/admin/llm-model?ts='+Date.now());
    return modelState.active_model||'';
  }catch(_error){
    const payload=await jsonFetch('/llm-api/v1/models');
    const models=payload.data||payload.models||[];
    return models[0]?.id||models[0]?.name||models[0]?.model||'';
  }
}

function renderModels(catalog){
  const select=$('modelSelect');
  const previous=selectedRuntimeModel||select.value||'';
  modelCatalog=catalog;
  const groups=catalog.reduce((acc,item)=>{(acc[item.backend] ||= []).push(item);return acc;},{});
  select.innerHTML=Object.entries(groups).map(([backend,items])=>
    `<optgroup label="${esc(backend)}">${items.map(item=>`<option value="${esc(item.value)}">${esc(item.name)}</option>`).join('')}</optgroup>`
  ).join('');
  if(previous&&catalog.some(item=>item.value===previous))select.value=previous;
  else if(activeModel&&catalog.some(item=>item.value===activeModel))select.value=activeModel;
  else if(activeModel&&catalog.some(item=>item.value===`ollama:${activeModel}`))select.value=`ollama:${activeModel}`;
  selectedRuntimeModel=select.value||activeModel||'';
  $('modelName').textContent=selectedRuntimeModel||activeModel||'--';
  applyModelDefaults(select.value||activeModel);
  renderLoadButton();
}

function applyModelDefaults(name){
  const d=modelDefaults(name||'');
  if($('maxTokens'))$('maxTokens').value=d.tokens;
  if($('temperature'))$('temperature').value=d.temp;
  const subtitle=d.specialist==='codex4u'?' · especialista codex4u':(d.specialist==='ollama'?' · Ollama':'');
  if($('modelName'))$('modelName').textContent=displayModelName(name||selectedRuntimeModel||activeModel||'--')+subtitle;
}

function isOllamaModel(value){ return /^ollama:/i.test(value||''); }
function displayModelName(value){ return String(value||'').replace(/^ollama:/i,''); }
function selectedModelMeta(value){ return modelCatalog.find(item=>item.value===value)||null; }

function renderLoadButton(){
  const btn=$('loadModelBtn');
  if(!btn)return;
  const selected=$('modelSelect')?.value||'';
  const changed=Boolean(selected&&selected!==activeModel);
  if(isOllamaModel(selected)){
    btn.disabled=modelBusy||selectedRuntimeModel===selected;
    btn.textContent=modelBusy?'Cargando modelo...':(selectedRuntimeModel===selected?'Modelo activo':'Usar modelo Ollama');
    return;
  }
  btn.disabled=modelBusy||!changed;
  btn.textContent=modelBusy?'Cargando modelo...':(changed?'Cargar modelo':'Modelo activo');
}

async function loadGpuControl(){
  gpuState=await jsonFetch('/admin/gpu?ts='+Date.now());
  renderGpuControl();
}

function renderGpuControl(){
  const el=$('gpuControl');
  const available=Boolean(gpuState?.available);
  const enabled=Boolean(gpuState?.gpu_enabled);
  const status=available?(enabled?'GPU encendida':'GPU apagada'):'Control no disponible';
  const containers=(gpuState?.containers||[]).map(c=>`${c.name}: ${c.status}`).join(' · ')||'Sin datos';
  el.innerHTML=`<div class="gpu-state"><div><strong>${esc(status)}</strong><small>${esc(containers)}</small></div><span class="${enabled?'ok':'warn'}">${enabled?'ON':'OFF'}</span></div>
    <div class="gpu-buttons">
      <button id="gpuStart" ${gpuBusy||!available||enabled?'disabled':''}>Prender GPU</button>
      <button id="gpuStop" class="danger" ${gpuBusy||!available||!enabled?'disabled':''}>Apagar GPU</button>
    </div>`;
  $('gpuStart')?.addEventListener('click',()=>setGpuPower('start'));
  $('gpuStop')?.addEventListener('click',()=>setGpuPower('stop'));
}

async function setGpuPower(action){
  gpuBusy=true; renderGpuControl();
  try{
    gpuState=await jsonFetch('/admin/gpu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    await new Promise(resolve=>setTimeout(resolve, action==='start'?3500:900));
    await loadModels();
  }catch(error){
    $('serverInfo').insertAdjacentHTML('afterbegin',row('GPU error',error.message));
  }finally{
    gpuBusy=false; renderGpuControl();
  }
}

async function loadModels(){
  try{
    const [catalog,active]=await Promise.all([loadModelCatalog(),loadActiveModel()]);
    const localNames=(modelState?.models&&modelState.models.length)?modelState.models:catalog.filter(item=>item.backend==='llama.cpp').map(item=>item.name);
    const modelList=[
      ...localNames.map(name=>({name,backend:'llama.cpp',value:name})),
      ...catalog.filter(item=>item.backend==='Ollama')
    ];
    activeModel=active||modelList[0]?.value||'';
    renderModels(modelList.length?modelList:[activeModel].filter(Boolean).map(name=>({name,backend:'llama.cpp',value:name})));
    $('llmStatus').textContent='LLM listo';
    $('serverBadge').textContent='OK';
    $('serverInfo').innerHTML=[
      row('Endpoint','/llm-api/v1/chat/completions'),
      row('Modelo activo',displayModelName(activeModel)||'--'),
      row('Modelos locales',modelList.filter(item=>item.backend==='llama.cpp').length),
      row('Modelos Ollama',modelList.filter(item=>item.backend==='Ollama').length),
      row('Template sugerido',modelTemplate(activeModel||$('modelSelect').value||'')),
      row('Especialista',modelState?.specialist||modelDefaults(activeModel).specialist),
      row('Contexto / GPU layers',modelState?`${modelState.ctx_size} / ${modelState.gpu_layers}`:'--'),
      row('Backend','llama.cpp + Ollama')
    ].join('');
  }catch(error){
    $('llmStatus').textContent='LLM no disponible';
    $('serverBadge').textContent='ERROR';
    $('serverInfo').innerHTML=row('Error',error.message);
  }
  loadGpuControl().catch(error=>{
    gpuState={available:false,error:error.message,containers:[],gpu_enabled:false};
    renderGpuControl();
  });
}

async function switchModel(){
  const selected=$('modelSelect').value;
  if(isOllamaModel(selected)){
    selectedRuntimeModel=selected;
    applyModelDefaults(selected);
    renderLoadButton();
    $('responseBox').textContent=`Modelo Ollama listo: ${displayModelName(selected)}`;
    $('requestState').textContent='LISTO';
    $('llmStatus').textContent='Ollama listo';
    return;
  }
  if(!selected||selected===activeModel||modelBusy)return;
  modelBusy=true;
  $('requestState').textContent='CARGANDO';
  $('serverBadge').textContent='LOAD';
  $('llmStatus').textContent='Cargando modelo';
  $('responseBox').textContent=`Cargando ${selected}...\nEsto puede tardar mientras Docker recrea quantlab_llm y el modelo entra a GPU.`;
  renderLoadButton();
  try{
    modelState=await jsonFetch('/admin/llm-model',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:selected})
    });
    activeModel=modelState.active_model||modelState.selected_model||selected;
    selectedRuntimeModel=activeModel;
    await loadModels();
    $('responseBox').textContent=modelState.readiness?.ready
      ? `Modelo cargado: ${activeModel}`
      : `Modelo solicitado: ${activeModel}\nEl contenedor reinicio, pero aun esta calentando. Espera unos segundos y prueba de nuevo.`;
    $('requestState').textContent=modelState.readiness?.ready?'LISTO':'CARGANDO';
  }catch(error){
    $('responseBox').textContent=`No fue posible cargar el modelo: ${error.message}`;
    $('requestState').textContent='ERROR';
  }finally{
    modelBusy=false;
    renderLoadButton();
  }
}

async function submitPrompt(event){
  event.preventDefault();
  const prompt=$('prompt').value.trim();
  if(!prompt){$('responseBox').textContent='Escribe una consulta antes de enviar.';return;}
  const btn=$('runBtn'), selected=$('modelSelect').value||activeModel;
  const endpoint=isOllamaModel(selected)?'/ollama/v1/chat/completions':'/llm-api/v1/chat/completions';
  const requestModel=isOllamaModel(selected)?displayModelName(selected):selected;
  const started=performance.now();
  lastPrompt=prompt;
  currentController=new AbortController();
  btn.disabled=true;
  $('stopBtn').disabled=false;
  $('requestState').textContent='GENERANDO';
  $('responseBox').textContent='Pensando...';
  $('responseTime').textContent='Tiempo: corriendo...';
  try{
    const payload=await jsonFetch(endpoint,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      signal:currentController.signal,
      body:JSON.stringify({
        model:requestModel||undefined,
        messages:[
          {role:'system',content:systemPromptForModel(selected)},
          {role:'user',content:prompt}
        ],
        max_tokens:Number($('maxTokens').value)||384,
        temperature:Number($('temperature').value)||0.7,
        stream:false
      })
    });
    const text=payload.choices?.[0]?.message?.content||payload.choices?.[0]?.text||payload.content||JSON.stringify(payload,null,2);
    $('responseBox').textContent=text;
    const usage=payload.usage;
    $('tokenUsage').textContent=usage?`${usage.prompt_tokens||0}+${usage.completion_tokens||0} tokens`:'OK';
    $('responseTime').textContent=`Tiempo: ${((performance.now()-started)/1000).toFixed(2)} s`;
    $('requestState').textContent='LISTO';
  }catch(error){
    $('responseBox').textContent=error.name==='AbortError'?'Petición detenida.':`No fue posible consultar el LLM: ${error.message}`;
    $('requestState').textContent='ERROR';
    $('responseTime').textContent=`Tiempo: ${((performance.now()-started)/1000).toFixed(2)} s`;
  }finally{
    btn.disabled=false;
    $('stopBtn').disabled=true;
    currentController=null;
  }
}

function systemPromptForModel(model){
  if(/qwen2\.5-coder/i.test(model||'')){
    return 'Eres codex4u, programador especialista para servidor Ubuntu, Docker, Python, shell scripts, JavaScript, Node.js, HTML y CSS. Responde en español, con pasos verificables y comandos seguros.';
  }
  return 'Eres QuantLabs AI, un asistente privado. Responde claro, directo y en español.';
}

function openServerModal(){ $('serverModal')?.classList.add('open'); $('serverModal')?.setAttribute('aria-hidden','false'); }
function closeServerModal(){ $('serverModal')?.classList.remove('open'); $('serverModal')?.setAttribute('aria-hidden','true'); }

$('promptForm')?.addEventListener('submit',submitPrompt);
$('loadModelBtn')?.addEventListener('click',switchModel);
$('modelSelect')?.addEventListener('change',()=>{applyModelDefaults($('modelSelect').value);renderLoadButton();});
$('stopBtn')?.addEventListener('click',()=>currentController?.abort());
$('retryBtn')?.addEventListener('click',()=>{if(lastPrompt){$('prompt').value=lastPrompt;$('promptForm').requestSubmit();}});
$('copyPromptBtn')?.addEventListener('click',()=>navigator.clipboard?.writeText($('prompt').value||''));
$('editPromptBtn')?.addEventListener('click',()=>$('prompt')?.focus());
document.addEventListener('click',e=>{if(e.target.classList.contains('copy-response'))navigator.clipboard?.writeText($('responseBox').textContent||''); if(e.target.matches('[data-close-server]'))closeServerModal();});
$('openServerModal')?.addEventListener('click',openServerModal);
$('closeServerModal')?.addEventListener('click',closeServerModal);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeServerModal();});
loadModels();
