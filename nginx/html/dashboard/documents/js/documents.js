const apiBase = '/harness-api/v1';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const short = (value, n = 52) => {
  const text = String(value ?? '');
  return text.length > n ? `${text.slice(0, n - 1)}...` : text;
};

let selectedFile = null;

async function jsonFetch(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: {'content-type': 'application/json', ...(options.headers || {})},
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function uploadFile(file) {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(`${apiBase}/files`, {
    method: 'POST',
    credentials: 'same-origin',
    body
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data.file;
}

async function processDocument(payload) {
  return jsonFetch('/document-intelligence/process', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

function setBusy(isBusy, text = '') {
  $('processBtn').disabled = isBusy;
  $('ingestState').textContent = isBusy ? 'PROCESANDO' : (text || 'IDLE');
  $('topStatus').textContent = isBusy ? 'Analizando documento' : 'Document Intelligence listo';
}

function badge(el, state) {
  el.classList.remove('badge-ok', 'badge-warn', 'badge-bad');
  if (state) el.classList.add(state);
}

function renderEmpty(message) {
  $('summaryBox').className = 'summary-box empty';
  $('summaryBox').textContent = message;
}

function renderResult(payload) {
  const result = payload.output || payload;
  const analysis = result.analysis || {};
  const verification = result.verification || {};
  const extraction = result.extraction || {};
  const communication = result.communication || {};
  const guidance = result.guidance || {};
  const confidence = Math.round(Number(verification.confidence || 0) * 100);

  $('statusChip').textContent = result.status || '--';
  $('confidenceChip').textContent = `${confidence}%`;
  $('nextAgentChip').textContent = result.next_agent || '--';
  $('safeBadge').textContent = verification.safe_to_email ? 'LISTO CON APROBACION' : 'REVISION';
  badge($('safeBadge'), verification.safe_to_email ? 'badge-ok' : 'badge-warn');
  $('scoreRing').style.setProperty('--value', confidence);
  $('scoreValue').textContent = `${confidence}%`;
  $('tablesBadge').textContent = `${extraction.tables_count || 0} tablas`;
  $('docStatus').textContent = analysis.estado_documento || '--';
  $('draftStatus').textContent = communication.status || '--';
  badge($('draftStatus'), communication.status === 'blocked' ? 'badge-warn' : 'badge-ok');
  $('docIdBadge').textContent = short(result.document_id || '--', 18);

  const missing = verification.missing_fields || [];
  const promptMissing = verification.prompt_missing_fields || [];
  const contradictions = verification.contradictions || [];
  const risks = analysis.riesgos || [];
  $('verifyList').innerHTML = [
    ['Revision humana', verification.requires_human_review ? 'Si' : 'No', verification.requires_human_review ? 'badge-warn' : 'badge-ok'],
    ['Correo seguro', verification.safe_to_email ? 'Con aprobacion' : 'Bloqueado', verification.safe_to_email ? 'badge-ok' : 'badge-warn'],
    ['Faltantes', missing.length ? missing.join(', ') : 'Ninguno', missing.length ? 'badge-warn' : 'badge-ok'],
    ['Guia pendiente', promptMissing.length ? promptMissing.join(', ') : 'Completa', promptMissing.length ? 'badge-warn' : 'badge-ok'],
    ['Contradicciones', contradictions.length ? contradictions.join(', ') : 'Ninguna', contradictions.length ? 'badge-bad' : 'badge-ok'],
    ['Riesgos', risks.length ? risks.join(', ') : 'Sin riesgos', risks.length ? 'badge-warn' : 'badge-ok']
  ].map(([label, value, state]) => `<div class="verify-item"><div><strong>${esc(label)}</strong><small>${esc(value)}</small></div><span class="${state}">${esc(state === 'badge-ok' ? 'OK' : 'CHECK')}</span></div>`).join('');

  const fields = [
    ['Cliente', analysis.cliente],
    ['Correo', analysis.correo || (analysis.correos || []).join(', ')],
    ['RFC', analysis.rfc],
    ['Telefonos', (analysis.telefonos || []).join(', ')],
    ['Monto', analysis.monto ?? '--'],
    ['Fechas', (analysis.fechas || []).join(', ')],
    ['Concepto', analysis.concepto],
    ['Campos guia', (guidance.requested_fields || []).join(', ') || '--'],
    ['Accion', analysis.accion_recomendada]
  ];
  $('fieldsGrid').innerHTML = fields.map(([label, value]) => `<div class="field-card"><small>${esc(label)}</small><strong title="${esc(value || '--')}">${esc(value || '--')}</strong></div>`).join('');

  $('summaryBox').className = 'summary-box';
  $('summaryBox').textContent = analysis.summary || extraction.text_preview || 'Sin resumen disponible.';

  if (communication.status === 'blocked') {
    $('draftBox').className = 'draft-box empty';
    $('draftBox').textContent = communication.reason || 'Borrador bloqueado.';
  } else {
    $('draftBox').className = 'draft-box';
    $('draftBox').textContent = `Para: ${communication.to || '--'}\nAsunto: ${communication.subject || '--'}\n\n${communication.body || ''}`;
  }

  const source = result.source || {};
  $('auditBox').innerHTML = [
    ['Documento', result.document_id],
    ['Archivo', source.filename],
    ['Extension', source.extension],
    ['Tamano', source.size ? `${source.size} bytes` : '--'],
    ['Extraccion', extraction.source_type],
    ['Creado', result.created_at],
    ['Dry run', result.dry_run ? 'true' : 'false'],
    ['Guia', guidance.extraction_prompt || '--'],
    ['Campos guia', (guidance.requested_fields || []).join(', ') || '--'],
    ['File ID', source.file_id || '--']
  ].map(([label, value]) => `<div class="audit-item"><small>${esc(label)}</small><strong title="${esc(value || '--')}">${esc(value || '--')}</strong></div>`).join('');
}

async function handleProcess() {
  try {
    setBusy(true);
    let fileId = $('fileId').value.trim();
    const path = $('filePath').value.trim();
    if (selectedFile) {
      const meta = await uploadFile(selectedFile);
      fileId = meta.id;
      $('fileId').value = fileId;
    }
    if (!fileId && !path) throw new Error('Selecciona archivo, file_id o path');
    const payload = {
      file_id: fileId || undefined,
      path: path || undefined,
      language: $('ocrLanguage').value || 'spa',
      dry_run: $('dryRun').checked,
      extraction_prompt: $('extractionPrompt').value.trim() || undefined
    };
    const result = await processDocument(payload);
    renderResult(result);
    setBusy(false, 'COMPLETO');
  } catch (error) {
    setBusy(false, 'ERROR');
    $('topStatus').textContent = `Error: ${error.message}`;
    renderEmpty(error.message);
  }
}

function bind() {
  $('documentFile').addEventListener('change', event => {
    selectedFile = event.target.files?.[0] || null;
    if (selectedFile) {
      $('fileName').textContent = selectedFile.name;
      $('fileMeta').textContent = `${selectedFile.type || 'archivo'} · ${selectedFile.size} bytes`;
      document.querySelector('.drop-zone').classList.add('ready');
    }
  });
  $('processBtn').addEventListener('click', handleProcess);
  jsonFetch('/document-intelligence/rules')
    .then(data => {
      const output = data.output || {};
      $('statusChip').textContent = output.mode || 'draft_and_review';
      $('nextAgentChip').textContent = (output.agents || [])[0] || '--';
      $('confidenceChip').textContent = 'sin documento';
    })
    .catch(error => {
      $('topStatus').textContent = `Rules error: ${error.message}`;
    });
}

bind();
