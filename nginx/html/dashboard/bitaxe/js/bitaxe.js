(function () {

  const BITAXE_URL = '/bitaxe/info';
  const POLL_INTERVAL = 15000;
  const MAX_SPARK = 30;

  let sparkData = [];

  const $ = id => document.getElementById(id);

  const fmt = (n, dec = 1) =>
    n != null ? Number(n).toFixed(dec) : '—';

  const fmtM = n => {
    if (n == null) return '—';

    n = Number(n);

    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';

    return n.toString();
  };

  const setText = (id, val) => {
    const el = $(id);
    if (el) el.textContent = val;
  };

  const setBar = (id, val, max) => {
    const el = $(id);

    if (!el) return;

    const pct = Math.min(100, Math.max(0, (val / max) * 100));

    el.style.width = pct + '%';
  };

  const uptimeFmt = s => {

    s = Number(s);

    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);

    return h > 0
      ? `${h}h ${m}m`
      : `${m}m`;
  };

  function drawSpark(data) {

    const canvas = $('bx-spark-canvas');

    if (!canvas || data.length < 2) return;

    canvas.width = canvas.offsetWidth;

    const ctx = canvas.getContext('2d');

    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0,0,W,H);

    const min = Math.min(...data) * 0.998;
    const max = Math.max(...data) * 1.002;

    const sy = v =>
      H - ((v - min) / (max - min || 1)) * (H - 8) - 4;

    const sx = i =>
      (i / (data.length - 1)) * W;

    ctx.beginPath();

    data.forEach((v,i)=>{

      if(i===0){
        ctx.moveTo(sx(i), sy(v));
      } else {
        ctx.lineTo(sx(i), sy(v));
      }

    });

    ctx.strokeStyle = '#00e5ff';
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  async function fetchBitaxe() {

    try {

      const res = await fetch(BITAXE_URL, {
        cache:'no-store'
      });

      const d = await res.json();

      setText('bx-status-badge', `⬤ ${fmt(d.hashRate)} GH/s`);

      setText(
        'bx-footer-meta',
        `192.168.88.181 · ${d.ASICModel || 'BM1370'} · ${d.axeOSVersion || ''}`
      );

      setText('bx-asic-model', d.ASICModel || 'BM1370');
      setText('bx-version', d.axeOSVersion || '—');
      setText('bx-uptime', uptimeFmt(d.uptimeSeconds));

      setText('bx-hr-live', fmt(d.hashRate));
      setText('bx-hr-1m', fmt(d.hashRate_1m));
      setText('bx-hr-10m', fmt(d.hashRate_10m));
      setText('bx-hr-1h', fmt(d.hashRate_1h));

      setText('bx-best-diff', fmtM(d.bestDiff));
      setText('bx-best-session-diff', fmtM(d.bestSessionDiff));

      setText('bx-power', fmt(d.power));
      setText('bx-efficiency',
        d.hashRate > 0
          ? (d.power / (d.hashRate / 1000)).toFixed(2)
          : '—'
      );

      setBar('bx-bar-temp', d.temp, 85);
      setBar('bx-bar-vrtemp', d.vrTemp, 90);
      setBar('bx-bar-fan', d.fanspeed, 100);
      setBar('bx-bar-rpm', d.fanrpm, 8000);
      setBar('bx-bar-power', d.power, d.maxPower || 40);
      setBar('bx-bar-err', d.errorPercentage, 10);

      setText('bx-val-temp', `${fmt(d.temp)} °C`);
      setText('bx-val-vrtemp', `${fmt(d.vrTemp)} °C`);
      setText('bx-val-fan', `${fmt(d.fanspeed)} %`);
      setText('bx-val-rpm', `${Math.round(d.fanrpm)} RPM`);
      setText('bx-val-power2', `${fmt(d.power)} W`);
      setText('bx-val-err', `${fmt(d.errorPercentage,2)} %`);

      setText('bx-wallet', d.stratumUser || '—');

      setText('bx-block-height', d.blockHeight != null ? Number(d.blockHeight).toLocaleString('en-US') : '—');
      setText('bx-net-diff', fmtM(d.networkDifficulty));
      setText('bx-coinbase-sats', d.coinbaseValueTotalSatoshis != null ? Number(d.coinbaseValueTotalSatoshis).toLocaleString('en-US') : '—');
      setText('bx-scriptsig', d.scriptsig || '—');

      const accepted = Number(d.sharesAccepted || 0);
      const rejected = Number(d.sharesRejected || 0);
      const totalShares = accepted + rejected;
      setText('bx-shares-acc', accepted.toLocaleString('en-US'));
      setText('bx-shares-rej', rejected.toLocaleString('en-US'));
      setText('bx-shares-rate', totalShares > 0 ? ((accepted / totalShares) * 100).toFixed(2) + '%' : '—');
      setText('bx-pool-url', d.stratumURL || '—');
      setText('bx-pool-port', d.stratumPort != null ? d.stratumPort : '—');
      setText('bx-pool-diff', d.poolDifficulty != null ? Number(d.poolDifficulty).toLocaleString('en-US') : (d.stratumSuggestedDifficulty != null ? Number(d.stratumSuggestedDifficulty).toLocaleString('en-US') : '—'));
      setText('bx-rej-reason', Array.isArray(d.sharesRejectedReasons) && d.sharesRejectedReasons.length ? d.sharesRejectedReasons.map(r => `${r.message}: ${r.count}`).join(', ') : '—');
      setText('bx-fallback', d.fallbackStratumURL ? `${d.fallbackStratumURL}:${d.fallbackStratumPort || ''}` : '—');
      setText('bx-using-fallback', d.isUsingFallbackStratum ? 'Sí' : 'No');
      setText('bx-response-time', d.responseTime != null ? `${fmt(d.responseTime, 2)} ms` : '—');
      setText('bx-pool-conn', d.poolConnectionInfo || '—');

      setText('bx-sys-asic', d.ASICModel || '—');
      setText('bx-sys-freq', d.frequency != null ? `${d.frequency} MHz` : '—');
      setText('bx-sys-cores', d.smallCoreCount != null ? Number(d.smallCoreCount).toLocaleString('en-US') : '—');
      setText('bx-sys-volt', d.coreVoltageActual != null ? `${d.coreVoltageActual} mV` : (d.coreVoltage != null ? `${d.coreVoltage} mV` : '—'));
      setText('bx-sys-vin', d.voltage != null ? `${(Number(d.voltage) / 1000).toFixed(3)} V` : '—');
      setText('bx-sys-ssid', d.ssid || '—');
      setText('bx-sys-rssi', d.wifiRSSI != null ? `${d.wifiRSSI} dBm` : '—');
      setText('bx-sys-ip', d.ipv4 || '—');
      setText('bx-sys-ram', d.freeHeapSpiram != null ? `${(Number(d.freeHeapSpiram) / 1024 / 1024).toFixed(2)} MB` : '—');
      setText('bx-sys-board', d.boardVersion || '—');
      setText('bx-sys-host', d.hostname || '—');
      setText('bx-sys-mac', d.macAddr || '—');
      setText('bx-sys-idf', d.idfVersion || '—');
      setText('bx-sys-reset', d.resetReason || '—');

      setText(
        'bx-last-update',
        'Actualizado: ' +
        new Date().toLocaleTimeString('es-MX')
      );

      sparkData.push(Number(d.hashRate));

      if (sparkData.length > MAX_SPARK) {
        sparkData.shift();
      }

      drawSpark(sparkData);

    } catch(err){

      setText(
        'bx-last-update',
        'Error: ' + err.message
      );

    }

  }

  fetchBitaxe();

  setInterval(fetchBitaxe, POLL_INTERVAL);

})();
