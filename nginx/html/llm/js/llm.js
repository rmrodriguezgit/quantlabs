const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
let activeModel='', gpuBusy=false, modelBusy=false, gpuState=null, modelState=null;

async function jsonFetch(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
  return data;
}
const row=(label,value)=>`<div class="server-row"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;

function modelTemplate(name){
  if(/mistral-nemo/i.test(name))return 'mistral';
  return 'chatml';
}

async function loadModelCatalog(){
  const payload=await fetch('./models.json?ts='+Date.now(),{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():{models:[]});
  return payload.models||[];
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
  select.innerHTML=catalog.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
  if(activeModel&&catalog.includes(activeModel))select.value=activeModel;
  $('modelName').textContent=activeModel||select.value||'--';
  renderLoadButton();
}

function renderLoadButton(){
  const btn=$('loadModelBtn');
  if(!btn)return;
  const selected=$('modelSelect')?.value||'';
  const changed=Boolean(selected&&selected!==activeModel);
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
    const modelList=(modelState?.models&&modelState.models.length)?modelState.models:catalog;
    activeModel=active||modelList[0]||'';
    renderModels(modelList.length?modelList:[activeModel].filter(Boolean));
    $('llmStatus').textContent='LLM listo';
    $('serverBadge').textContent='OK';
    $('serverInfo').innerHTML=[
      row('Endpoint','/llm-api/v1/chat/completions'),
      row('Modelo activo',activeModel||'--'),
      row('Modelos en carpeta',modelList.length),
      row('Template sugerido',modelTemplate(activeModel||$('modelSelect').value||'')),
      row('Contexto / GPU layers',modelState?`${modelState.ctx_size} / ${modelState.gpu_layers}`:'--'),
      row('Backend','llama.cpp')
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
  btn.disabled=true;
  $('requestState').textContent='GENERANDO';
  $('responseBox').textContent='Pensando...';
  try{
    const payload=await jsonFetch('/llm-api/v1/chat/completions',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        model:selected||undefined,
        messages:[
          {role:'system',content:'Eres QuantLabs AI, un asistente privado. Responde claro, directo y en español.'},
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
    $('requestState').textContent='LISTO';
  }catch(error){
    $('responseBox').textContent=`No fue posible consultar el LLM: ${error.message}`;
    $('requestState').textContent='ERROR';
  }finally{
    btn.disabled=false;
  }
}

$('promptForm')?.addEventListener('submit',submitPrompt);
$('loadModelBtn')?.addEventListener('click',switchModel);
$('modelSelect')?.addEventListener('change',renderLoadButton);
loadModels();
