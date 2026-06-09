const apiBase = '/harness-api/v1';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const short = (value, n = 90) => {
  const text = String(value ?? '');
  return text.length > n ? `${text.slice(0, n - 1)}...` : text;
};

let selectedFile = null;
let lastCopy = '';
let lastEvidence = [];
let responseCopies = [];

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
  body.append('scope', 'escola');
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
  if (stats.database) renderDatabase(stats.database);
}

function openModal(id) {
  const modal = $(id);
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
}

function closeModals() {
  document.querySelectorAll('.modal.open').forEach(modal => {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  });
}

function renderDocuments(payload) {
  const output = payload.output || payload;
  const docs = output.documents || [];
  renderStats(output.stats || {});
  $('documentsList').innerHTML = docs.length ? docs.slice(0, 10).map(doc => `
    <div class="doc-item">
      <small>${esc(doc.extension || 'doc')}</small>
      <strong title="${esc(doc.filename)}">${esc(doc.filename)}</strong>
      <p>${esc(doc.chunks)} chunks · ${esc((doc.tags || []).join(', ') || 'sin tags')}</p>
    </div>
  `).join('') : '<div class="empty">Sin documentos indexados.</div>';
}

function renderDatabase(payload = {}) {
  const stats = payload.stats || {};
  $('dbStatus').textContent = payload.active ? `${stats.nombre || 'BD activa'}` : 'Sin BD activa';
  $('dbStats').innerHTML = payload.active ? `
    <div class="answer-card"><small>Programas</small><strong>${esc(stats.programas ?? 0)}</strong></div>
    <div class="answer-card"><small>Materias</small><strong>${esc(stats.materias ?? 0)}</strong></div>
    <div class="answer-card"><small>Optativas</small><strong>${esc(stats.optativas ?? 0)}</strong></div>
  ` : '<div class="empty">Importa la BD NoSQL para consultas estructuradas.</div>';
}

async function refreshDatabase() {
  const data = await jsonFetch('/escola/database');
  renderDatabase(data.output || data);
}

async function importDatabase() {
  try {
    $('importDbBtn').disabled = true;
    setStatus('Importando BD NoSQL');
    const result = await jsonFetch('/escola/database/import', {
      method: 'POST',
      body: JSON.stringify({path: $('dbPathInput').value.trim(), name: 'facultad_negocios'})
    });
    renderDatabase({active: true, stats: result.output?.stats || {}});
    setStatus('BD NoSQL integrada');
  } catch (error) {
    setStatus(`BD: ${error.message}`);
  } finally {
    $('importDbBtn').disabled = false;
  }
}

function addMessage(role, content) {
  const article = document.createElement('article');
  article.className = `message ${role}`;
  article.innerHTML = `
    <div class="avatar">${role === 'user' ? 'T' : 'E'}</div>
    <div class="message-body">${content}</div>
  `;
  $('chatStream').appendChild(article);
  $('chatStream').scrollTop = $('chatStream').scrollHeight;
}

function renderQuery(payload) {
  const result = payload.output || payload;
  const answer = result.answer || {};
  const evidence = result.evidence || [];
  lastCopy = result.copy_ready || '';
  const copyId = responseCopies.push(lastCopy) - 1;
  lastEvidence = evidence;
  renderStats(result.stats || {});
  renderEvidence(evidence);
  addMessage('assistant', renderAssistantMarkdown(result, answer, evidence, copyId));
  setStatus('Respuesta lista');
}

function renderAssistantMarkdown(result, answer, evidence, copyId) {
  const pending = answer.pending?.length ? answer.pending.join(', ') : 'Ninguno';
  return `
    <div class="markdown-view">
      <h2>ESCOLA</h2>
      <p>${esc(answer.summary || 'Respuesta generada con la base documental.')}</p>
      <h3>Respuesta</h3>
      <table>
        <tbody>
          <tr><th>Consulta</th><td>${esc(result.question || '--')}</td></tr>
          <tr><th>Respuesta</th><td>${renderResponseList(answer.response || '--')}</td></tr>
          <tr><th>Confianza</th><td>${esc(answer.confidence || '--')}</td></tr>
          <tr><th>Pendientes</th><td>${esc(pending)}</td></tr>
        </tbody>
      </table>
      <div class="inline-actions">
        <button class="small-action icon-action" type="button" data-copy-response="${copyId}" title="Copiar respuesta" aria-label="Copiar respuesta">⧉</button>
        <button class="small-action" type="button" data-open-evidence>Ver evidencia (${evidence.length})</button>
      </div>
    </div>
  `;
}

function renderResponseList(value) {
  const lines = String(value || '').split('\n').map(line => line.trim()).filter(Boolean);
  if (!lines.length) return '--';
  if (lines.every(line => line.startsWith('- '))) {
    return `<ul>${lines.map(line => `<li>${esc(line.replace(/^- /, ''))}</li>`).join('')}</ul>`;
  }
  return esc(lines.join('\n'));
}

function renderEvidence(evidence) {
  $('evidenceState').textContent = `${evidence.length} fragmentos`;
  $('evidenceList').innerHTML = evidence.length ? evidence.map(item => `
    <div class="evidence-item">
      <small>${esc(item.filename || item.document_id)} · score ${esc(item.score)}</small>
      <p>${esc(short(item.preview, 520))}</p>
    </div>
  `).join('') : '<div class="empty">Sin evidencia.</div>';
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

async function handleQuery(event) {
  event.preventDefault();
  try {
    const question = $('questionInput').value.trim();
    if (!question) throw new Error('Escribe una consulta');
    addMessage('user', `<div class="user-text">${esc(question)}</div>`);
    $('questionInput').value = '';
    autosizeQuestion();
    $('queryBtn').disabled = true;
    setStatus('Consultando ESCOLA');
    const result = await jsonFetch('/escola/query', {
      method: 'POST',
      body: JSON.stringify({question, top_k: Number($('topK').value || 6)})
    });
    renderQuery(result);
  } catch (error) {
    setStatus(`Error: ${error.message}`);
    addMessage('assistant', `<div class="markdown-view"><h2>Error</h2><p>${esc(error.message)}</p></div>`);
  } finally {
    $('queryBtn').disabled = false;
  }
}

async function copyOutput() {
  if (!lastCopy) return;
  await navigator.clipboard.writeText(lastCopy);
  $('copyBtn').textContent = 'Copiado';
  setTimeout(() => { $('copyBtn').textContent = 'Copiar salida'; }, 1200);
}
async function copyResponse(index) {
  const text = responseCopies[Number(index)];
  if (!text) return;
  await navigator.clipboard.writeText(text);
  setStatus('Respuesta copiada');
}

function autosizeQuestion() {
  const input = $('questionInput');
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 170)}px`;
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
  $('refreshDbBtn').addEventListener('click', refreshDatabase);
  $('importDbBtn').addEventListener('click', importDatabase);
  $('queryForm').addEventListener('submit', handleQuery);
  $('copyBtn').addEventListener('click', copyOutput);
  $('openAdminBtn').addEventListener('click', () => openModal('adminModal'));
  $('openEvidenceBtn').addEventListener('click', () => {
    renderEvidence(lastEvidence);
    openModal('evidenceModal');
  });
  $('questionInput').addEventListener('input', autosizeQuestion);
  $('questionInput').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      $('queryForm').requestSubmit();
    }
  });
  document.addEventListener('click', event => {
    if (event.target.matches('[data-close-modal]')) closeModals();
    const copyButton = event.target.closest('[data-copy-response]');
    if (copyButton) copyResponse(copyButton.dataset.copyResponse);
    if (event.target.matches('[data-open-evidence]')) openModal('evidenceModal');
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeModals();
  });
  refreshDocuments().catch(error => {
    setStatus(`ESCOLA: ${error.message}`);
  });
  refreshDatabase().catch(() => {});
}

bind();
