/* ═══════════════════════════════════════════════════════════════
   bitaxe.js  —  QuantLab AI · BitAxe Monitor
   Polling automático cada 15 s al proxy nginx /bitaxe/info
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const BITAXE_URL    = '/bitaxe/info';
  const POLL_INTERVAL = 15000;   // ms
  const MAX_SPARK     = 40;      // puntos en el sparkline

  let sparkData      = [];
  let countdownVal   = POLL_INTERVAL / 1000;
  let countdownTimer = null;
  let pollTimer      = null;

  /* ── Utilidades ───────────────────────────────────────────── */
  const $       = id  => document.getElementById(id);
  const fmt     = (n, dec = 1) => n != null ? (+n).toFixed(dec) : '—';
  const fmtM    = n => {
    if (n == null) return '—';
    n = +n;
    if (n >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (n >= 1e9)  return (n / 1e9).toFixed(2)  + 'B';
    if (n >= 1e6)  return (n / 1e6).toFixed(1)  + 'M';
    if (n >= 1e3)  return (n / 1e3).toFixed(1)  + 'k';
    return n.toString();
  };
  const pct     = (v, max) => Math.min(100, Math.max(0, (v / max) * 100)).toFixed(1) + '%';
  const setBar  = (id, v, max) => { const el = $(id); if (el) el.style.width = pct(v, max); };
  const setText = (id, v)      => { const el = $(id); if (el) el.textContent = v; };
  const uptimeFmt = s => {
    s = +s;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  };

  /* ── Sparkline ────────────────────────────────────────────── */
  function drawSpark(data) {
    const canvas = $('bx-spark-canvas');
    if (!canvas || data.length < 2) return;
    canvas.width  = canvas.offsetWidth  || 800;
    canvas.height = canvas.offsetHeight || 60;
    const W = canvas.width, H = canvas.height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);

    const min = Math.min(...data) * 0.997;
    const max = Math.max(...data) * 1.003;
    const sy  = v => H - ((v - min) / (max - min || 1)) * (H - 10) - 5;
    const sx  = i => (i / (data.length - 1)) * W;

    // Área de relleno
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(0,229,255,.15)');
    grad.addColorStop(1, 'rgba(0,229,255,.00)');
    ctx.beginPath();
    ctx.moveTo(sx(0), H);
    data.forEach((v, i) => ctx.lineTo(sx(i), sy(v)));
    ctx.lineTo(sx(data.length - 1), H);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Línea principal
    ctx.beginPath();
    data.forEach((v, i) => i === 0 ? ctx.moveTo(sx(i), sy(v)) : ctx.lineTo(sx(i), sy(v)));
    ctx.strokeStyle = '#00e5ff';
    ctx.lineWidth   = 2;
    ctx.lineJoin    = 'round';
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Punto final
    const lx = sx(data.length - 1);
    const ly = sy(data[data.length - 1]);
    ctx.beginPath();
    ctx.arc(lx, ly, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#00e5ff';
    ctx.fill();

    // Etiqueta último valor
    ctx.font      = '10px Space Mono, monospace';
    ctx.fillStyle = 'rgba(0,229,255,.7)';
    ctx.textAlign = 'right';
    ctx.fillText(fmt(data[data.length - 1]) + ' GH/s', W - 4, ly - 7);
  }

  /* ── Render dominios ASIC ─────────────────────────────────── */
  function renderDomains(asics) {
    const container = $('bx-domains-grid');
    if (!container || !asics || !asics[0]) return;
    const domains = asics[0].domains || [];
    container.innerHTML = domains.map((v, i) =>
      `<div class="bx-domain-card">
        <div class="bx-domain-label">Domain ${i}</div>
        <div class="bx-domain-val">${fmt(v)}</div>
        <div style="font-size:9px;color:rgba(255,255,255,.3);margin-top:2px;">GH/s</div>
      </div>`
    ).join('');
  }

  /* ── Fetch principal ──────────────────────────────────────── */
  async function fetchBitAxe() {
    const dot   = $('bx-live-dot');
    const badge = $('bx-status-badge');
    if (dot) { dot.classList.remove('offline'); dot.classList.add('loading'); }

    try {
      const res = await fetch(BITAXE_URL, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const d = await res.json();

      /* Estado online */
      if (dot)   { dot.classList.remove('loading', 'offline'); }
      if (badge) { badge.textContent = '⬤ ' + fmt(d.hashRate) + ' GH/s'; badge.style.color = '#00c97a'; }

      /* Header */
      setText('bx-asic-model', d.ASICModel || 'BM1370');
      setText('bx-version',    d.axeOSVersion || '—');
      setText('bx-uptime',     uptimeFmt(d.uptimeSeconds));

      /* KPIs hashrate */
      setText('bx-hr-live',           fmt(d.hashRate));
      setText('bx-hr-1m',             fmt(d.hashRate_1m));
      setText('bx-hr-10m',            fmt(d.hashRate_10m));
      setText('bx-hr-1h',             fmt(d.hashRate_1h));
      setText('bx-hr-expected',       fmt(d.expectedHashrate));
      setText('bx-best-diff',         fmtM(d.bestDiff));
      setText('bx-best-session-diff', fmtM(d.bestSessionDiff));

      /* KPIs eléctricos */
      setText('bx-power',      fmt(d.power, 1));
      const eff = d.hashRate > 0 ? (d.power / (d.hashRate / 1000)).toFixed(2) : '—';
      setText('bx-efficiency', eff);

      /* KPIs red Bitcoin */
      setText('bx-block-height',    d.blockHeight    ? d.blockHeight.toLocaleString() : '—');
      setText('bx-net-diff',        d.networkDifficulty ? fmtM(d.networkDifficulty) : '—');
      setText('bx-coinbase-sats',   d.coinbaseValueUserSatoshis ? d.coinbaseValueUserSatoshis.toLocaleString() : '—');
      setText('bx-scriptsig',       d.scriptsig || '—');

      /* Barras métricas */
      setBar('bx-bar-temp',   d.temp,             85);
      setBar('bx-bar-vrtemp', d.vrTemp,           90);
      setBar('bx-bar-fan',    d.fanspeed,        100);
      setBar('bx-bar-rpm',    d.fanrpm,         8000);
      setBar('bx-bar-power',  d.power,           d.maxPower || 40);
      setBar('bx-bar-err',    d.errorPercentage,  10);

      setText('bx-val-temp',   fmt(d.temp,   1) + ' °C');
      setText('bx-val-vrtemp', fmt(d.vrTemp, 0) + ' °C');
      setText('bx-val-fan',    fmt(d.fanspeed, 1) + ' %');
      setText('bx-val-rpm',    Math.round(d.fanrpm).toLocaleString() + ' RPM');
      setText('bx-val-power2', fmt(d.power, 1) + ' W');
      setText('bx-val-err',    fmt(d.errorPercentage, 2) + ' %');

      /* Shares */
      const total = (d.sharesAccepted || 0) + (d.sharesRejected || 0);
      const rate  = total > 0 ? ((d.sharesAccepted / total) * 100).toFixed(1) + '%' : '—';
      setText('bx-shares-acc',  d.sharesAccepted);
      setText('bx-shares-rej',  d.sharesRejected);
      setText('bx-shares-rate', rate);

      /* Pool */
      const usingFallback = !!d.isUsingFallbackStratum;
      setText('bx-pool-url',   usingFallback ? d.fallbackStratumURL  : d.stratumURL);
      setText('bx-pool-port',  usingFallback ? d.fallbackStratumPort : d.stratumPort);
      setText('bx-pool-diff',  d.poolDifficulty);
      const rejReason = (d.sharesRejectedReasons && d.sharesRejectedReasons.length)
        ? d.sharesRejectedReasons.map(r => `${r.message} (${r.count})`).join(', ')
        : 'ninguno';
      setText('bx-rej-reason',     rejReason);
      setText('bx-fallback',       d.fallbackStratumURL || '—');
      setText('bx-using-fallback', usingFallback ? '⚠ Sí (fallback activo)' : 'No · pool primario OK');
      setText('bx-response-time',  d.responseTime != null ? fmt(d.responseTime, 1) + ' ms' : '—');

      /* Sistema */
      setText('bx-sys-asic',  d.ASICModel || '—');
      setText('bx-sys-freq',  (d.frequency || '—') + ' MHz');
      setText('bx-sys-cores', (d.smallCoreCount || '—').toLocaleString());
      setText('bx-sys-volt',  (d.coreVoltageActual || '—') + ' mV (nom. ' + (d.coreVoltage || '—') + ' mV)');
      setText('bx-sys-vin',   fmt(d.voltage / 1000, 3) + ' V  ·  ' + fmt(d.current / 1000, 2) + ' A');
      setText('bx-sys-ssid',  d.ssid || '—');
      setText('bx-sys-rssi',  (d.wifiRSSI || '—') + ' dBm');
      setText('bx-sys-ip',    d.ipv4 || '—');
      setText('bx-sys-mac',   d.macAddr || '—');
      setText('bx-sys-ram',   d.freeHeapSpiram
        ? (d.freeHeapSpiram / 1024 / 1024).toFixed(2) + ' MB PSRAM · ' + (d.freeHeapInternal / 1024).toFixed(0) + ' KB int'
        : '—');
      setText('bx-sys-board',  'v' + (d.boardVersion || '—'));
      setText('bx-sys-idf',    d.idfVersion || '—');
      setText('bx-sys-reason', d.resetReason || '—');

      /* Wallet */
      setText('bx-wallet', d.stratumUser || d.fallbackStratumUser || '—');

      /* Dominios ASIC */
      if (d.hashrateMonitor) renderDomains(d.hashrateMonitor.asics);

      /* Sparkline */
      sparkData.push(+d.hashRate);
      if (sparkData.length > MAX_SPARK) sparkData.shift();
      drawSpark(sparkData);

      /* Timestamp */
      setText('bx-last-update', 'Actualizado: ' + new Date().toLocaleTimeString('es-MX'));

    } catch (err) {
      if (dot)   { dot.classList.remove('loading'); dot.classList.add('offline'); }
      if (badge) { badge.textContent = '✕ Sin conexión'; badge.style.color = '#ff4466'; }
      setText('bx-last-update', 'Error: ' + err.message);
      console.warn('[BitAxe]', err);
    }
  }

  /* ── Countdown ────────────────────────────────────────────── */
  function startCountdown() {
    countdownVal = POLL_INTERVAL / 1000;
    clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      countdownVal--;
      setText('bx-countdown', 'Próx. actualización: ' + countdownVal + 's');
      if (countdownVal <= 0) clearInterval(countdownTimer);
    }, 1000);
  }

  /* ── Polling recursivo ────────────────────────────────────── */
  function schedulePoll() {
    pollTimer = setTimeout(() => {
      fetchBitAxe().then(() => { startCountdown(); schedulePoll(); });
    }, POLL_INTERVAL);
  }

  /* ── Botón refresh manual ─────────────────────────────────── */
  const btn = $('bx-refresh-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      clearTimeout(pollTimer);
      clearInterval(countdownTimer);
      fetchBitAxe().then(() => { startCountdown(); schedulePoll(); });
    });
  }

  /* ── Redibuja sparkline al cambiar tamaño ─────────────────── */
  window.addEventListener('resize', () => drawSpark(sparkData));

  /* ── Arranque ─────────────────────────────────────────────── */
  fetchBitAxe().then(() => { startCountdown(); schedulePoll(); });

})();
