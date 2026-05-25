const apiBase='/harness-api/v1';
const $=id=>document.getElementById(id);
let txState=[], latestStatus=null, latestLogs={agents:{}}, txSignature='', txLoading=false;
function esc(v=''){return String(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function get(path){const r=await fetch(`${apiBase}${path}`,{credentials:'same-origin',cache:'no-store'});const ct=r.headers.get('content-type')||'';if(!r.ok)throw new Error(`HTTP ${r.status}`);return ct.includes('json')?r.json():r.text()}
function kpi(label,value,klass=''){return `<div class="kpi ${klass}"><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`}
function compactTime(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?value:d.toLocaleString('es-MX',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function nowEt(){return new Date().toLocaleString('es-MX',{timeZone:'America/New_York',hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' ET'}
function marketInterval(tx){const raw=String(tx.interval||tx.indicators?.candidate?.interval||'').toLowerCase();return raw.includes('15')?'15m':raw.includes('5')?'5m':raw||'—'}
function marketSymbol(tx){const interval=marketInterval(tx);return interval==='—'?'BTC-UP-DOWN':`BTC-UP-DOWN_${interval}`}
function marketWindowEt(tx){return tx.window||tx.indicators?.candidate?.window_et||''}
function compactEtWindow(value){if(!value)return nowEt();const parts=String(value).split(' - ');const fmt=p=>{const m=String(p||'').match(/(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?\s+(E[DS]T)/);return m?`${m[1]} ${m[2]} ${m[3]}`:p};return parts.length>1?`${fmt(parts[0])} - ${fmt(parts[1]).replace(/^\d{4}-\d{2}-\d{2}\s+/,'')}`:fmt(value)}
function txTime(tx){return String(tx.venue||'').toLowerCase()==='polymarket'?compactEtWindow(marketWindowEt(tx)):compactTime(tx.timestamp)}
function priceToBeat(tx){return tx.indicators?.price_to_beat_reference??tx.indicators?.candidate?.price_to_beat_reference??tx.price_to_beat_reference??tx.price}
function predictedPrice(tx){return tx.indicators?.forecast_price_at_close??tx.indicators?.candidate?.forecast_price_at_close??tx.forecast_price_at_close}
function closePrice(tx){return tx.indicators?.final_price_reference??tx.indicators?.candidate?.final_price_reference??tx.final_price_reference}
function money(value){const n=Number(value);return Number.isFinite(n)&&n!==0?n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}):'—'}
function confidence(tx){return Number(tx.confidence||tx.probability||0)}
function paperPnl(tx){const pnl=Number(tx.pnl||0);const suffix=tx.mode==='paper'?' paper':'';return `${pnl.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}${suffix}`}
function outcomeLabel(tx){const status=String(tx.status||'').toLowerCase(), side=String(tx.side||'NONE').toUpperCase(), actual=String(tx.indicators?.winning_side||tx.indicators?.actual_close_side||'').toUpperCase();if(actual){const klass=actual==='UP'?'close-up':'close-down';if(side==='NONE')return {label:`Cerró ${actual} · Sin trade`,klass};return side===actual?{label:`Cerró ${actual} · Acierto`,klass}:{label:`Cerró ${actual} · Error`,klass}}if(status==='won')return {label:'Acierto',klass:'ok'};if(status==='lost')return {label:'Error',klass:'bad'};if(status==='no_trade'||side==='NONE')return {label:'Pendiente / Sin trade',klass:'pending'};if(tx.pnl>0)return {label:'Acierto',klass:'ok'};if(tx.pnl<0)return {label:'Error',klass:'bad'};return {label:'Pendiente',klass:'pending'}}
function tradeReason(tx){const cand=tx.indicators?.candidate||{};const labels={confidence_below_threshold:'Confianza < 80%',edge_too_small:'Edge/Kelly insuficiente',missing_ask:'Sin ask en book',spread_too_wide:'Spread alto',insufficient_ask_depth:'Profundidad baja',too_close_to_close:'Cierre muy cerca',missing_side:'Sin direccion',duplicate_window_trade:'Trade ya registrado',coordinator_blocked:'Sin evento valido',no_event_passed_filters:'Sin evento valido',kelly_or_stake_zero:'Kelly/stake en cero'};const reasons=cand.reasons||[];if(reasons.length)return reasons.map(r=>labels[r]||r).join(' · ');const risk=tx.risk||'—';return labels[risk]||risk}
function normalizeSide(tx){const side=String(tx.side||tx.signal||'NONE').toUpperCase();return ['UP','DOWN','NONE'].includes(side)?side:'NONE'}
function render(payload,logs){
 const s=payload.summary||{}, infra=payload.infrastructure||{}, agents=payload.agents||[], alerts=payload.alerts||[];
 $('generatedAt').textContent=payload.generated_at||'—';
 $('kpis').innerHTML=[kpi('Health',s.health_score??100),kpi('Agentes',s.agents_total??0),kpi('Activos',s.agents_active??0),kpi('Errores',s.agents_error??0),kpi('PnL paper',`${Number(s.total_pnl||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} USDT`),kpi('Live',s.live??0)].join('');
 $('agents').innerHTML=agents.map(a=>`<section class="agent-card mode-${esc(a.mode)} status-${esc(a.status)}"><header><div><small>${esc(a.market||'strategy')}</small><h3>${esc(a.name||a.agent)}</h3></div><div class="health">${esc(a.health_score??0)}</div></header><div class="agent-meta"><span><b>Mode</b>${esc(String(a.mode||'').toUpperCase())}</span><span><b>Status</b><em class="badge ${esc(a.status)}">${esc(a.status)}</em></span><span><b>Uptime</b>${esc(a.uptime||'n/d')}</span><span><b>Señal</b>${esc(a.signal||a.prediction||'NONE')}</span><span><b>Confianza</b>${Number(a.confidence||0).toFixed(1)}%</span><span><b>Órdenes</b>${Number(a.orders||0).toLocaleString()}</span><span><b>Accuracy</b>${Number(a.accuracy||0).toFixed(1)}%</span><span><b>Sharpe</b>${Number(a.sharpe||0).toFixed(2)}</span><span><b>Drawdown</b>${Number(a.max_drawdown||0).toFixed(2)}%</span></div><p>${esc(a.last_event||a.health||'Sin evento reciente')}</p></section>`).join('')||'<p>Sin agentes registrados.</p>';
 $('alerts').innerHTML=(alerts.length?alerts:['Sin alertas críticas.']).map(a=>`<li>${esc(a)}</li>`).join('');
 const gpu=(infra.gpu||[])[0]||{};
 $('infra').innerHTML=[['CPU',`${infra.cpu_percent||0}%`],['RAM',`${infra.ram_percent||0}% · ${infra.ram_available_gb||0} GB libres`],['GPU',gpu.name?`${gpu.name} · ${gpu.memory_percent}% VRAM`:'N/D'],['Docker',(infra.docker||[]).length+' contenedores'],['API latency',`${infra.api_latency_ms??0} ms`]].map(x=>`<div class="infra-row"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
 const d=payload.discovery||{};
 $('discovery').innerHTML=[['CRON detectados',(d.crons||[]).length],['Procesos relevantes',(d.processes||[]).length],['Docker containers',(d.docker||[]).length],['GPU devices',(d.gpu||[]).length]].map(x=>`<div class="discovery-row"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
 const logText=Object.entries((logs||{}).agents||{}).map(([name,lines])=>`[${name}]\n${(lines||[]).slice(-30).join('\n')}`).join('\n\n')||'Sin logs registrados.';
 $('logs').textContent=logText;
 if(!txState.length){renderTransactionFilters();renderTransactions({})}
}
function optionSet(rows,key){return [...new Set(rows.map(x=>key==='symbol'?marketSymbol(x):x[key]).filter(Boolean))].sort()}
function fillSelect(id,values,label){const el=$(id);const current=el.value;el.innerHTML=`<option value="">${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');el.value=values.includes(current)?current:''}
function renderTransactionFilters(){const agents=optionSet(txState,'agent');const agentFilter=$('agentFilter');agentFilter.closest('label')?.classList.toggle('hidden-filter',agents.length<=1);fillSelect('agentFilter',agents,'Todos los agentes');fillSelect('modeFilter',optionSet(txState,'mode'),'Todos los modos');fillSelect('sideFilter',['UP','DOWN','NONE'],'Todos los sides')}
function renderTransactions(payload={}){
 const agent=$('agentFilter').value, mode=$('modeFilter').value, side=$('sideFilter').value;
 const rows=txState.filter(tx=>(!agent||tx.agent===agent)&&(!mode||tx.mode===mode)&&(!side||normalizeSide(tx)===side));
 const exposure=rows.reduce((a,b)=>a+Number(b.stake_usdt||0),0), pnl=rows.reduce((a,b)=>a+Number(b.pnl||0),0), agents=optionSet(rows,'agent'), showAgent=agents.length>1;
 const latestEt=rows[0]?txTime(rows[0]):nowEt();
 $('txSummary').innerHTML=[['Transacciones',rows.length],['Exposure paper',`${exposure.toLocaleString()} USDT`],['PnL paper',`${pnl.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} USDT`],['Fuente',latestEt]].map(x=>`<div><small>${esc(x[0])}</small><strong>${esc(x[1])}</strong></div>`).join('');
 const agentHead=showAgent?'<th>Agente</th>':'';
 $('txHead').innerHTML=`<tr><th>Hora ET</th>${agentHead}<th>Venue</th><th>Mercado</th><th>Side</th><th>Mode</th><th>Status</th><th>Precio a superar</th><th>Precio predicho</th><th>Precio cierre</th><th>Stake</th><th>Confianza</th><th>Motivo</th><th>Acierto / Error</th><th>PnL paper</th></tr>`;
 $('transactions').innerHTML=rows.slice(0,160).map(tx=>{const side=normalizeSide(tx), outcome=outcomeLabel(tx), agentCell=showAgent?`<td>${esc(tx.agent)}</td>`:'';return `<tr class="mode-${esc(tx.mode)}"><td>${esc(txTime(tx))}</td>${agentCell}<td><span class="venue-pill">Polymarket</span></td><td>${esc(marketSymbol(tx))}</td><td><span class="side side-${esc(side.toLowerCase())}">${esc(side)}</span></td><td>${esc(tx.mode)}</td><td><span class="status-pill status-${esc(String(tx.status||'').toLowerCase())}">${esc(tx.status)}</span></td><td>${money(priceToBeat(tx))}</td><td>${money(predictedPrice(tx))}</td><td>${money(closePrice(tx))}</td><td>${Number(tx.stake_usdt||0).toLocaleString()}</td><td>${confidence(tx).toFixed(1)}%</td><td>${esc(tradeReason(tx))}</td><td><span class="outcome outcome-${outcome.klass}">${esc(outcome.label)}</span></td><td>${esc(paperPnl(tx))}</td></tr>`}).join('')||'<tr><td colspan="15">Sin transacciones Polymarket registradas.</td></tr>';
}
function txPayloadSignature(payload){return JSON.stringify(((payload||{}).transactions||[]).filter(tx=>String(tx.venue||'').toLowerCase()==='polymarket').map(tx=>[tx.timestamp,tx.window,tx.interval,tx.side,tx.status,tx.pnl,tx.stake_usdt,tx.risk,tx.indicators?.winning_side,tx.indicators?.final_price_reference,tx.indicators?.forecast_price_at_close,tx.risk]))}
function renderTransactionPayload(payload={},force=false){const next=txPayloadSignature(payload);if(!force&&next===txSignature)return;txSignature=next;txState=((payload||{}).transactions||[]).filter(tx=>String(tx.venue||'').toLowerCase()==='polymarket');renderTransactionFilters();renderTransactions(payload)}
async function refresh(){
 try{
  latestStatus=await get('/agents/status');
  try{latestLogs=await get('/agents/logs?limit=60')}catch(e){latestLogs={agents:{validation:[`Logs no disponibles: ${e.message}`]}}}
  render(latestStatus,latestLogs);
  refreshTransactions(true);
 }catch(e){
  const msg=`No fue posible cargar validation: ${esc(e.message)}`;
  ['generatedAt','logs'].forEach(id=>{const el=$(id);if(el)el.textContent=msg});
  const alerts=$('alerts');if(alerts)alerts.innerHTML=`<li>${msg}</li>`;
  const kpis=$('kpis');if(kpis)kpis.innerHTML=[kpi('Health','N/D'),kpi('Agentes','N/D'),kpi('Activos','N/D'),kpi('Errores','N/D'),kpi('PnL paper','N/D'),kpi('Live','N/D')].join('');
 }
}
async function refreshTransactions(force=false){
 if(txLoading&& !force)return;
 if(txLoading&&force)txLoading=false;
 txLoading=true;
 try{renderTransactionPayload(await get('/agents/transactions?limit=40'),force)}
 catch(e){$('alerts')?.insertAdjacentHTML('afterbegin',`<li>Transacciones no disponibles: ${esc(e.message)}</li>`)}
 finally{txLoading=false}
}
function msToNextFiveMinuteWindow(){const now=new Date();const elapsed=(now.getMinutes()%5)*60000+now.getSeconds()*1000+now.getMilliseconds();return Math.max(1000,300000-elapsed+1200)}
function scheduleWindowRefresh(){setTimeout(()=>{refreshTransactions(true);scheduleWindowRefresh()},msToNextFiveMinuteWindow())}
['agentFilter','modeFilter','sideFilter'].forEach(id=>document.addEventListener('change',e=>{if(e.target?.id===id)renderTransactions()}));
function manualRefresh(){const btn=$('refreshBtn');if(btn){btn.disabled=true;btn.textContent='Actualizando'}Promise.resolve(refresh()).finally(()=>setTimeout(()=>{if(btn){btn.disabled=false;btn.textContent='Actualizar'}},600))}
$('refreshBtn')?.addEventListener('click',manualRefresh);
refresh();setInterval(refresh,30000);setInterval(()=>refreshTransactions(false),5000);scheduleWindowRefresh();
