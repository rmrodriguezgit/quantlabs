const apiBase = '/harness-api/v1';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const short = (value, n = 64) => {
  const text = String(value ?? '');
  return text.length > n ? `${text.slice(0, n - 1)}...` : text;
};

let selectedFile = null;
let lastCopy = '';

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

function setStatus(text) {
  $('topStatus').textContent = text;
}

function parseTags() {
  return $('tagsInput').value.split(',').map(x => x.trim()).filter(Boolean);
}

function renderStats(stats = {}) {
  $('docsMetric').textContent = stats.documents ?? '--';
  $('chunksMetric').textContent = stats.chunks ?? '--';
}

function renderDocuments(payload) {
  const output = payload.output || payload;
  const docs = output.documents || [];
  renderStats(output.stats || {});
  $('documentsList').innerHTML = docs.length ? docs.slice(0, 8).map(doc => `
    <div class="doc-item">
      <small>${esc(doc.extension || 'doc')}</small>
      <strong title="${esc(doc.filename)}">${esc(doc.filename)}</strong>
      <p>${esc(doc.chunks)} chunks · ${esc((doc.tags || []).join(', ') || 'sin tags')}</p>
    </div>
  `).join('') : '<div class="empty">Sin documentos indexados.</div>';
}

function renderQuery(payload) {
  const result = payload.output || payload;
  const answer = result.answer || {};
  const evidence = result.evidence || [];
  lastCopy = result.copy_ready || '';
  $('copyBox').textContent = lastCopy || 'Sin salida copiable.';
  $('evidenceState').textContent = `${evidence.length} items`;
  $('answerCards').innerHTML = [
    ['Confianza', answer.confidence || '--', answer.summary || ''],
    ['Evidencia', `${evidence.length} fragmentos`, result.stats ? `${result.stats.documents} documentos` : ''],
    ['Pendientes', (answer.pending || []).length ? answer.pending.join(', ') : 'Ninguno', '']
  ].map(([label, value, note]) => `<div class="answer-card"><small>${esc(label)}</small><strong>${esc(value)}</strong><p>${esc(note)}</p></div>`).join('');
  $('evidenceList').innerHTML = evidence.length ? evidence.map(item => `
    <div class="evidence-item">
      <small>${esc(item.filename || item.document_id)} · score ${esc(item.score)}</small>
      <p>${esc(short(item.preview, 360))}</p>
    </div>
  `).join('') : '<div class="empty">Sin evidencia.</div>';
  renderStats(result.stats || {});
}

async function refreshDocuments() {
  const data = await jsonFetch('/escola/documents');
  renderDocuments(data);
}

async function handleIngest() {
  try {
    if (!selectedFile) throw new Error('Selecciona un archivo');
    $('ingestBtn').disabled = true;
    $('ingestState').textContent = 'SUBIENDO';
    setStatus('Subiendo archivo');
    const meta = await uploadFile(selectedFile);
    $('ingestState').textContent = 'INDEXANDO';
    setStatus('Indexando en ESCOLA');
    const result = await jsonFetch('/escola/ingest', {
      method: 'POST',
      body: JSON.stringify({file_id: meta.id, tags: parseTags()})
    });
    $('ingestState').textContent = result.output?.already_indexed ? 'YA INDEXADO' : 'LISTO';
    setStatus('ESCOLA actualizado');
    await refreshDocuments();
  } catch (error) {
    $('ingestState').textContent = 'ERROR';
    setStatus(`Error: ${error.message}`);
  } finally {
    $('ingestBtn').disabled = false;
  }
}

async function handleQuery() {
  try {
    const question = $('questionInput').value.trim();
    if (!question) throw new Error('Escribe una consulta');
    $('queryBtn').disabled = true;
    $('queryState').textContent = 'CONSULTANDO';
    setStatus('Consultando ESCOLA');
    const result = await jsonFetch('/escola/query', {
      method: 'POST',
      body: JSON.stringify({question, top_k: Number($('topK').value || 6)})
    });
    renderQuery(result);
    $('queryState').textContent = 'COMPLETO';
    setStatus('Respuesta lista');
  } catch (error) {
    $('queryState').textContent = 'ERROR';
    setStatus(`Error: ${error.message}`);
    $('copyBox').textContent = error.message;
  } finally {
    $('queryBtn').disabled = false;
  }
}

async function copyOutput() {
  if (!lastCopy) return;
  await navigator.clipboard.writeText(lastCopy);
  $('copyBtn').textContent = 'Copiado';
  setTimeout(() => { $('copyBtn').textContent = 'Copiar'; }, 1200);
}

function bind() {
  $('escolaFile').addEventListener('change', event => {
    selectedFile = event.target.files?.[0] || null;
    if (!selectedFile) return;
    $('fileName').textContent = selectedFile.name;
    $('fileMeta').textContent = `${selectedFile.type || 'archivo'} · ${selectedFile.size} bytes`;
    document.querySelector('.drop-zone').classList.add('ready');
  });
  $('ingestBtn').addEventListener('click', handleIngest);
  $('queryBtn').addEventListener('click', handleQuery);
  $('copyBtn').addEventListener('click', copyOutput);
  refreshDocuments().catch(error => {
    setStatus(`ESCOLA: ${error.message}`);
  });
}

bind();
