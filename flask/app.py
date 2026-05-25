from collections import OrderedDict
from threading import RLock

from flask import Flask, g, request, jsonify
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
import requests
import functools
import os

load_dotenv()

app = Flask(__name__)
HTTP_POOL_SIZE = int(os.getenv("HTTP_POOL_SIZE", "32"))
MARKET_CACHE_TTL = int(os.getenv("MARKET_CACHE_TTL", "45"))
PORTFOLIO_CACHE_TTL = int(os.getenv("PORTFOLIO_CACHE_TTL", "180"))
MAX_CACHE_ITEMS = int(os.getenv("MAX_CACHE_ITEMS", "128"))

http = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=HTTP_POOL_SIZE,
    pool_maxsize=HTTP_POOL_SIZE,
    max_retries=0,
)
http.mount("http://", adapter)
http.mount("https://", adapter)


class TTLCache:
    def __init__(self, ttl_seconds, max_items=128):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items = OrderedDict()
        self._lock = RLock()

    def get(self, key):
        now = time.perf_counter()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value.copy(deep=True) if hasattr(value, "copy") else value

    def set(self, key, value):
        with self._lock:
            self._items[key] = (
                time.perf_counter() + self.ttl_seconds,
                value.copy(deep=True) if hasattr(value, "copy") else value,
            )
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


market_data_cache = TTLCache(MARKET_CACHE_TTL, MAX_CACHE_ITEMS)
portfolio_close_cache = TTLCache(PORTFOLIO_CACHE_TTL, MAX_CACHE_ITEMS)

@app.before_request
def log_request():
    if not app.debug:
        return
    app.logger.debug("REQUEST: %s %s", request.method, request.path)


socketio = SocketIO(                               # ← nuevo
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    path="/socket.io"
)


# ─── Config ─────────────────────────────────────────────────────────────────
LLM_URL    = os.getenv("LLM_URL", "http://llm:8080/completion")
API_TOKENS = set(os.getenv("API_TOKENS", "").split(","))

if not any(API_TOKENS):
    raise RuntimeError("❌ API_TOKENS no definido en .env")

# ─── Auth ─────────────────────────────────────────────────────────────────
def require_token(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Token requerido"}), 401
        if token not in API_TOKENS:
            return jsonify({"error": "Token inválido o expirado"}), 403
        return f(*args, **kwargs)
    return decorated
# ─── LLM Helper ─────────────────────────────────────────────────────────────
def ask_llm(prompt, n_predict=300):
    r = http.post(LLM_URL, json={
        "prompt":      prompt,
        "n_predict":   n_predict,
        "temperature": 0.4,
        "top_p":       0.9
    }, timeout=120)
    r.raise_for_status()
    return r.json()

# ─── Rutas originales ────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"service": "QuantLab Trading Engine",
                    "status": "running", "version": "1.0"})

@app.route("/analyze", methods=["POST"])
@require_token
def analyze():
    data    = request.get_json()
    symbol  = data.get("symbol", "BTC")
    context = data.get("context", "Sin datos proporcionados")
    prompt = f"""[INST] Actúa como analista cuantitativo profesional.
Activo: {symbol}
Contexto: {context}
Devuelve en español:
1. Lectura técnica
2. Riesgo
3. Señal: LONG, SHORT o WAIT
4. Justificación
5. Confianza 0-100
No des recomendaciones financieras absolutas. [/INST]"""
    result = ask_llm(prompt, n_predict=400)
    return jsonify({
        "symbol":           symbol,
        "analysis":         result.get("content", ""),
        "tokens_predicted": result.get("tokens_predicted")
    })

@app.route("/signal", methods=["POST"])
@require_token
def signal():
    data          = request.get_json()
    symbol        = data.get("symbol", "BTC")
    rsi           = data.get("rsi")
    macd          = data.get("macd")
    price_vs_vwap = data.get("price_vs_vwap")
    prompt = f"""[INST] Señal técnica para {symbol}:
RSI: {rsi} | MACD: {macd} | Precio vs VWAP: {price_vs_vwap}
Responde SOLO así:
SEÑAL: LONG / SHORT / WAIT
CONFIANZA: 0-100
RAZÓN: breve
RIESGO: bajo / medio / alto [/INST]"""
    result = ask_llm(prompt, n_predict=250)
    return jsonify({"symbol": symbol, "signal": result.get("content", "")})

# ─── OpenAI-Compatible (para OpenClaw / Mac Mini) ────────────────────────────
def _messages_to_prompt(messages):
    prompt = ""
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            prompt += f"<|im_start|>system\n{content}<|im_end|>\n"
        elif role == "user":
            prompt += f"<|im_start|>user\n{content}<|im_end|>\n"
        elif role == "assistant":
            prompt += f"<|im_start|>assistant\n{content}<|im_end|>\n"
    prompt += "<|im_start|>assistant\n"
    return prompt

@app.route("/v1/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "QuantLab LLM Gateway"})

@app.route("/v1/models", methods=["GET"])
@require_token
def list_models():
    return jsonify({
        "object": "list",
        "data": [{"id": "qwen", "object": "model",
                  "owned_by": "quantlab", "created": 1700000000}]
    })

@app.route("/v1/chat/completions", methods=["POST"])
@require_token
def chat_completions():
    import time
    data      = request.get_json()
    messages  = data.get("messages", [])
    n_predict = data.get("max_tokens", 512)
    prompt    = _messages_to_prompt(messages)
    result    = ask_llm(prompt, n_predict=n_predict)
    content   = result.get("content", "")
    return jsonify({
        "id":      "chatcmpl-quantlab",
        "object":  "chat.completion",
        "created": int(time.time()),          # ← campo requerido
        "model":   data.get("model", "qwen"),
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": content},
            "finish_reason": "stop",
            "logprobs":      None             # ← campo requerido
        }],
        "usage": {
            "prompt_tokens":     result.get("tokens_evaluated", 0),
            "completion_tokens": result.get("tokens_predicted", 0),
            "total_tokens":      result.get("tokens_evaluated", 0) +
                                 result.get("tokens_predicted", 0)
        }
    })

@app.route("/v1/messages", methods=["POST"])
@require_token
def anthropic_messages():
    import json
    import time
    data = request.get_json() or {}
    messages = data.get("messages", [])
    n_predict = data.get("max_tokens", 512)

    normalized_messages = []
    system = data.get("system", "")
    if isinstance(system, list):
        system = "\n".join(
            part.get("text", "") for part in system
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if system:
        normalized_messages.append({"role": "system", "content": system})

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            content = "\n".join(text_parts)
        normalized_messages.append({
            "role": msg.get("role", "user"),
            "content": content,
        })

    prompt = _messages_to_prompt(normalized_messages)
    result = ask_llm(prompt, n_predict=n_predict)
    content = result.get("content", "")
    input_tokens = result.get("tokens_evaluated", 0)
    output_tokens = result.get("tokens_predicted", 0)
    model = data.get("model", "qwen")
    message_id = f"msg_quantlab_{int(time.time())}"

    if data.get("stream"):
        def event(name, payload):
            return f"event: {name}\ndata: {json.dumps(payload)}\n\n"

        def generate():
            yield event("message_start", {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                },
            })
            yield event("content_block_start", {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            yield event("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": content},
            })
            yield event("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })
            yield event("message_stop", {"type": "message_stop"})

        return app.response_class(generate(), mimetype="text/event-stream")

    return jsonify({
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    })

@app.route("/v1/messages/count_tokens", methods=["POST"])
@require_token
def anthropic_count_tokens():
    data = request.get_json() or {}
    messages = data.get("messages", [])
    text = ""
    system = data.get("system", "")
    if isinstance(system, list):
        system = " ".join(
            part.get("text", "") for part in system
            if isinstance(part, dict) and part.get("type") == "text"
        )
    text += f" {system}"
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        text += f" {content}"
    return jsonify({"input_tokens": max(1, len(text.split()))})

# ─── WebSocket: stream de señales en tiempo real ─────────────────────────────
@socketio.on("connect")
def on_connect():
    auth = request.args.get("token", "")
    if auth not in API_TOKENS:
        return False                               # rechaza la conexión

@socketio.on("subscribe_signal")
def handle_signal(data):
    symbol        = data.get("symbol", "BTC")
    rsi           = data.get("rsi")
    macd          = data.get("macd")
    price_vs_vwap = data.get("price_vs_vwap")
    prompt = f"""[INST] Señal técnica para {symbol}:
                RSI: {rsi} | MACD: {macd} | Precio vs VWAP: {price_vs_vwap}
                Responde SOLO así:
                SEÑAL: LONG / SHORT / WAIT
                CONFIANZA: 0-100
                RAZÓN: breve
                RIESGO: bajo / medio / alto [/INST]"""
    result  = ask_llm(prompt, n_predict=250)
    content = result.get("content", "")
    emit("signal_update", {
        "symbol":     symbol,
        "signal":     content,
        "timestamp":  __import__("time").time()
    })


# ─── API QuantLab: Market Data CPU/GPU ──────────────────────────────────────
import time
import yfinance as yf
import pandas as pd

GPU_MARKET_URL = os.getenv("GPU_MARKET_URL", "http://market_gpu:9000")
AUTH_URL = os.getenv("AUTH_URL", "http://auth:7000")
SYMBOL_ALIASES = {"BTC":"BTC-USD", "BRK.B":"BRK-B", "MNX=X":"MXN=X"}
TICKERS = {"WMT","AAPL","PLTR","MSFT","NVDA","GOOGL","AMZN","META","TSM","BRK-B","V","JPM","XOM","LLY","MRK","UNH","PG","MA","CVX","KO","PEP","COST","TMO","ORCL","CSCO","NKE","VZ","ASML","TXN","ABT","TM","SAP","AMD","NFLX","NOW","ADBE","LVMUY","BABA","SHEL","TMUS","QCOM","PFE","SNY","AZN","TOT","GSK","RIO","BHP","MCD","HWM","WM","ADMA","ALMU","AVGO","ASTS","BE","DAVE","POWL","BTC-USD","ETH-USD","USDT-USD","XRP-USD","LTC-USD","ADA-USD","DOT-USD","BCH-USD","XLM-USD","LINK-USD","EURUSD=X","MXN=X","USDMXN=X","CADMXN=X","JPYMXN=X","EURMXN=X","GBPMXN=X","GBPUSD=X","USDJPY=X","EURJPY=X","AUDUSD=X","CADUSD=X","SPY","QQQ","TSLA","GC=F","CL=F"}
def _normalize_symbol(symbol):
    return SYMBOL_ALIASES.get(symbol.upper(), symbol.upper())
PERIODS = {"1d","5d","7d","1mo","3mo","6mo","1y","2y","5y"}
INTERVALS = {"1m","5m","15m","30m","1h","1d","1wk"}

def _selected_user_assets():
    if not request.cookies:
        return None
    if hasattr(g, "_selected_user_assets"):
        return g._selected_user_assets
    try:
        r=http.get(f"{AUTH_URL}/auth/assets", cookies=request.cookies, timeout=3)
        if r.ok:
            g._selected_user_assets = set(r.json().get("selected", []))
            return g._selected_user_assets
    except requests.RequestException:
        pass
    g._selected_user_assets = None
    return None

def _validate_market_args(ticker, period, interval):
    errors = {}
    selected=_selected_user_assets()
    normalized_selected={_normalize_symbol(x) for x in selected} if selected is not None else None
    if normalized_selected is not None:
        if ticker not in normalized_selected: errors["ticker"] = "Ticker fuera de tu universo de activos"
    elif ticker not in TICKERS:
        errors["ticker"] = "Ticker no permitido"
    if period not in PERIODS: errors["period"] = "Periodo no permitido"
    if interval not in INTERVALS: errors["interval"] = "Intervalo no permitido"
    return errors

def _records(df):
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    out = out.astype(object).where(pd.notnull(out), None)
    return out.to_dict("records")

def get_market_data_cpu(ticker="BTC-USD", period="7d", interval="5m"):
    cache_key = (ticker, period, interval)
    cached = market_data_cache.get(cache_key)
    if cached is not None:
        return cached
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if df.empty:
        return df
    df = df.reset_index()
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    market_data_cache.set(cache_key, df)
    return df

def _add_indicators(df):
    df = df.copy()
    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["RSI14"] = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACDSignal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACDHist"] = df["MACD"] - df["MACDSignal"]
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["BBMid"] = mid
    df["BBUpper"] = mid + (2 * std)
    df["BBLower"] = mid - (2 * std)
    typical = (high + low + close) / 3
    vol_cum = volume.cumsum().replace(0, pd.NA)
    df["VWAP"] = (typical * volume).cumsum() / vol_cum
    return df

def _market_cpu_payload(ticker, period, interval):
    t0 = time.perf_counter()
    df = get_market_data_cpu(ticker, period, interval)
    if df.empty:
        return None
    df = _add_indicators(df)
    close = df["Close"]
    last = df.iloc[-1]
    change_pct = ((close.iloc[-1] / close.iloc[0]) - 1) * 100 if len(close) else None
    return {
        "ticker": ticker, "period": period, "interval": interval, "mode": "cpu",
        "rows": int(len(df)), "latency_ms": round((time.perf_counter()-t0)*1000, 2),
        "metrics": {"last": float(close.iloc[-1]), "high": float(df["High"].max()), "low": float(df["Low"].min()), "volume": float(df["Volume"].sum()), "change_pct": float(change_pct), "rsi": None if pd.isna(last["RSI14"]) else float(last["RSI14"]), "macd": None if pd.isna(last["MACD"]) else float(last["MACD"]), "vwap": None if pd.isna(last["VWAP"]) else float(last["VWAP"])},
        "data": _records(df.tail(250))
    }

@app.route("/health", methods=["GET"])
def api_health():
    gpu = {"status": "unknown"}
    try:
        gpu = http.get(f"{GPU_MARKET_URL}/health", timeout=3).json()
    except Exception as exc:
        gpu = {"status": "unavailable", "detail": str(exc)}
    return jsonify({"status":"ok","service":"QuantLab API","cpu":"pandas","gpu":gpu})

@app.route("/market", methods=["GET"])
def market():
    mode = request.args.get("mode", "cpu").lower()
    if mode == "gpu":
        return market_gpu()
    return market_cpu()

@app.route("/market/cpu", methods=["GET"])
def market_cpu():
    ticker = _normalize_symbol(request.args.get("ticker", "BTC-USD"))
    period = request.args.get("period", "7d")
    interval = request.args.get("interval", "5m")
    errors = _validate_market_args(ticker, period, interval)
    if errors: return jsonify({"error":"Parámetros inválidos","fields":errors}), 400
    payload = _market_cpu_payload(ticker, period, interval)
    if payload is None: return jsonify({"error":"Sin datos para la consulta"}), 404
    return jsonify(payload)

@app.route("/market/gpu", methods=["GET"])
def market_gpu():
    ticker = _normalize_symbol(request.args.get("ticker", "BTC-USD"))
    period = request.args.get("period", "7d")
    interval = request.args.get("interval", "5m")
    errors = _validate_market_args(ticker, period, interval)
    if errors: return jsonify({"error":"Parámetros inválidos","fields":errors}), 400
    try:
        r = http.get(f"{GPU_MARKET_URL}/market", params={"ticker":ticker,"period":period,"interval":interval}, timeout=45)
        return jsonify(r.json()), r.status_code
    except requests.RequestException as exc:
        return jsonify({"error":"GPU no disponible","detail":str(exc)}), 503

@app.route("/btc", methods=["GET"])
def btc_shortcut():
    args = request.args.to_dict()
    args["ticker"] = "BTC-USD"
    with app.test_request_context(query_string=args):
        return market()


# ─── Prophet Forecasting ─────────────────────────────────────────────────────
from prophet import Prophet

STYLE_CONFIG = {
    "scalping": {"interval": "5m", "period": "5d", "steps": 3, "freq": "5min", "label": "Scalping"},
    "day_trading": {"interval": "15m", "period": "5d", "steps": 4, "freq": "15min", "label": "Day Trading"},
    "swing_trading": {"interval": "1h", "period": "3mo", "steps": 5, "freq": "1h", "label": "Swing Trading"},
    "position_trading": {"interval": "1d", "period": "2y", "steps": 5, "freq": "1D", "label": "Position Trading"},
    "long_term": {"interval": "1d", "period": "5y", "steps": 10, "freq": "1D", "label": "Inversión a largo plazo"}
}

def _atr(df, n=14):
    tr = pd.concat([(df["High"]-df["Low"]), (df["High"]-df["Close"].shift()).abs(), (df["Low"]-df["Close"].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def _forecast_features(df):
    out = _add_indicators(df)
    out["ATR14"] = _atr(out)
    return out.dropna(subset=["Close", "MACD", "RSI14", "ATR14", "VWAP"])

def _simulate_future_ohlcv(df, steps, freq):
    hist = df.copy()
    time_col = "Datetime" if "Datetime" in hist.columns else "Date"
    hist[time_col] = pd.to_datetime(hist[time_col], utc=True).dt.tz_convert(None)
    recent = hist.tail(min(len(hist), 60)).copy()
    close = recent["Close"]
    drift = close.pct_change().dropna().median()
    atr_series = _atr(hist).dropna()
    atr_now = float(atr_series.iloc[-1]) if not atr_series.empty else float((recent["High"]-recent["Low"]).median())
    vol_med = float(recent["Volume"].tail(20).median()) if "Volume" in recent else 0.0
    future_times = pd.date_range(hist[time_col].iloc[-1], periods=steps+1, freq=freq)[1:]
    rows=[]; prev_close=float(hist["Close"].iloc[-1])
    for ts in future_times:
        open_=prev_close
        close_=prev_close*(1+float(drift))
        body=abs(close_-open_)
        spread=max(atr_now, body*1.5, prev_close*0.001)
        high=max(open_, close_) + spread*0.5
        low=min(open_, close_) - spread*0.5
        rows.append({time_col:ts,"Open":open_,"High":high,"Low":low,"Close":close_,"Adj Close":close_,"Volume":vol_med})
        prev_close=close_
    return pd.DataFrame(rows)

@app.route("/forecast/prophet", methods=["GET"])
def forecast_prophet():
    ticker = _normalize_symbol(request.args.get("ticker", "BTC-USD"))
    style = request.args.get("style", "scalping").lower()
    selected=_selected_user_assets()
    if selected is not None:
        if ticker not in {_normalize_symbol(x) for x in selected}:
            return jsonify({"error":"Ticker fuera de tu universo de activos"}), 400
    elif ticker not in TICKERS:
        return jsonify({"error":"Ticker no permitido"}), 400
    if style not in STYLE_CONFIG:
        return jsonify({"error":"Estilo inválido", "styles": list(STYLE_CONFIG)}), 400
    cfg = STYLE_CONFIG[style]
    forecast_mode = request.args.get("forecast_mode", "carry_forward").lower()
    if forecast_mode not in ["carry_forward", "simulated_ohlcv"]:
        return jsonify({"error":"forecast_mode inválido", "modes":["carry_forward","simulated_ohlcv"]}), 400
    t0 = time.perf_counter()
    df = get_market_data_cpu(ticker, cfg["period"], cfg["interval"])
    if df.empty:
        return jsonify({"error":"Sin datos"}), 404
    df = _forecast_features(df)
    if len(df) < 60:
        return jsonify({"error":"Datos insuficientes para Prophet"}), 422
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    train = df[[time_col, "Close", "MACD", "RSI14", "ATR14", "VWAP"]].copy()
    train.columns = ["ds", "y", "MACD", "RSI", "ATR", "VWAP"]
    train["ds"] = pd.to_datetime(train["ds"], utc=True).dt.tz_convert(None)
    model = Prophet(daily_seasonality=cfg["interval"] in ["1m","5m","15m","30m","1h"], weekly_seasonality=True, yearly_seasonality=False, interval_width=0.8)
    for reg in ["MACD","RSI","ATR","VWAP"]:
        model.add_regressor(reg)
    model.fit(train)
    future = model.make_future_dataframe(periods=cfg["steps"], freq=cfg["freq"], include_history=True)
    if forecast_mode == "simulated_ohlcv":
        simulated = _simulate_future_ohlcv(df, cfg["steps"], cfg["freq"])
        combined = pd.concat([df, simulated], ignore_index=True)
        combined = _forecast_features(combined)
        future_regs = combined.tail(cfg["steps"])[["MACD","RSI14","ATR14","VWAP"]].copy()
        future_regs.columns = ["MACD","RSI","ATR","VWAP"]
        for reg in ["MACD","RSI","ATR","VWAP"]:
            future[reg] = list(train[reg]) + list(future_regs[reg])
    else:
        last_regs = train[["MACD","RSI","ATR","VWAP"]].iloc[-1]
        for reg in ["MACD","RSI","ATR","VWAP"]:
            future[reg] = list(train[reg]) + [float(last_regs[reg])] * cfg["steps"]
    fcst = model.predict(future)
    pred = fcst.tail(cfg["steps"])[["ds","yhat","yhat_lower","yhat_upper"]].copy()
    pred["ds"] = pred["ds"].astype(str)
    history = train.tail(120)[["ds","y"]].copy(); history["ds"] = history["ds"].astype(str)
    last_price = float(train["y"].iloc[-1]); final_price = float(pred["yhat"].iloc[-1])
    return jsonify({
        "ticker": ticker, "style": style, "style_label": cfg["label"], "interval": cfg["interval"], "period": cfg["period"], "steps": cfg["steps"],
        "latency_ms": round((time.perf_counter()-t0)*1000,2),
        "forecast_mode": forecast_mode,
        "regressor_strategy": "simulated_ohlcv_derived" if forecast_mode == "simulated_ohlcv" else "last_observation_carried_forward",
        "metrics": {"last": last_price, "forecast_final": final_price, "forecast_change_pct": ((final_price/last_price)-1)*100},
        "history": history.to_dict("records"), "forecast": pred.to_dict("records")
    })


@app.route("/forecast/lstm", methods=["GET"])
def forecast_lstm_proxy():
    ticker = _normalize_symbol(request.args.get("ticker", "BTC-USD"))
    style = request.args.get("style", "scalping").lower()
    selected=_selected_user_assets()
    if selected is not None:
        if ticker not in {_normalize_symbol(x) for x in selected}:
            return jsonify({"error":"Ticker fuera de tu universo de activos"}), 400
    elif ticker not in TICKERS:
        return jsonify({"error":"Ticker no permitido"}), 400
    if style not in STYLE_CONFIG:
        return jsonify({"error":"Estilo inválido"}), 400
    try:
        r=http.get(f"{GPU_MARKET_URL}/forecast/lstm", params={"ticker":ticker,"style":style}, timeout=120)
        return jsonify(r.json()), r.status_code
    except requests.RequestException as exc:
        return jsonify({"error":"LSTM GPU no disponible","detail":str(exc)}), 503


# ─── Portfolio Markowitz CPU/GPU ────────────────────────────────────────────
import numpy as np
from scipy.optimize import minimize

DEFAULT_TICKERS = ["HWM", "PLTR", "NVDA", "V", "AMZN", "WM"]
DEFAULT_BENCHMARK = "SPY"
STOCK_CATALOG = ['WMT', 'AAPL', 'PLTR', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSM', 'BRK-B', 'V', 'JPM', 'XOM', 'LLY', 'MRK', 'UNH', 'PG', 'MA', 'CVX', 'KO', 'PEP', 'COST', 'TMO', 'ORCL', 'CSCO', 'NKE', 'VZ', 'ASML', 'TXN', 'ABT', 'TM', 'SAP', 'AMD', 'NFLX', 'NOW', 'ADBE', 'LVMUY', 'BABA', 'SHEL', 'TMUS', 'QCOM', 'PFE', 'SNY', 'AZN', 'TOT', 'GSK', 'RIO', 'BHP', 'MCD', 'HWM', 'WM', 'ADMA', 'ALMU', 'AVGO', 'ASTS', 'BE', 'DAVE', 'POWL']
CRYPTO_CATALOG = ['BTC-USD', 'ETH-USD', 'USDT-USD', 'XRP-USD', 'LTC-USD', 'ADA-USD', 'DOT-USD', 'BCH-USD', 'XLM-USD', 'LINK-USD']
FX_CATALOG = ['EURUSD=X', 'MXN=X', 'USDMXN=X', 'CADMXN=X', 'JPYMXN=X', 'EURMXN=X', 'GBPMXN=X', 'GBPUSD=X', 'USDJPY=X', 'EURJPY=X', 'AUDUSD=X', 'CADUSD=X']
PORTFOLIO_BENCHMARKS = {"SPY", "QQQ"}
PORTFOLIO_PERIODS = {"6mo", "1y", "2y", "5y"}
PORTFOLIO_MAP = {"BTC": "BTC-USD", "BRK.B": "BRK-B", "MNX=X": "MXN=X", "MCD​": "MCD"}
PORTFOLIO_CATALOG = set(STOCK_CATALOG + CRYPTO_CATALOG + FX_CATALOG)

def _normalize_portfolio_tickers(raw):
    items = [x.strip().upper() for x in raw.split(",") if x.strip()]
    normalized = [PORTFOLIO_MAP.get(x, x) for x in items]
    dedup = []
    for x in normalized:
        if x not in dedup:
            dedup.append(x)
    return dedup

def _portfolio_validate(tickers, benchmark, period):
    errors = {}
    if not (2 <= len(tickers) <= 10): errors["tickers"] = "Selecciona entre 2 y 10 activos"
    selected = _selected_user_assets()
    if selected is None:
        invalid = [t for t in tickers if t not in PORTFOLIO_CATALOG]
        if invalid: errors["invalid_tickers"] = invalid
    else:
        allowed={PORTFOLIO_MAP.get(x, x) for x in selected}
        outside = [t for t in tickers if t not in allowed]
        if outside: errors["outside_user_universe"] = outside
    if benchmark not in PORTFOLIO_BENCHMARKS: errors["benchmark"] = "Benchmark no permitido"
    if period not in PORTFOLIO_PERIODS: errors["period"] = "Periodo no permitido"
    return errors

def _download_close_matrix(tickers, benchmark, period):
    symbols = list(dict.fromkeys(tickers + [benchmark]))
    cache_key = (tuple(symbols), period)
    cached = portfolio_close_cache.get(cache_key)
    if cached is not None:
        return cached
    raw = yf.download(symbols, period=period, interval="1d", progress=False, auto_adjust=True, group_by="column")
    if raw.empty: return None
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"].copy()
    else:
        close = raw[["Close"]].copy()
        close.columns = symbols[:1]
    close = close.dropna(how="all").ffill().dropna()
    needed = tickers + [benchmark]
    missing = [s for s in needed if s not in close.columns]
    if missing: raise ValueError(f"Sin datos para: {', '.join(missing)}")
    close = close[needed].dropna()
    portfolio_close_cache.set(cache_key, close)
    return close

def _drawdown(series):
    wealth = (1 + series).cumprod()
    peak = wealth.cummax()
    return float(((wealth / peak) - 1).min())

def _sortino(series, annualization=252):
    downside = series[series < 0].std(ddof=1)
    if not downside or pd.isna(downside): return None
    return float(np.sqrt(annualization) * series.mean() / downside)

def _annual_metrics(series, benchmark=None):
    annual_return = float(series.mean() * 252)
    annual_vol = float(series.std(ddof=1) * np.sqrt(252))
    sharpe = None if annual_vol == 0 else float(annual_return / annual_vol)
    cumulative = float((1 + series).prod() - 1)
    years = max(len(series) / 252, 1/252)
    var95 = float(series.quantile(0.05))
    tail = series[series <= var95]
    out = {
        "annual_return": annual_return,
        "annual_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": _sortino(series),
        "max_drawdown": _drawdown(series),
        "var_95_daily": var95,
        "cvar_95_daily": None if tail.empty else float(tail.mean()),
        "cumulative_return": cumulative,
        "cagr": float((1 + cumulative) ** (1 / years) - 1),
    }
    if benchmark is not None:
        cov = np.cov(series, benchmark)[0,1]
        bvar = float(benchmark.var(ddof=1))
        beta = None if bvar == 0 else float(cov / bvar)
        alpha = None if beta is None else float((series.mean() - beta * benchmark.mean()) * 252)
        out.update({"beta": beta, "alpha": alpha})
    return out

def _solve_long_only(mu, cov, target=None, objective="min_variance"):
    n=len(mu); x0=np.repeat(1/n,n); bounds=[(0.0,1.0)]*n; cons=[{"type":"eq","fun":lambda w: np.sum(w)-1}]
    if target is not None:
        cons.append({"type":"eq","fun":lambda w, target=target: float(w@mu-target)})
    if objective == "max_sharpe":
        fun=lambda w: -float((w@mu)/np.sqrt(w@cov@w)) if (w@cov@w)>0 else 1e9
    else:
        fun=lambda w: float(w@cov@w)
    res=minimize(fun,x0,method="SLSQP",bounds=bounds,constraints=cons,options={"ftol":1e-12,"maxiter":1000})
    if not res.success:
        raise ValueError(f"Optimización no convergió: {res.message}")
    return np.clip(res.x,0,1)

def _frontier_exact(mu, cov, points=60):
    w_min=_solve_long_only(mu,cov,objective="min_variance")
    max_ret=float(np.max(mu)); min_ret=float(w_min@mu)
    frontier=[]
    for target in np.linspace(min_ret,max_ret,points):
        try:
            w=_solve_long_only(mu,cov,target=float(target),objective="min_variance")
            ret=float(w@mu); vol=float(np.sqrt(w@cov@w)); sharpe=None if vol==0 else float(ret/vol)
            frontier.append({"risk":vol,"return":ret,"sharpe":sharpe})
        except ValueError:
            continue
    return frontier, w_min

def _markowitz_payload(close, tickers, benchmark, mode="cpu", engine="pandas"):
    returns = close[tickers].pct_change().dropna()
    bench = close[benchmark].pct_change().reindex(returns.index).dropna()
    returns = returns.loc[bench.index]
    mu = returns.mean().to_numpy() * 252
    cov = returns.cov().to_numpy() * 252
    frontier, w_min = _frontier_exact(mu, cov)
    w_max = _solve_long_only(mu, cov, objective="max_sharpe")
    rng = np.random.default_rng(42)
    sample_w = rng.dirichlet(np.ones(len(tickers)), size=3000)
    sample_ret = sample_w @ mu
    sample_vol = np.sqrt(np.einsum("ij,jk,ik->i", sample_w, cov, sample_w))
    sample_sh = np.divide(sample_ret, sample_vol, out=np.full_like(sample_ret, np.nan), where=sample_vol>0)
    ret_min = returns.mul(w_min, axis=1).sum(axis=1)
    ret_max = returns.mul(w_max, axis=1).sum(axis=1)
    min_metrics = _annual_metrics(ret_min, bench)
    max_metrics = _annual_metrics(ret_max, bench)
    bench_metrics = _annual_metrics(bench)
    cumulative = pd.DataFrame({
        "date": returns.index.astype(str),
        "max_sharpe": ((1+ret_max).cumprod()-1).values,
        "min_variance": ((1+ret_min).cumprod()-1).values,
        "benchmark": ((1+bench).cumprod()-1).values,
    })
    corr = returns.corr().round(6)
    return {
        "mode": mode,
        "engine": engine,
        "tickers": tickers,
        "benchmark": benchmark,
        "rows": int(len(returns)),
        "method": "slsqp_exact_long_only",
        "solver": "SLSQP",
        "frontier_points": int(len(frontier)),
        "context_portfolios": int(len(sample_w)),
        "max_sharpe": {"weights": {t: float(w) for t,w in zip(tickers,w_max)}, "metrics": max_metrics},
        "min_variance": {"weights": {t: float(w) for t,w in zip(tickers,w_min)}, "metrics": min_metrics},
        "benchmark_metrics": bench_metrics,
        "frontier": frontier,
        "cloud": [{"risk":float(v),"return":float(r),"sharpe":float(sh)} for v,r,sh in zip(sample_vol[::10],sample_ret[::10],sample_sh[::10])],
        "cumulative": cumulative.to_dict("records"),
        "correlation": {r: {c: float(corr.loc[r,c]) for c in corr.columns} for r in corr.index},
    }

def _portfolio_cpu_payload(tickers, benchmark, period):
    t0=time.perf_counter()
    close = _download_close_matrix(tickers, benchmark, period)
    if close is None or close.empty: return None
    payload = _markowitz_payload(close, tickers, benchmark, mode="cpu", engine="pandas")
    payload.update({"period": period, "latency_ms": round((time.perf_counter()-t0)*1000,2)})
    return payload

@app.route("/portfolio/markowitz", methods=["GET"])
def portfolio_markowitz():
    raw = request.args.get("tickers", ",".join(DEFAULT_TICKERS))
    tickers = _normalize_portfolio_tickers(raw)
    benchmark = request.args.get("benchmark", DEFAULT_BENCHMARK).upper()
    period = request.args.get("period", "2y")
    mode = request.args.get("mode", "cpu").lower()
    errors = _portfolio_validate(tickers, benchmark, period)
    if errors: return jsonify({"error":"Parámetros inválidos", "fields":errors}), 400
    if mode == "gpu":
        try:
            r = http.get(f"{GPU_MARKET_URL}/portfolio/markowitz", params={"tickers": ",".join(tickers), "benchmark": benchmark, "period": period}, timeout=120)
            if r.ok:
                return jsonify(r.json()), r.status_code
        except requests.RequestException:
            pass
    try:
        payload = _portfolio_cpu_payload(tickers, benchmark, period)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    if payload is None: return jsonify({"error":"Sin datos para la consulta"}), 404
    if mode == "gpu": payload["fallback_from"] = "gpu"
    return jsonify(payload)

# ─── Arranque ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.getenv("FLASK_PORT", 5000)))
