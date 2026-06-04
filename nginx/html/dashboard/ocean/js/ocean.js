(function () {
  const API_BASE = "/harness-api/v1/ocean";
  const TOKEN_KEY = "quantlabs_ocean_tokens_v1";

  const $ = (id) => document.getElementById(id);
  const state = {
    tokens: {},
    busy: false,
    controller: null,
    messages: new Map(),
    nextId: 1,
  };

  const els = {
    provider: $("providerSelect"),
    model: $("modelInput"),
    agent: $("agentSelect"),
    temp: $("temperatureInput"),
    chat: $("chatLog"),
    form: $("chatForm"),
    prompt: $("promptInput"),
    send: $("sendBtn"),
    stop: $("stopBtn"),
    routerBtn: $("routerBtn"),
    telemetryBtn: $("telemetryBtn"),
    credentials: $("credentialsBtn"),
    clear: $("clearBtn"),
    modal: $("credentialsModal"),
    routerModal: $("routerModal"),
    telemetryModal: $("telemetryModal"),
    closeModal: $("closeModal"),
    closeRouterModal: $("closeRouterModal"),
    closeTelemetryModal: $("closeTelemetryModal"),
    saveTokens: $("saveTokens"),
    forgetTokens: $("forgetTokens"),
    openaiToken: $("openaiToken"),
    anthropicToken: $("anthropicToken"),
    deepseekToken: $("deepseekToken"),
    status: $("statusPill"),
    routeBadge: $("routeBadge"),
    modelBadge: $("modelBadge"),
    latencyBadge: $("latencyBadge"),
    lastAgent: $("lastAgent"),
    lastRoute: $("lastRoute"),
    lastModel: $("lastModel"),
    lastTokens: $("lastTokens"),
  };

  function readTokens() {
    try {
      return JSON.parse(localStorage.getItem(TOKEN_KEY) || "{}");
    } catch (_) {
      return {};
    }
  }

  function writeTokens(tokens) {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function usageText(usage) {
    if (!usage || typeof usage !== "object") return "--";
    const total = usage.total_tokens || usage.total || usage.output_tokens || usage.completion_tokens;
    return total ? String(total) : "--";
  }

  function providerToken() {
    const provider = els.provider.value;
    if (provider === "openai") return state.tokens.openai || "";
    if (provider === "anthropic") return state.tokens.anthropic || "";
    if (provider === "deepseek") return state.tokens.deepseek || "";
    return "";
  }

  function defaultModel(provider) {
    if (provider === "openai") return "gpt-4o-mini";
    if (provider === "anthropic") return "claude-3-5-haiku-20241022";
    if (provider === "deepseek") return "deepseek-chat";
    return "";
  }

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.dataset.kind = kind || "ready";
  }

  function actionButtons(role, incomplete) {
    if (role === "user") {
      return `
        <button class="message-action" type="button" data-action="retry" title="Volver a preguntar" aria-label="Volver a preguntar">↻</button>
        <button class="message-action" type="button" data-action="edit" title="Editar petición" aria-label="Editar petición">✎</button>
      `;
    }
    return `
      ${incomplete ? '<button class="message-action primary" type="button" data-action="continue" title="Continuar respuesta" aria-label="Continuar respuesta">⤵</button>' : ""}
      <button class="message-action" type="button" data-action="copy" title="Copiar respuesta" aria-label="Copiar respuesta">⧉</button>
    `;
  }

  function appendMessage(role, text, meta) {
    const id = String(state.nextId++);
    state.messages.set(id, { role, text, meta: meta || {} });
    const wrap = document.createElement("div");
    wrap.className = `message ${role}`;
    wrap.dataset.messageId = id;
    const label = role === "user" ? "Tu petición" : (meta && meta.agent ? meta.agent : "OCEAN");
    const extra = meta && meta.extra ? ` · ${escapeHtml(meta.extra)}` : "";
    const incomplete = Boolean(meta && meta.incomplete);
    wrap.innerHTML = `
      <div class="message-meta">
        <span>${escapeHtml(label)}</span><span>${extra}</span>
        <div class="message-actions">${actionButtons(role, incomplete)}</div>
      </div>
      <div class="bubble">${escapeHtml(text)}</div>
    `;
    els.chat.appendChild(wrap);
    els.chat.scrollTop = els.chat.scrollHeight;
    return id;
  }

  function welcome() {
    if (els.chat.children.length) return;
    appendMessage("assistant", "OCEAN está listo. Escribe una duda, argumento, interacción, problema complejo, meta de aprendizaje o pregunta de investigación; el router elegirá entre los seis agentes educativos o puedes fijarlo manualmente desde el panel de router.", {
      agent: "OCEAN",
      extra: "auto router",
    });
  }

  function fillModal() {
    els.openaiToken.value = state.tokens.openai || "";
    els.anthropicToken.value = state.tokens.anthropic || "";
    els.deepseekToken.value = state.tokens.deepseek || "";
  }

  function showModal() {
    fillModal();
    els.modal.classList.remove("hidden");
    els.openaiToken.focus();
  }

  function showPanelModal(modal, focusTarget) {
    modal.classList.remove("hidden");
    if (focusTarget) focusTarget.focus();
  }

  function hideModal(modal) {
    modal.classList.add("hidden");
  }

  async function loadMetadata() {
    try {
      const res = await fetch(`${API_BASE}/models`, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      els.modelBadge.textContent = data.local_model ? "LLM local" : "local";
      setStatus("OCEAN listo", "ready");
    } catch (err) {
      setStatus(`Sin metadata: ${err.message}`, "warn");
    }
  }

  function setBusy(value) {
    state.busy = value;
    els.send.disabled = value;
    els.stop.disabled = !value;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copiado", "ready");
    } catch (_) {
      setStatus("No se pudo copiar", "warn");
    }
  }

  async function sendMessage(message, options) {
    const provider = els.provider.value;
    const token = providerToken();
    if (provider !== "local" && !token) {
      showModal();
      setStatus("Falta token del proveedor", "warn");
      return;
    }

    setBusy(true);
    state.controller = new AbortController();
    setStatus("Pensando...", "busy");
    if (!options || options.appendUser !== false) {
      appendMessage("user", options && options.displayMessage ? options.displayMessage : message, { extra: provider, rawMessage: message });
    }

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          provider,
          token,
          model: els.model.value.trim(),
          agent: options && options.agent ? options.agent : els.agent.value,
          temperature: Number(els.temp.value || 0.45),
          max_tokens: options && options.maxTokens ? options.maxTokens : 90,
        }),
        signal: state.controller.signal,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail ? `: ${data.detail}` : "";
        throw new Error(`${data.error || "HTTP " + res.status}${detail}`);
      }

      appendMessage("assistant", data.response || "", {
        agent: data.agent && data.agent.label ? data.agent.label : data.agent_type,
        extra: `${data.route_source || "router"} · ${data.elapsed_ms || "--"} ms${data.incomplete ? " · incompleta" : ""}`,
        incomplete: data.incomplete,
        agentType: data.agent_type,
        originalPrompt: message,
      });
      els.routeBadge.textContent = data.agent_type || "AUTO";
      els.modelBadge.textContent = data.provider || provider;
      els.latencyBadge.textContent = `${data.elapsed_ms || "--"} ms`;
      els.lastAgent.textContent = data.agent && data.agent.label ? data.agent.label : data.agent_type || "--";
      els.lastRoute.textContent = data.route_source || "--";
      els.lastModel.textContent = data.model || els.model.value || "local";
      els.lastTokens.textContent = usageText(data.usage);
      setStatus("Respuesta lista", "ready");
    } catch (err) {
      if (err.name === "AbortError") {
        appendMessage("assistant", "Petición detenida por el usuario.", {
          agent: "OCEAN",
          extra: "stop",
        });
        setStatus("Petición detenida", "warn");
        return;
      }
      appendMessage("assistant", `No fue posible consultar OCEAN: ${err.message}`, {
        agent: "Error",
        extra: "revisar proveedor",
      });
      setStatus("Error de consulta", "error");
    } finally {
      state.controller = null;
      setBusy(false);
      els.prompt.focus();
    }
  }

  function bindEvents() {
    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      const message = els.prompt.value.trim();
      if (!message || state.busy) return;
      els.prompt.value = "";
      sendMessage(message);
    });

    els.stop.addEventListener("click", () => {
      if (state.controller) state.controller.abort();
    });

    els.chat.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button || state.busy) return;
      const wrap = button.closest(".message");
      const item = wrap ? state.messages.get(wrap.dataset.messageId) : null;
      if (!item) return;
      const action = button.dataset.action;
      const raw = item.meta && item.meta.rawMessage ? item.meta.rawMessage : item.text;

      if (action === "copy") {
        copyText(item.text);
      } else if (action === "retry") {
        sendMessage(raw);
      } else if (action === "edit") {
        els.prompt.value = raw;
        els.prompt.focus();
      } else if (action === "continue") {
        const prompt = [
          "Continúa exactamente desde donde se cortó tu respuesta anterior.",
          "No repitas lo ya dicho, conserva el mismo formato y termina la idea pendiente.",
          "",
          "Petición original:",
          item.meta.originalPrompt || "",
          "",
          "Respuesta anterior incompleta:",
          item.text,
        ].join("\n");
        sendMessage(prompt, {
          displayMessage: "Continúa tu respuesta anterior.",
          agent: item.meta.agentType || els.agent.value,
          maxTokens: 110,
        });
      }
    });

    els.provider.addEventListener("change", () => {
      if (!els.model.value.trim()) els.model.value = defaultModel(els.provider.value);
      els.modelBadge.textContent = els.provider.value === "local" ? "local" : els.provider.value;
    });

    els.routerBtn.addEventListener("click", () => showPanelModal(els.routerModal, els.provider));
    els.telemetryBtn.addEventListener("click", () => showPanelModal(els.telemetryModal, els.closeTelemetryModal));
    els.credentials.addEventListener("click", showModal);
    els.closeModal.addEventListener("click", () => hideModal(els.modal));
    els.closeRouterModal.addEventListener("click", () => hideModal(els.routerModal));
    els.closeTelemetryModal.addEventListener("click", () => hideModal(els.telemetryModal));
    els.modal.addEventListener("click", (event) => {
      if (event.target === els.modal) hideModal(els.modal);
    });
    els.routerModal.addEventListener("click", (event) => {
      if (event.target === els.routerModal) hideModal(els.routerModal);
    });
    els.telemetryModal.addEventListener("click", (event) => {
      if (event.target === els.telemetryModal) hideModal(els.telemetryModal);
    });
    els.clear.addEventListener("click", () => {
      els.chat.innerHTML = "";
      state.messages.clear();
      welcome();
    });
    els.saveTokens.addEventListener("click", () => {
      state.tokens = {
        openai: els.openaiToken.value.trim(),
        anthropic: els.anthropicToken.value.trim(),
        deepseek: els.deepseekToken.value.trim(),
      };
      writeTokens(state.tokens);
      hideModal(els.modal);
      setStatus("Credenciales guardadas", "ready");
    });
    els.forgetTokens.addEventListener("click", () => {
      state.tokens = {};
      localStorage.removeItem(TOKEN_KEY);
      fillModal();
      setStatus("Credenciales borradas", "warn");
    });
  }

  state.tokens = readTokens();
  bindEvents();
  welcome();
  loadMetadata();
})();
