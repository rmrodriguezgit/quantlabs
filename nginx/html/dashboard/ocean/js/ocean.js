(function () {
  const API_BASE = "/harness-api/v1/ocean";
  const TOKEN_KEY = "quantlabs_ocean_tokens_v1";

  const $ = (id) => document.getElementById(id);
  const state = {
    tokens: {},
    busy: false,
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
    credentials: $("credentialsBtn"),
    clear: $("clearBtn"),
    modal: $("credentialsModal"),
    closeModal: $("closeModal"),
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

  function appendMessage(role, text, meta) {
    const wrap = document.createElement("div");
    wrap.className = `message ${role}`;
    const label = role === "user" ? "Tu petición" : (meta && meta.agent ? meta.agent : "OCEAN");
    const extra = meta && meta.extra ? ` · ${escapeHtml(meta.extra)}` : "";
    wrap.innerHTML = `
      <div class="message-meta"><span>${escapeHtml(label)}</span><span>${extra}</span></div>
      <div class="bubble">${escapeHtml(text)}</div>
    `;
    els.chat.appendChild(wrap);
    els.chat.scrollTop = els.chat.scrollHeight;
  }

  function welcome() {
    if (els.chat.children.length) return;
    appendMessage("assistant", "OCEAN está listo. Escribe una duda, un argumento o un problema complejo; el router elegirá el agente adecuado o puedes fijarlo manualmente.", {
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

  function hideModal() {
    els.modal.classList.add("hidden");
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

  async function sendMessage(message) {
    const provider = els.provider.value;
    const token = providerToken();
    if (provider !== "local" && !token) {
      showModal();
      setStatus("Falta token del proveedor", "warn");
      return;
    }

    state.busy = true;
    els.send.disabled = true;
    els.send.textContent = "Enviando";
    setStatus("Pensando...", "busy");
    appendMessage("user", message, { extra: provider });

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
          agent: els.agent.value,
          temperature: Number(els.temp.value || 0.45),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail ? `: ${data.detail}` : "";
        throw new Error(`${data.error || "HTTP " + res.status}${detail}`);
      }

      appendMessage("assistant", data.response || "", {
        agent: data.agent && data.agent.label ? data.agent.label : data.agent_type,
        extra: `${data.route_source || "router"} · ${data.elapsed_ms || "--"} ms`,
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
      appendMessage("assistant", `No fue posible consultar OCEAN: ${err.message}`, {
        agent: "Error",
        extra: "revisar proveedor",
      });
      setStatus("Error de consulta", "error");
    } finally {
      state.busy = false;
      els.send.disabled = false;
      els.send.textContent = "Enviar";
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

    els.provider.addEventListener("change", () => {
      if (!els.model.value.trim()) els.model.value = defaultModel(els.provider.value);
      els.modelBadge.textContent = els.provider.value === "local" ? "local" : els.provider.value;
    });

    els.credentials.addEventListener("click", showModal);
    els.closeModal.addEventListener("click", hideModal);
    els.modal.addEventListener("click", (event) => {
      if (event.target === els.modal) hideModal();
    });
    els.clear.addEventListener("click", () => {
      els.chat.innerHTML = "";
      welcome();
    });
    els.saveTokens.addEventListener("click", () => {
      state.tokens = {
        openai: els.openaiToken.value.trim(),
        anthropic: els.anthropicToken.value.trim(),
        deepseek: els.deepseekToken.value.trim(),
      };
      writeTokens(state.tokens);
      hideModal();
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
