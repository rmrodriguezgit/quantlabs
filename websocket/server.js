const WebSocket = require("ws");
const http      = require("http");

const PORT    = process.env.PORT || 3000;
const LLM_HOST = "llm";
const LLM_PORT = 8080;
const LLM_PATH = "/completion";

const wss = new WebSocket.Server({ port: PORT });
console.log(`WebSocket server running on port ${PORT}`);
console.log(`LLM directo: http://${LLM_HOST}:${LLM_PORT}${LLM_PATH}`);

// ── Llamada directa a llama.cpp ──────────────────────────────────
function askLLM(prompt) {
      return new Promise((resolve, reject) => {
        const body = JSON.stringify({
          prompt,
          n_predict:   350,
          temperature: 0.3,
          top_p:       0.9,
          top_k:       40,
          stop:        ["</s>", "###", "Usuario:", "Human:"]  // ← sin [INST]
        });

    const options = {
      hostname: LLM_HOST,
      port:     LLM_PORT,
      path:     LLM_PATH,
      method:   "POST",
      headers: {
        "Content-Type":   "application/json",
        "Content-Length": Buffer.byteLength(body)
      }
    };

    const req = http.request(options, (res) => {
      let data = "";
      res.on("data",  chunk => data += chunk);
      res.on("end", () => {
        try {
          const json = JSON.parse(data);
          const content = (json.content || "").trim();
          resolve(content || "Sin respuesta");
        } catch (e) {
          reject(new Error("Parse error: " + data.slice(0, 200)));
        }
      });
    });

    req.on("error", reject);
    req.setTimeout(120000, () => {
      req.destroy();
      reject(new Error("LLM timeout (120s)"));
    });

    req.write(body);
    req.end();
  });
}

// ── WebSocket handler ────────────────────────────────────────────
wss.on("connection", (ws, req) => {
  console.log("✅ Cliente conectado:", req.socket.remoteAddress);

  ws.send(JSON.stringify({
    type:    "welcome",
    message: "WebSocket QuantLab funcionando"
  }));

  ws.on("message", async (raw) => {
    let data;
    try {
      data = JSON.parse(raw.toString());
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "JSON inválido" }));
      return;
    }

    console.log(`📨 tipo: ${data.type} | símbolo: ${data.symbol || "-"}`);

    // ── Señal rápida ───────────────────────────────────────────
    if (data.type === "llm_query") {
              const { symbol = "BTC", rsi = "N/A", macd = "N/A", price_vs_vwap = "N/A" } = data;
              ws.send(JSON.stringify({ type: "thinking", message: `Analizando ${symbol}...` }));

              const prompt =
                `### Instrucción:\n` +
                `Eres un analista cuantitativo. Genera una señal técnica para ${symbol}.\n` +
                `RSI: ${rsi} | MACD: ${macd} | Precio vs VWAP: ${price_vs_vwap}\n\n` +
                `Responde EXACTAMENTE así:\n` +
                `SEÑAL: LONG o SHORT o WAIT\n` +
                `CONFIANZA: número del 0 al 100\n` +
                `RAZÓN: una línea\n` +
                `RIESGO: bajo o medio o alto\n\n` +
                `### Respuesta:\n`;

      try {
        const content = await askLLM(prompt);
        console.log(`✅ LLM response (${symbol}):`, content.slice(0, 80));
        ws.send(JSON.stringify({
          type:      "llm_response",
          symbol,
          signal:    content,
          timestamp: new Date().toISOString()
        }));
      } catch (err) {
        console.error("❌ LLM error:", err.message);
        ws.send(JSON.stringify({ type: "error", message: err.message }));
      }

    // ── Análisis completo ──────────────────────────────────────
    } else if (data.type === "analyze") {
              const { symbol = "BTC", context = "Sin datos" } = data;
              ws.send(JSON.stringify({ type: "thinking", message: `Analizando ${symbol}...` }));

              const prompt =
                `### Instrucción:\n` +
                `Actúa como analista cuantitativo profesional. Responde en español.\n` +
                `Activo: ${symbol}\n` +
                `Contexto: ${context}\n\n` +
                `Devuelve:\n` +
                `1. Lectura técnica\n` +
                `2. Riesgo\n` +
                `3. Señal: LONG, SHORT o WAIT\n` +
                `4. Justificación breve\n` +
                `5. Confianza 0-100\n\n` +
                `### Respuesta:\n`;

      try {
        const content = await askLLM(prompt);
        ws.send(JSON.stringify({
          type:      "analysis",
          symbol,
          content,
          timestamp: new Date().toISOString()
        }));
      } catch (err) {
        ws.send(JSON.stringify({ type: "error", message: err.message }));
      }

    // ── Ping / echo ────────────────────────────────────────────
    } else {
      ws.send(JSON.stringify({
        type:      "echo",
        message:   raw.toString(),
        timestamp: new Date().toISOString()
      }));
    }
  });

  ws.on("close", () => console.log("🔌 Cliente desconectado"));
  ws.on("error", (e) => console.error("WS error:", e.message));
});
