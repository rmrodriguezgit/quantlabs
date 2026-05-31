const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
let activeModel='', gpuBusy=false, gpuState=null;

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
  const payload=await jsonFetch('/llm-api/v1/models');
  const models=payload.data||payload.models||[];
  return models[0]?.id||models[0]?.name||models[0]?.model||'';
}

function renderModels(catalog){
  const select=$('modelSelect');
  select.innerHTML=catalog.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('');
  if(activeModel&&catalog.includes(activeModel))select.value=activeModel;
  $('modelName').textContent=activeModel||select.value||'--';
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
    activeModel=active||catalog[0]||'';
    renderModels(catalog.length?catalog:[activeModel].filter(Boolean));
    $('llmStatus').textContent='LLM listo';
    $('serverBadge').textContent='OK';
    $('serverInfo').innerHTML=[
      row('Endpoint','/llm-api/v1/chat/completions'),
      row('Modelo activo',activeModel||'--'),
      row('Modelos en carpeta',catalog.length),
      row('Template sugerido',modelTemplate(activeModel||$('modelSelect').value||'')),
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
loadModels();
