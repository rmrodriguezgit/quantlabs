const apiBase = '/harness-api/v1';
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const money = n => Number(n || 0).toLocaleString('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2});
const num = (v, d = 0) => Number.isFinite(Number(v)) ? Number(v) : d;
const pct = v => `${num(v).toFixed(1)}%`;
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
const boundedPct = (v, fallback, min, max) => {
  const n = num(v, fallback);
  return n < min ? fallback : clamp(n, min, max);
};
const boundedNumber = (v, fallback, min, max, decimals = 2) => {
  const n = num(v, fallback);
  const safe = n < min ? fallback : clamp(n, min, max);
  const factor = 10 ** decimals;
  return Math.round(safe * factor) / factor;
};
const boundedInt = (v, fallback, min, max) => Math.round(boundedNumber(v, fallback, min, max, 0));
const stakeValue = v => {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0.1 ? Math.round(clamp(n, 0.1, 100) * 100) / 100 : 1;
};

let controlState = {
  enabled:false,
  mode:'observe',
  stake:1,
  autoLiquidate:true,
  timeStop:75,
  tp:100,
  strategyProfile:'adaptive_5m15m',
  threshold:0.8,
  minEdge:0.03,
  maxSpread:0.08,
  minAskSize:1,
  minSecondsToClose:45,
  invertPrediction:false,
  liveExecution:false,
  serverLive:false,
  liveBlocked:false,
  lastError:''
};
let controlDirty = false;
let controlsBound = false;
let openPositionByAsset = new Map();
let resolvedPositionByAsset = new Map();
let openPositionsLoaded = false;

async function api(path, opts = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    credentials:'same-origin',
    cache:'no-store',
    headers:{'content-type':'application/json', ...(opts.headers || {})},
    ...opts
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
const get = path => api(path);
const patch = (path, body) => api(path, {method:'PATCH', body:JSON.stringify(body)});
const post = (path, body) => api(path, {method:'POST', body:JSON.stringify(body)});

function compactTime(value) {
  if (!value) return '--';
  const d = new Date(value);
  return Number.isNaN(d.valueOf()) ? String(value) : d.toLocaleTimeString('es-MX', {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'});
}
function etClock() {
  return `${new Date().toLocaleTimeString('es-MX', {timeZone:'America/New_York', hour12:false})} ET`;
}
function sideOf(row) {
  return String(row?.side || row?.signal || row?.preferred_side || 'NONE').toUpperCase();
}
function intervalOf(row) {
  const raw = String(row?.interval || row?.indicators?.candidate?.interval || '').toLowerCase();
  return raw.includes('15') ? '15m' : raw.includes('5') ? '5m' : (raw || '--');
}
function statusOf(row) {
  return String(row?.status || row?.transaction_status || row?.execution || 'pending').toLowerCase();
}
function labelForStatus(value) {
  const st = String(value || 'pending').toLowerCase();
  const labels = {
    accepted:'aceptada',
    open:'abierta',
    filled:'abierta',
    won:'ganada',
    lost:'perdida',
    rejected:'rechazada',
    no_trade:'sin trade',
    liquidated:'liquidada',
    closed:'cerrada',
    claimed:'cobrada',
    redeemed:'cobrada',
    resuelto:'resuelta',
    liquidado:'liquidada',
    sin_posicion:'sin posicion'
  };
  return labels[st] || st.replaceAll('_', ' ');
}
function asConfidence(row) {
  let v = num(row?.probability ?? row?.confidence ?? row?.indicators?.candidate?.probability ?? row?.indicators?.candidate?.confidence, 0);
  if (v <= 1) v *= 100;
  return clamp(v, 0, 100);
}
function parseEt(value) {
  if (!value) return null;
  const cleaned = String(value).trim().replace(' EDT', '-04:00').replace(' EST', '-05:00').replace(' ', 'T');
  const d = new Date(cleaned);
  return Number.isNaN(d.valueOf()) ? null : d;
}
function windowBounds(row) {
  const value = row?.window || row?.window_et || row?.indicators?.candidate?.window_et || '';
  const parts = String(value).split(' - ');
  if (parts.length !== 2) return {raw:value, start:null, end:null};
  return {raw:value, start:parseEt(parts[0]), end:parseEt(parts[1])};
}
function windowProgress(row) {
  const {start, end} = windowBounds(row);
  if (!start || !end || end <= start) return null;
  return clamp(((Date.now() - start.getTime()) / (end.getTime() - start.getTime())) * 100, 0, 100);
}
function isWindowExpired(row) {
  const {end} = windowBounds(row);
  return !!end && Date.now() > end.getTime();
}
function hasLiveSide(row) {
  return ['UP', 'DOWN'].includes(sideOf(row));
}
function isRejected(row) {
  const st = statusOf(row);
  return st === 'rejected' || st === 'error' || st.includes('failed') || st.includes('blocked');
}
function isResolved(row) {
  const st = statusOf(row);
  return ['won', 'lost', 'closed', 'liquidated', 'claimed', 'redeemed'].includes(st);
}
function assetOf(row) {
  const c = (row?.indicators || {}).candidate || {};
  const m = c.microstructure || {};
  return String(row?.token_id || row?.asset || m.token_id || '').trim();
}
function positionFor(row) {
  const asset = assetOf(row);
  return asset ? openPositionByAsset.get(asset) : null;
}
function resolvedPositionFor(row) {
  const asset = assetOf(row);
  return asset ? resolvedPositionByAsset.get(asset) : null;
}
function hasOpenPosition(row) {
  const position = positionFor(row);
  return !!position && num(position.size, 0) > 0;
}
function isOpenTrade(row) {
  return hasLiveSide(row) && ['accepted', 'open', 'filled'].includes(statusOf(row)) && !isWindowExpired(row) && hasOpenPosition(row);
}
function displayStatus(row) {
  if (isOpenTrade(row)) return 'abierta';
  if (isResolved(row)) return statusOf(row);
  if (isRejected(row)) return 'rechazada';
  if (hasLiveSide(row) && resolvedPositionFor(row)) return 'resuelto';
  if (hasLiveSide(row) && isWindowExpired(row)) return 'liquidado';
  if (hasLiveSide(row) && openPositionsLoaded && !hasOpenPosition(row)) return 'sin_posicion';
  return statusOf(row);
}
function statusLabel(auto) {
  if (!auto.enabled || auto.status === 'stopped') return 'DETENIDO';
  const mode = String(auto.mode || 'observe').toLowerCase();
  const liveExecution = auto.live_execution_enabled === true;
  if (auto.live_blocked || auto.status === 'blocked' || (mode === 'live' && !liveExecution)) return 'LIVE BLOQUEADO';
  if (auto.status === 'error' || (auto.errors || []).length) return `${mode.toUpperCase()} ERROR`;
  if (mode === 'live' && liveExecution) return 'LIVE ARMADO';
  if (auto.status === 'stale') return `${mode.toUpperCase()} SIN CICLO`;
  return `${mode.toUpperCase()} ${String(auto.status || 'sync').toUpperCase()}`;
}

function setSaveState(text) {
  const badge = $('controlSaveState');
  badge.textContent = text;
  const upper = String(text || '').toUpperCase();
  badge.className = controlDirty ? 'dirty-state' : (upper.includes('ERROR') || upper.includes('BLOQUEADO') ? 'error-state' : (upper.includes('LISTO') || upper.includes('OK') || upper.includes('ARMADO') ? 'saved-state' : ''));
  $('saveControls').disabled = !controlDirty;
}
function liveGateHint() {
  if (controlState.mode !== 'live') return ' LIVE real solo aplica en modo Live.';
  if (!controlState.serverLive) return ' Servidor LIVE OFF: falta POLYMARKET_LIVE_TRADING_ENABLED.';
  if (!controlState.liveExecution) return ' LIVE real OFF: falta encender y guardar Ejecutar LIVE real.';
  return ' LIVE real ARMADO: ambos candados estan encendidos y guardados.';
}
function friendlyError(value = '') {
  const text = String(value || '');
  if (text.includes('no orders found to match')) return 'Ultima orden rechazada: no hubo liquidez FAK al precio limite.';
  if (text.includes('orderbook') && text.includes('does not exist')) return 'Ultima orden rechazada: el order book ya no existia para esa ventana.';
  return text ? `Ultimo error: ${text.slice(0, 160)}` : '';
}
function syncControlSummary() {
  const invert = '';
  const err = controlState.lastError ? ` ${friendlyError(controlState.lastError)}` : '';
  const profile = controlState.strategyProfile === 'adaptive_5m15m' ? 'Adaptivo' : 'Legacy';
  $('rulesNote').textContent = `${profile}: confianza ${controlState.threshold.toFixed(2)}, edge ${controlState.minEdge.toFixed(2)}, spread ${controlState.maxSpread.toFixed(2)}, profundidad ${controlState.minAskSize}, cierre ${controlState.minSecondsToClose}s. SL ${controlState.timeStop}% ventana. TP +${controlState.tp}%.${invert}${liveGateHint()}${err}`;
}
function applyLiveGateAvailability() {
  const liveToggle = $('liveExecutionEnabled');
  const canUse = controlState.mode === 'live' && controlState.serverLive;
  liveToggle.disabled = !canUse;
  if (!canUse) {
    liveToggle.checked = false;
    controlState.liveExecution = false;
  }
  document.querySelector('.live-gate')?.classList.toggle('disabled', !canUse);
}
function readControls() {
  return {
    enabled:$('tradingEnabled').checked,
    mode:$('tradingMode').value,
    stake:stakeValue($('customStake').value || controlState.stake),
    strategyProfile:$('strategyProfile').value,
    threshold:boundedNumber($('threshold').value, 0.8, 0.5, 0.99, 2),
    minEdge:boundedNumber($('minEdge').value, 0.03, 0, 0.5, 2),
    maxSpread:boundedNumber($('maxSpread').value, 0.08, 0, 0.5, 2),
    minAskSize:boundedNumber($('minAskSize').value, 1, 0, 10000, 2),
    minSecondsToClose:boundedInt($('minSecondsToClose').value, 45, 0, 600),
    autoLiquidate:$('autoLiquidate').checked,
    timeStop:boundedPct($('timeStopPct').value, 75, 10, 99),
    tp:boundedPct($('takeProfitPct').value, 100, 10, 500),
    invertPrediction:$('invertPrediction').checked,
    liveExecution:$('tradingMode').value === 'live' && $('liveExecutionEnabled').checked
  };
}
function markControlsDirty() {
  const current = readControls();
  controlState = {...controlState, ...current, liveBlocked:false};
  $('timeStopPct').value = controlState.timeStop;
  $('takeProfitPct').value = controlState.tp;
  $('threshold').value = controlState.threshold;
  $('minEdge').value = controlState.minEdge;
  $('maxSpread').value = controlState.maxSpread;
  $('minAskSize').value = controlState.minAskSize;
  $('minSecondsToClose').value = controlState.minSecondsToClose;
  applyLiveGateAvailability();
  syncStakeButtons();
  syncControlSummary();
  controlDirty = true;
  setSaveState('SIN GUARDAR');
}
function syncStakeButtons() {
  const presetActive = [1, 2, 3].includes(Number(controlState.stake));
  [...$('stakeGroup').querySelectorAll('button')].forEach(b => b.classList.toggle('active', Number(b.dataset.stake) === Number(controlState.stake)));
  const custom = $('customStake');
  if (custom && document.activeElement !== custom) custom.value = presetActive ? '' : controlState.stake;
  custom?.parentElement?.classList.toggle('active', !presetActive);
}

function syncControls(auto, rules = {}) {
  if (controlDirty) return;
  const latestError = ((auto.errors || [])[0] || {}).error || '';
  controlState = {
    enabled:!!auto.enabled,
    mode:auto.mode || rules.mode || 'observe',
    stake:num(auto.polymarket_stake_usdt ?? rules.polymarket_stake_usdt, 1),
    strategyProfile:auto.polymarket_strategy_profile ?? rules.polymarket_strategy_profile ?? 'adaptive_5m15m',
    threshold:boundedNumber(auto.threshold ?? rules.threshold, 0.8, 0.5, 0.99, 2),
    minEdge:boundedNumber(auto.polymarket_min_edge ?? rules.polymarket_min_edge, 0.03, 0, 0.5, 2),
    maxSpread:boundedNumber(auto.polymarket_max_spread ?? rules.polymarket_max_spread, 0.08, 0, 0.5, 2),
    minAskSize:boundedNumber(auto.polymarket_min_ask_size ?? rules.polymarket_min_ask_size, 1, 0, 10000, 2),
    minSecondsToClose:boundedInt(auto.polymarket_min_seconds_to_close ?? rules.polymarket_min_seconds_to_close, 45, 0, 600),
    autoLiquidate:auto.polymarket_auto_liquidate_enabled ?? rules.polymarket_auto_liquidate_enabled ?? true,
    timeStop:boundedPct(auto.polymarket_time_stop_pct ?? rules.polymarket_time_stop_pct, 75, 10, 99),
    tp:boundedPct(auto.polymarket_take_profit_pct ?? rules.polymarket_take_profit_pct, 100, 10, 500),
    invertPrediction:!!(auto.polymarket_invert_prediction_enabled ?? rules.polymarket_invert_prediction_enabled),
    liveExecution:!!(auto.live_execution_enabled ?? rules.live_execution_enabled),
    serverLive:!!(auto.server_live_trading_enabled ?? rules.server_live_trading_enabled),
    liveBlocked:!!(auto.live_blocked || auto.status === 'blocked' || ((auto.mode || rules.mode) === 'live' && !(auto.live_execution_enabled ?? rules.live_execution_enabled))),
    lastError:latestError
  };
  $('tradingEnabled').checked = controlState.enabled;
  $('tradingMode').value = controlState.mode;
  $('strategyProfile').value = controlState.strategyProfile;
  $('threshold').value = controlState.threshold;
  $('minEdge').value = controlState.minEdge;
  $('maxSpread').value = controlState.maxSpread;
  $('minAskSize').value = controlState.minAskSize;
  $('minSecondsToClose').value = controlState.minSecondsToClose;
  $('autoLiquidate').checked = controlState.autoLiquidate;
  $('timeStopPct').value = controlState.timeStop;
  $('takeProfitPct').value = controlState.tp;
  $('invertPrediction').checked = controlState.invertPrediction;
  $('liveExecutionEnabled').checked = controlState.liveExecution;
  syncStakeButtons();
  applyLiveGateAvailability();
  setSaveState(statusLabel(auto));
  syncControlSummary();
}
function controlPayload() {
  const current = readControls();
  return {
    enabled:current.enabled,
    mode:current.mode,
    live_execution_enabled:current.liveExecution,
    polymarket_stake_usdt:current.stake,
    polymarket_strategy_profile:current.strategyProfile,
    threshold:current.threshold,
    polymarket_min_edge:current.minEdge,
    polymarket_max_spread:current.maxSpread,
    polymarket_min_ask_size:current.minAskSize,
    polymarket_min_seconds_to_close:current.minSecondsToClose,
    polymarket_auto_liquidate_enabled:current.autoLiquidate,
    polymarket_time_stop_pct:current.timeStop,
    polymarket_take_profit_pct:current.tp,
    polymarket_invert_prediction_enabled:current.invertPrediction
  };
}
function bindControls() {
  if (controlsBound) return;
  controlsBound = true;
  $('stakeGroup').addEventListener('click', e => {
    const b = e.target.closest('button[data-stake]');
    if (!b) return;
    controlState.stake = Number(b.dataset.stake);
    $('customStake').value = '';
    markControlsDirty();
  });
  ['tradingEnabled','tradingMode','liveExecutionEnabled','strategyProfile','threshold','minEdge','maxSpread','minAskSize','minSecondsToClose','autoLiquidate','timeStopPct','takeProfitPct','invertPrediction','customStake'].forEach(id => {
    const node = $(id);
    node.addEventListener('change', markControlsDirty);
    node.addEventListener('input', markControlsDirty);
  });
  $('saveControls').disabled = true;
  $('saveControls').addEventListener('click', async () => {
    const body = controlPayload();
    if (body.mode === 'live' && body.enabled && body.live_execution_enabled && !confirm('Vas a activar ejecucion LIVE real para Polymarket. Esto puede enviar ordenes reales. ¿Confirmas?')) return;
    $('saveControls').disabled = true;
    $('controlSaveState').className = 'saving-state';
    $('controlSaveState').textContent = 'GUARDANDO';
    try {
      const payload = await patch('/automations/paper-trading', body);
      controlDirty = false;
      syncControls(payload.automation || {}, payload.rules || {});
      $('controlSaveState').className = 'saved-state';
      $('controlSaveState').textContent = 'GUARDADO';
      setTimeout(refresh, 400);
    } catch (e) {
      controlDirty = true;
      $('saveControls').disabled = false;
      $('controlSaveState').className = 'error-state';
      $('controlSaveState').textContent = 'ERROR';
      alert(`No se pudo guardar: ${e.message}`);
    }
  });
}

function getCandidateRows(auto) {
  const source = [...(auto?.orders || []), ...(auto?.observations || [])];
  const rows = [];
  for (const item of source) {
    if (item?.candidate) rows.push({...item.candidate, _order:item});
    for (const c of (item?.candidates || item?.indicators?.candidates || [])) {
      if (c && typeof c === 'object') rows.push({...c, _order:item});
    }
  }
  return rows.filter(r => r.interval || r._order?.interval);
}
function hasFreshAutomationCycle(auto) {
  const age = num(auto?.last_age_seconds, 999999);
  const status = String(auto?.status || '').toLowerCase();
  return auto?.enabled !== false && status !== 'stopped' && age <= 300;
}
function latestByInterval(auto) {
  if (!hasFreshAutomationCycle(auto)) {
    return ['5m','15m'].map(key => ({
      interval:key,
      preferred_side:'NONE',
      confidence:0,
      probability:0,
      passes_filters:false,
      reason:'Automatizacion detenida o sin ciclo vigente',
      window_et:''
    }));
  }
  const map = {};
  for (const c of getCandidateRows(auto)) {
    const key = intervalOf(c);
    if (key === '5m' || key === '15m') map[key] = c;
  }
  return ['5m','15m'].map(key => map[key] || {interval:key, preferred_side:'NONE', confidence:0, probability:0, passes_filters:false, reason:'Sin lectura vigente del ultimo ciclo', window_et:''});
}
function windowCard(row) {
  const order = row._order || {};
  const micro = row.microstructure || order.microstructure || order.indicators?.candidate?.microstructure || {};
  const conf = asConfidence(row) || asConfidence(order);
  const side = String(row.preferred_side || sideOf(order) || 'NONE');
  const sideClass = side.toLowerCase().includes('up') ? 'up' : (side.toLowerCase().includes('down') ? 'down' : 'none');
  const edgeRaw = row.edge ?? order.edge ?? order.indicators?.candidate?.edge;
  const edge = Number.isFinite(Number(edgeRaw)) ? Number(edgeRaw) * 100 : null;
  const ask = num(micro.ask ?? order.price, 0);
  const spread = Number.isFinite(Number(micro.spread)) ? Number(micro.spread) * 100 : null;
  const status = row.passes_filters ? 'trade' : 'no-trade';
  const hasSignal = Boolean(row.window_et || order.window_et || order.window || row.reason || order.reason);
  const reason = (row.reasons && row.reasons.join(', ')) || order.reason || row.reason || order.risk || (row.passes_filters ? 'Pasa filtros de confianza, edge y microestructura' : 'No cumple filtros del ciclo');
  const model = row.model || order.indicators?.candidate?.model || order.model || '';
  const comps = row.model_components || order.indicators?.candidate?.model_components || {};
  const profile = row.strategy_profile || order.strategy_profile || order.indicators?.strategy_profile || order.indicators?.candidate?.strategy_profile || controlState.strategyProfile;
  const modelLine = [profile ? `Perfil ${profile}` : '', model ? `Modelo ${model}${comps.technical_weight != null ? ` · tecnico ${(Number(comps.technical_weight) * 100).toFixed(0)}%` : ''}` : ''].filter(Boolean).join(' · ');
  const progress = windowProgress(row) ?? 0;
  const fill = clamp(conf, 8, 100);
  return `<article class="window-card ${status}">
    <div class="window-top"><div><div class="window-title">${esc(row.interval || '--')}</div><small class="muted">${esc(row.window_et || order.window_et || order.window || 'Ventana actual')}</small></div><span class="window-side ${sideClass}">${esc(side)}</span></div>
    <div class="confidence-ring" style="--value:${fill}"><span>${pct(conf)}</span></div>
    <div class="window-stats">
      <div class="stat-box"><small>Costo Ask</small><strong>${ask ? ask.toFixed(2) : '--'}</strong></div>
      <div class="stat-box"><small>Edge p-ask</small><strong>${edge == null ? '--' : `${edge.toFixed(2)}%`}</strong></div>
      <div class="stat-box"><small>Spread</small><strong>${spread == null ? '--' : `${spread.toFixed(2)}%`}</strong></div>
    </div>
    <div class="window-stats">
      <div class="stat-box"><small>Senal modelo</small><strong>${hasSignal ? (row.passes_filters ? 'TRADE' : 'NO TRADE') : 'SIN DATO'}</strong></div>
      <div class="stat-box"><small>Ventana</small><strong>${pct(progress)}</strong></div>
      <div class="stat-box"><small>Posicion real</small><strong>${(openPositionByAsset.size || 0)} abierta(s)</strong></div>
    </div>
    <div class="countdown"><span style="width:${progress || fill}%"></span></div>
    <div class="window-reason">${modelLine ? `<small>${esc(modelLine)}</small><br>` : ''}${esc(reason)}</div>
  </article>`;
}
function renderWindows(auto, txs) {
  const rows = latestByInterval(auto);
  $('windowGrid').innerHTML = rows.map(windowCard).join('');
  $('watchingBadge').textContent = `${rows.filter(r => r.passes_filters).length}/2 senales`;
}

function realizedPnlRows(txs) {
  return txs.filter(tx => ['won', 'lost'].includes(statusOf(tx)) && num(tx.pnl, 0) !== 0);
}
function renderTop(auto, txs) {
  const orders = txs.filter(hasLiveSide);
  const open = (auto.polymarket_open_positions || []).length;
  const won = orders.filter(tx => statusOf(tx) === 'won').length;
  const lost = orders.filter(tx => statusOf(tx) === 'lost').length;
  const rejected = orders.filter(isRejected).length;
  const guards = [...(auto.position_actions || []), ...(auto.claim_actions || [])];
  const pnl = realizedPnlRows(orders).reduce((sum, tx) => sum + num(tx.pnl, 0), 0) + guards.reduce((sum, a) => sum + num(a.cash_pnl, 0), 0);
  const label = statusLabel(auto);
  $('modeChip').textContent = label;
  $('engineMode').textContent = label;
  $('lastRun').textContent = compactTime(auto.last_run_at);
  $('clockEt').textContent = etClock();
  $('topStatus').textContent = label;
  $('netPnl').textContent = money(pnl);
  $('pnlTotal').textContent = money(pnl);
  $('openCount').textContent = open;
  $('winRate').textContent = won + lost ? pct((won / (won + lost)) * 100) : 'N/D';
  $('rejectedCount').textContent = rejected;
  $('guardCount').textContent = guards.length;
  $('pnlCaption').textContent = `${orders.length} operaciones auditadas · ${open} posiciones abiertas en Polymarket`;
}
function orderTitle(tx) {
  return `${intervalOf(tx)} · ${sideOf(tx)} · ${tx.market || 'BTC Up/Down'}`;
}
function positionInterval(position) {
  const slug = String(position?.event_slug || '').toLowerCase();
  return slug.includes('15m') ? '15m' : slug.includes('5m') ? '5m' : '--';
}
function positionTitle(position) {
  return `${positionInterval(position)} · ${String(position?.outcome || '--').toUpperCase()} · ${position?.market || 'BTC Up/Down'}`;
}
function liquidationPayload(tx) {
  const c = (tx.indicators || {}).candidate || {};
  const m = c.microstructure || {};
  const position = positionFor(tx) || {};
  return {
    token_id:position.asset || tx.token_id || m.token_id || tx.asset,
    shares:position.size || tx.size || tx.shares,
    current_price:position.current_price || tx.current_price || tx.price || m.ask,
    stake_usdt:tx.stake_usdt,
    price:tx.price
  };
}
function liquidationLine(tx) {
  const progress = windowProgress(tx);
  const position = positionFor(tx);
  const resolved = resolvedPositionFor(tx);
  const value = position ? money(num(position.current_value, 0)) : (resolved ? `resuelto ${money(resolved.current_value)}` : 'sin posicion abierta');
  const pnl = position ? ` · PnL ${pct(num(position.percent_pnl, 0))} / ${money(position.cash_pnl)}` : '';
  return `Liquidacion: SL ${controlState.timeStop}% ventana${progress == null ? '' : ` (va ${pct(progress)})`} con PnL negativo · TP +${controlState.tp}% · ${value}${pnl}`;
}
async function liquidateTrade(tx) {
  const payload = liquidationPayload(tx);
  if (!confirm('Se intentara liquidar esta posicion abierta en Polymarket. ¿Confirmas?')) return;
  try {
    await post('/automations/paper-trading/liquidate', payload);
    alert('Liquidacion enviada.');
    refresh();
  } catch (e) {
    alert(`No se pudo liquidar: ${e.message}`);
  }
}
function renderOrders(auto, txs) {
  const positions = auto.polymarket_open_positions || [];
  window.__polyPositions = positions;
  if (!positions.length) {
    $('orderList').innerHTML = '<div class="empty">Sin posiciones abiertas reales en Polymarket. El historial queda solo en Ledger/Validation.</div>';
    return;
  }
  $('orderList').innerHTML = positions.map((position, i) => {
    const pnl = `PnL ${pct(position.percent_pnl || 0)} / ${money(position.cash_pnl || 0)}`;
    const detail = [position.event_slug, pnl, `valor ${money(position.current_value || 0)}`].filter(Boolean).join(' · ');
    return `<div class="order-row">
      <div class="order-main">
        <strong>${esc(positionTitle(position))}</strong>
        <small>${esc(detail)}</small>
        <small>${esc(`Liquidacion: SL ${controlState.timeStop}% ventana con PnL negativo · TP +${controlState.tp}% · precio ${position.current_price || '--'}`)}</small>
      </div>
      <div class="order-money">
        <b>${money(position.current_value || 0)}</b>
        <span class="badge abierta">abierta</span>
        <button class="mini-action" onclick="liquidateTrade({token_id:window.__polyPositions[${i}].asset,shares:window.__polyPositions[${i}].size,current_price:window.__polyPositions[${i}].current_price})">Liquidar</button>
      </div>
    </div>`;
  }).join('');
}
function renderGuards(auto, txs) {
  const actions = [...(auto.position_actions || []), ...(auto.claim_actions || [])];
  $('guardBadge').textContent = actions.length ? `${actions.length} acciones` : 'AUTO';
  $('guardList').innerHTML = actions.map(a => {
    const st = statusOf(a);
    const title = a.action === 'claim_profit' || a.action === 'settled_profit' ? 'Ganancia / cobro' : (String(a.action || '').includes('stop_loss') || a.action === 'settled_loss' ? 'Stop loss / perdida' : 'Take profit');
    const windowText = a.window || a.window_et || a.event_slug || a.market || '';
    const meta = [
      a.outcome,
      a.size ? `${a.size} shares` : null,
      a.current_price ? `precio ${a.current_price}` : null,
      a.cash_pnl ? `PnL ${money(a.cash_pnl)}` : null,
      a.threshold_pct ? `umbral ${a.threshold_pct}%` : null
    ].filter(Boolean);
    const note = [compactTime(a.timestamp || auto.last_run_at), windowText, a.note || a.error].filter(Boolean).join(' · ');
    return `<div class="guard-row"><div><strong>${esc(title)}</strong><small>${esc(note)}</small><div class="guard-meta">${meta.map(x => `<span class="badge">${esc(x)}</span>`).join('')}</div></div><span class="badge ${esc(st)}">${esc(labelForStatus(st))}</span></div>`;
  }).join('') || '<div class="empty">Sin acciones reales de SL/TP/cobro en el ultimo ciclo.</div>';
}
function renderActivity(auto, txs) {
  const events = [];
  for (const tx of txs.filter(tx => hasLiveSide(tx) || isRejected(tx)).slice(0, 18)) {
    events.push({time:tx.timestamp, label:orderTitle(tx), status:displayStatus(tx), detail:[tx.window, tx.risk || tx.execution_error].filter(Boolean).join(' · ')});
  }
  for (const a of [...(auto.position_actions || []), ...(auto.claim_actions || [])]) {
    events.push({time:auto.last_run_at, label:a.action || 'proteccion', status:statusOf(a), detail:a.error || a.note || a.market || ''});
  }
  for (const c of (auto.recent_cycles || []).slice().reverse()) {
    if ((c.orders_count || 0) || (c.errors_count || 0) || (c.position_actions_count || 0) || (c.claim_actions_count || 0)) {
      events.push({time:c.created_at, label:`Ciclo ${c.mode || ''}`, status:c.errors_count ? 'error' : 'ok', detail:`${c.orders_count || 0} ordenes · ${c.position_actions_count || 0} protecciones · ${c.claim_actions_count || 0} cobros`});
    }
  }
  events.sort((a, b) => new Date(b.time || 0) - new Date(a.time || 0));
  $('activityCount').textContent = `${events.length} eventos`;
  $('activityLog').innerHTML = events.slice(0, 18).map(e => `<div class="activity-row"><div><strong>${esc(e.label)}</strong><small>${esc(e.detail)}</small></div><div><span class="activity-status">${esc(e.status)}</span><small>${compactTime(e.time)}</small></div></div>`).join('') || '<div class="empty">Sin actividad operativa relevante reciente.</div>';
}
function chartTheme() {
  const dark = document.documentElement.dataset.theme === 'dark';
  return {
    bg:dark ? '#101722' : '#ffffff',
    grid:dark ? '#263444' : '#e3ecef',
    text:dark ? '#aab7c4' : '#687780'
  };
}
function drawEmptyPnl(ctx, w, h, message) {
  const theme = chartTheme();
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = theme.grid;
  ctx.strokeRect(24, 24, w - 48, h - 52);
  ctx.fillStyle = theme.text;
  ctx.font = '14px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(message, w / 2, h / 2);
}
function drawPnl(txs) {
  const c = $('pnlCanvas');
  const ctx = c.getContext('2d');
  const w = c.width;
  const h = c.height;
  ctx.clearRect(0, 0, w, h);
  const pnlRows = realizedPnlRows(txs).slice().reverse();
  if (!pnlRows.length) {
    drawEmptyPnl(ctx, w, h, 'PNL realizado pendiente: sin cierres auditados en la ventana reciente.');
    return;
  }
  const values = [0];
  pnlRows.forEach(tx => values.push(values.at(-1) + num(tx.pnl, 0)));
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const theme = chartTheme();
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i++) {
    const y = 24 + i * (h - 52) / 5;
    ctx.beginPath();
    ctx.moveTo(28, y);
    ctx.lineTo(w - 28, y);
    ctx.stroke();
  }
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = 28 + i * (w - 56) / Math.max(values.length - 1, 1);
    const y = h - 28 - ((v - min) / (max - min || 1)) * (h - 64);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  const grad = ctx.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, '#00a66a');
  grad.addColorStop(.65, '#1967d2');
  grad.addColorStop(1, '#b36b00');
  ctx.strokeStyle = grad;
  ctx.lineWidth = 4;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke();
  ctx.fillStyle = theme.text;
  ctx.font = '13px Space Mono';
  ctx.textAlign = 'left';
  ctx.fillText(`max ${money(max)}`, 32, 22);
  ctx.fillText(`min ${money(min)}`, 32, h - 10);
}
async function refresh() {
  bindControls();
  try {
    const [autoPayload, txPayload, rulesPayload] = await Promise.all([
      get('/automations/paper-trading'),
      get('/agents/transactions?limit=220'),
      get('/agents/rules')
    ]);
    const auto = autoPayload.automation || {};
    syncControls(auto, rulesPayload || {});
    openPositionByAsset = new Map((auto.polymarket_open_positions || []).map(position => [String(position.asset || ''), position]).filter(([asset]) => asset));
    resolvedPositionByAsset = new Map((auto.polymarket_resolved_positions || []).map(position => [String(position.asset || ''), position]).filter(([asset]) => asset));
    openPositionsLoaded = Array.isArray(auto.polymarket_open_positions);
    const txs = (txPayload.transactions || []).filter(tx => String(tx.venue || '').toLowerCase() === 'polymarket');
    renderTop(auto, txs);
    renderWindows(auto, txs);
    renderOrders(auto, txs);
    renderGuards(auto, txs);
    renderActivity(auto, txs);
    drawPnl(txs);
  } catch (e) {
    $('windowGrid').innerHTML = `<div class="empty">No fue posible cargar Polymarket: ${esc(e.message)}</div>`;
  }
}

refresh();
setInterval(() => {$('clockEt').textContent = etClock();}, 1000);
setInterval(refresh, 5000);
