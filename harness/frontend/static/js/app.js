const token = localStorage.token || '';
const headers = {'Authorization': `Bearer ${token}`};
const chatButton = chatForm.querySelector('button');
let pendingRequest = null;

function nowTime() {
  return new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

function addMessage(kind, text) {
  const item = document.createElement('div');
  item.className = `message ${kind}`;
  item.textContent = `[${nowTime()}] ${text}`;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function setStatus(text, kind = 'idle') {
  requestStatus.textContent = text;
  requestStatus.className = `status ${kind}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {headers, ...options});
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = {error: text || 'invalid_json'};
  }
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function get(path) {
  return requestJson(path);
}

async function refresh() {
  try {
    const a = await get('/v1/agents');
    agents.innerHTML = a.agents.map(x => `<li>${x}</li>`).join('');
    agent.innerHTML = a.agents.map(x => `<option>${x}</option>`).join('');

    const t = await get('/v1/tools');
    tools.innerHTML = t.tools.map(x => `<li>${x}</li>`).join('');

    system.textContent = JSON.stringify(await get('/v1/system'), null, 2);
    const m = await get('/v1/memory?session_id=default');
    memory.textContent = JSON.stringify({summary: m.summary, messages: m.messages?.length}, null, 2);
    tasks.innerHTML = (m.tasks || []).map(x => `<li>${x.agent}: ${x.status}</li>`).join('');
    artifacts.innerHTML = (m.artifacts || []).map(x => `<li>${x}</li>`).join('');

    const latest = (m.tasks || []).at(-1);
    if (pendingRequest && latest?.status === 'running') {
      setStatus('Prompt recibido por el Harness. Procesando respuesta...', 'busy');
    }
  } catch (error) {
    setStatus(`No se pudo actualizar estado: ${error.message}`, 'error');
  }
}

function setFormBusy(isBusy) {
  chatButton.disabled = isBusy;
  prompt.disabled = isBusy;
  agent.disabled = isBusy;
}

chatForm.onsubmit = async event => {
  event.preventDefault();
  const message = prompt.value.trim();
  if (!message || pendingRequest) {
    return;
  }

  prompt.value = '';
  addMessage('user', `> ${message}`);
  const controller = new AbortController();
  pendingRequest = {controller, startedAt: Date.now()};
  setFormBusy(true);
  setStatus('Enviando prompt al Harness...', 'busy');

  const processingNotice = setTimeout(() => {
    if (pendingRequest) {
      setStatus('Prompt enviado. Esperando respuesta del agente...', 'busy');
    }
  }, 1200);

  const timeout = setTimeout(() => controller.abort(), 180000);

  try {
    const response = await requestJson('/v1/chat', {
      method: 'POST',
      headers: {...headers, 'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: 'default', message, agent: agent.value}),
      signal: controller.signal,
    });
    addMessage('assistant', response.response || 'Respuesta recibida sin contenido.');
    setStatus('Respuesta recibida.', 'ok');
  } catch (error) {
    const isTimeout = error.name === 'AbortError';
    addMessage('error', isTimeout ? 'La petición tardó demasiado y fue cancelada en el navegador.' : `No se pudo completar: ${error.message}`);
    setStatus(isTimeout ? 'Tiempo de espera agotado. Revisa tareas o reintenta.' : `Petición fallida: ${error.message}`, 'error');
  } finally {
    clearTimeout(processingNotice);
    clearTimeout(timeout);
    pendingRequest = null;
    setFormBusy(false);
    refresh();
  }
};

refresh();
setInterval(refresh, 5000);
