const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString(undefined, {maximumFractionDigits: 1});
const pct = n => `${fmt(n)}%`;
const gb = n => `${fmt(n)} GB`;
const mb = n => `${fmt(n)} MB`;
const esc = v => String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const klass = value => Number(value) >= 90 ? 'bad' : Number(value) >= 75 ? 'warn' : 'ok';
const pill = (label, state='ok') => `<span class="pill ${state}">${esc(label)}</span>`;
const bar = value => `<div class="bar ${klass(value)}"><span style="width:${Math.max(0, Math.min(100, Number(value)||0))}%"></span></div>`;
const row = (k,v) => `<div class="detail-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`;

function kpi(label, value, sub, state='ok'){
  return `<article class="metric-card ${state === 'ok' ? '' : state}">
    <div class="metric-label">${esc(label)}</div>
    <div class="metric-value">${esc(value)}</div>
    <div class="metric-sub">${esc(sub)}</div>
  </article>`;
}

async function loadStatus(){
  const response = await fetch('./status.json?ts=' + Date.now(), {cache:'no-store', credentials:'same-origin'});
  if(!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function requireAdmin(){
  const response = await fetch('/auth/userinfo', {credentials:'same-origin', cache:'no-store'});
  if(!response.ok) throw new Error('unauthorized');
  const user = await response.json();
  if(user.role !== 'admin'){
    document.body.innerHTML = '<main style="padding:40px"><div class="ops-panel"><h2>Acceso restringido</h2><p class="metric-sub">Este panel está disponible únicamente para el perfil admin.</p><p style="margin-top:18px"><a class="btn-logout" href="/dashboard/" style="display:inline-flex">Volver al dashboard</a></p></div></main>';
    setTimeout(() => { window.location.href = '/dashboard/'; }, 1200);
    return false;
  }
  return true;
}

function renderKpis(data){
  const gpu = (data.gpu || [])[0] || {};
  const mem = data.memory || {};
  const cpu = data.cpu || {};
  const disk = (data.disks || [])[0] || {};
  const btc = data.bitcoind || {};
  $('kpi-grid').innerHTML = [
    kpi('GPU', gpu.status === 'ok' ? `${pct(gpu.memory_used_percent)} VRAM` : 'N/D', `${gpu.name || 'nvidia-smi'} · ${gpu.temperature_c ?? '—'} °C`, klass(gpu.memory_used_percent || 0)),
    kpi('RAM', pct(mem.used_percent), `${gb(mem.used_gb)} usados · ${gb(mem.available_gb)} libres`, klass(mem.used_percent || 0)),
    kpi('CPU', pct(cpu.utilization_percent), `${cpu.cores || '—'} cores · load ${cpu.load?.['1m'] ?? '—'}`, klass(cpu.utilization_percent || 0)),
    kpi('Disco raíz', pct(disk.used_percent), `${gb(disk.free_gb)} libres en ${disk.mount || '/'}`, disk.status === 'warning' ? 'warn' : 'ok'),
    kpi('bitcoind', btc.status === 'ok' ? 'OK' : 'Revisar', `${btc.info?.blocks || '—'} bloques · ${btc.info?.network || 'network n/d'}`, btc.status === 'ok' ? 'ok' : 'warn'),
    kpi('UPS Dahua', data.ups?.status === 'detected' ? 'Detectado' : 'No detectado', 'NUT instalado · esperando USB/HID', data.ups?.status === 'detected' ? 'ok' : 'warn'),
    kpi('Swap', `${gb(mem.swap_used_gb)}`, `${gb(mem.swap_total_gb)} total`, Number(mem.swap_used_gb) > 2 ? 'warn' : 'ok'),
    kpi('Actualización', '1 min', data.generated_at || '—', 'ok')
  ].join('');
}

function renderGpu(data){
  const gpu = (data.gpu || [])[0] || {};
  const governor = data.gpu_idle_governor || {};
  const governorState = governor.status === 'ok' ? governor.mode : governor.status || 'n/d';
  $('gpu-panel').innerHTML = `<h2>NVIDIA GPU ${pill(gpu.status === 'ok' ? 'Online' : 'N/D', gpu.status === 'ok' ? 'ok' : 'warn')}</h2>
    ${bar(gpu.memory_used_percent || 0)}
    <div class="detail-list">
      ${row('Modelo', gpu.name || 'No disponible')}
      ${row('Driver', gpu.driver || '—')}
      ${row('Temperatura', gpu.temperature_c != null ? `${gpu.temperature_c} °C` : '—')}
      ${row('Uso GPU', pct(gpu.utilization_percent))}
      ${row('Memoria', `${mb(gpu.memory_used_mb)} / ${mb(gpu.memory_total_mb)}`)}
      ${row('Potencia', `${fmt(gpu.power_draw_w)} W / ${fmt(gpu.power_limit_w)} W`)}
      ${row('Modo idle automático', governorState)}
      ${row('Límite idle / activo', governor.status === 'ok' ? `${fmt(governor.idle_power_limit_w)} W / ${fmt(governor.active_power_limit_w)} W` : '—')}
      ${row('Ciclos ociosos', governor.status === 'ok' ? `${governor.idle_cycles}/${governor.idle_after_cycles}` : '—')}
      ${row('Última acción', governor.last_action || '—')}
    </div>`;
}

function renderUps(data){
  const ups = data.ups || {};
  const services = ups.services || {};
  const usb = (ups.usb_devices || []).join('\n') || 'Sin UPS visible en USB';
  $('ups-panel').innerHTML = `<h2>No Break UPS ${pill(ups.status === 'detected' ? 'Detectado' : 'No detectado', ups.status === 'detected' ? 'ok' : 'warn')}</h2>
    <div class="detail-list">
      ${row('Equipo esperado', ups.expected_device?.model || 'Dahua 1500VA 900W')}
      ${row('NUT mode', (ups.nut_mode || '').replace(/\n/g, ' · ') || '—')}
      ${row('upsc', ups.tools?.upsc ? 'instalado' : 'no disponible')}
      ${row('nut-scanner', ups.tools?.['nut-scanner'] ? 'instalado' : 'no disponible')}
      ${row('nut-server', `${services['nut-server']?.active || 'n/d'} / ${services['nut-server']?.enabled || 'n/d'}`)}
      ${row('nut-monitor', `${services['nut-monitor']?.active || 'n/d'} / ${services['nut-monitor']?.enabled || 'n/d'}`)}
    </div>
    <div class="metric-sub" style="margin:12px 0 8px">${esc(ups.note || '')}</div>
    <div class="raw-box">${esc(usb)}</div>`;
}

function renderCpu(data){
  const cpu = data.cpu || {};
  $('cpu-panel').innerHTML = `<h2>Procesador ${pill(pct(cpu.utilization_percent), klass(cpu.utilization_percent || 0))}</h2>
    ${bar(cpu.utilization_percent || 0)}
    <div class="detail-list">
      ${row('Modelo', cpu.model || '—')}
      ${row('Cores', cpu.cores || '—')}
      ${row('Load 1m / 5m / 15m', `${cpu.load?.['1m'] ?? '—'} / ${cpu.load?.['5m'] ?? '—'} / ${cpu.load?.['15m'] ?? '—'}`)}
      ${row('Temperatura CPU', cpu.temperature_c != null ? `${cpu.temperature_c} °C` : 'No expuesta')}
    </div>`;
}

function renderMemory(data){
  const mem = data.memory || {};
  $('memory-panel').innerHTML = `<h2>Memoria RAM ${pill(pct(mem.used_percent), klass(mem.used_percent || 0))}</h2>
    ${bar(mem.used_percent || 0)}
    <div class="detail-list">
      ${row('Total', gb(mem.total_gb))}
      ${row('Usada', gb(mem.used_gb))}
      ${row('Disponible', gb(mem.available_gb))}
      ${row('Swap', `${gb(mem.swap_used_gb)} / ${gb(mem.swap_total_gb)}`)}
    </div>`;
}

function renderDisks(data){
  const rows = (data.disks || []).map(d => `<tr><td>${esc(d.mount)}</td><td>${esc(d.device)}</td><td>${esc(d.type)}</td><td>${gb(d.total_gb)}</td><td>${gb(d.used_gb)}</td><td>${gb(d.free_gb)}</td><td>${pill(pct(d.used_percent), d.status === 'warning' ? 'warn' : 'ok')}</td></tr>`).join('');
  $('disk-panel').innerHTML = `<h2>Estado de discos ${pill(`${(data.disks || []).length} mounts`, 'ok')}</h2>
    <div class="table-wrap"><table class="ops-table"><thead><tr><th>Mount</th><th>Device</th><th>Tipo</th><th>Total</th><th>Usado</th><th>Libre</th><th>Uso</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderBitcoin(data){
  const btc = data.bitcoind || {};
  const info = btc.info || {};
  $('bitcoin-panel').innerHTML = `<h2>bitcoind ${pill(btc.status === 'ok' ? 'Online' : 'Revisar', btc.status === 'ok' ? 'ok' : 'warn')}</h2>
    <div class="detail-list">
      ${row('Contenedor', btc.container?.status || '—')}
      ${row('Imagen', btc.container?.image || '—')}
      ${row('Chain', info.chain || '—')}
      ${row('Bloques / headers', `${info.blocks || '—'} / ${info.headers || '—'}`)}
      ${row('Progreso', info.verification_progress || '—')}
      ${row('Red', info.network || '—')}
      ${row('Warnings', info.warnings || 'ninguno')}
    </div>`;
}

function renderDocker(data){
  const rows = (data.docker || []).map(c => `<tr><td>${esc(c.name)}</td><td>${esc(c.status)}</td><td>${esc(c.health || 'n/a')}</td><td>${esc(c.image || '—')}</td></tr>`).join('');
  $('docker-panel').innerHTML = `<h2>Servicios Docker ${pill(`${(data.docker || []).filter(c => c.running).length} running`, 'ok')}</h2>
    <div class="table-wrap"><table class="ops-table"><thead><tr><th>Servicio</th><th>Status</th><th>Health</th><th>Imagen</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function render(data){
  $('generated-at').textContent = data.generated_at || '—';
  $('system-freshness').textContent = 'Actualizado · ' + new Date(data.generated_at).toLocaleTimeString('es-MX', {hour12:false});
  renderKpis(data);
  renderGpu(data);
  renderUps(data);
  renderCpu(data);
  renderMemory(data);
  renderDisks(data);
  renderBitcoin(data);
  renderDocker(data);
}

async function refresh(){
  try {
    render(await loadStatus());
  } catch (error) {
    $('system-freshness').textContent = 'Sin datos';
    $('kpi-grid').innerHTML = kpi('Estado', 'Sin datos', error.message, 'bad');
  }
}

requireAdmin().then(ok => {
  if(!ok) return;
  refresh();
  setInterval(refresh, 30000);
}).catch(() => {
  window.location.href = '/login';
});
