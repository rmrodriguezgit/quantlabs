import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from agents.registry import AgentRegistry
from api.auth import rate_limit, require_auth
from config import settings
from memory.store import SessionStore, ProjectStore
from memory.uploads import UploadStore
from memory.vector import VectorMemory
from observability import ValidationCollector
from orchestrator.engine import HarnessEngine
from runtime.profiles import detect_compute_profile
from telemetry.logging import configure_logging
from tools.registry import ToolRegistry


configure_logging()
app = Flask(__name__, static_folder='../frontend/static', template_folder='../frontend')
CORS(app, origins=settings.allowed_origins.split(','))
sock = Sock(app)

engine = HarnessEngine()
agents = AgentRegistry()
tools = ToolRegistry()
sessions = SessionStore()
projects = ProjectStore()
uploads = UploadStore()
vector_memory = VectorMemory()
app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
IGNORED_LOG_LINES = ("Importing plotly failed. Interactive plots will not work.",)


def active_llm_model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or "modelo activo de llama.cpp"


def route_agent_for_model(agent: str) -> str:
    requested = (agent or "planner").strip()
    if "qwen2.5-coder" in active_llm_model().lower() and requested in {"coding", "codex4u"}:
        return "codex4u"
    return requested


def rag_enabled_for_agent(agent: str) -> bool:
    return (agent or "").strip() not in {"polymrkt"}


RAG_MARKER = "[Memoria RAG relevante]"
RAG_STOP_WORDS = {
    "como", "para", "puedes", "dime", "decir", "decirme", "hacer", "haz", "los", "las", "una", "uno", "con", "del", "que", "por", "favor", "usuario", "respuesta",
    "analiza", "usando", "objetivo", "reglas", "actual", "actuales", "muestra", "devuelve", "exactamente",
}
RAG_OPERATIONAL_TERMS = {"nginx", "docker", "gpu", "nvidia", "servicio", "servicios", "reinicio", "reiniciar", "detener", "parar", "systemctl", "compose", "contenedor", "harness", "api", "llm"}
RAG_TOPIC_GROUPS = {
    "gpu": {"gpu", "nvidia", "nvidia-smi", "cuda"},
    "nginx": {"nginx", "docker", "compose", "contenedor"},
    "harness": {"harness", "agent", "agente"},
    "llm": {"llm", "modelo", "ollama", "llama"},
}


def normalize_rag_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def strip_rag_sections(text: str) -> str:
    value = str(text or "")
    while RAG_MARKER in value:
        start = value.find(RAG_MARKER)
        response_at = value.find("Respuesta:", start)
        if response_at == -1:
            value = value[:start].rstrip()
        else:
            value = value[:start].rstrip() + "\n" + value[response_at:].lstrip()
    return value.strip()


def rag_tokens(text: str) -> set[str]:
    cleaned = normalize_rag_text(strip_rag_sections(text))
    return {token for token in re.findall(r"[a-záéíóúñü0-9_-]{3,}", cleaned) if token not in RAG_STOP_WORDS}


def rag_topic(tokens: set[str]) -> str | None:
    for topic, words in RAG_TOPIC_GROUPS.items():
        if tokens & words:
            return topic
    return None


def rag_hit_relevant(prompt: str, text: str, agent: str) -> bool:
    prompt_tokens = rag_tokens(prompt)
    text_tokens = rag_tokens(text)
    if not prompt_tokens or not text_tokens:
        return False
    overlap = prompt_tokens & text_tokens
    prompt_topic = rag_topic(prompt_tokens)
    text_topic = rag_topic(text_tokens)
    if prompt_topic and text_topic and prompt_topic != text_topic:
        return False
    if prompt_topic and text_topic == prompt_topic:
        return True
    operational_overlap = overlap & RAG_OPERATIONAL_TERMS
    if (agent or "").strip() in {"execution", "codex4u", "coding"}:
        return bool(operational_overlap) and len(overlap) >= 2
    return len(overlap) >= 2


def rag_context(user_id: str, session_id: str, agent: str, prompt: str, project_id: str | None = None) -> str:
    try:
        hits = vector_memory.search(vector_memory.embed(prompt), top_k=32)
    except Exception:
        return ""
    lines = []
    seen = set()
    for item in hits:
        meta = item.get("metadata") or {}
        if meta.get("user_id") != user_id:
            continue
        if project_id and meta.get("project_id") not in {project_id, None}:
            continue
        if meta.get("agent") != agent:
            continue
        text = strip_rag_sections(item.get("text") or "")
        if not rag_hit_relevant(prompt, text, agent):
            continue
        fingerprint = meta.get("text_hash") or normalize_rag_text(text)
        if not text or fingerprint in seen:
            continue
        seen.add(fingerprint)
        lines.append(f"- {text[:700]}")
        if len(lines) >= 3:
            break
    return "\n".join(lines)


def remember_exchange(user_id: str, session_id: str, agent: str, prompt: str, response: str, project_id: str | None = None):
    try:
        clean_prompt = strip_rag_sections(prompt)
        clean_response = strip_rag_sections(response)
        if not clean_prompt or len(clean_prompt) > 4000:
            return
        text = f"Usuario: {clean_prompt}\nRespuesta: {clean_response}"
        text_hash = vector_memory._hash(text)
        duplicate_scope = {"user_id": user_id, "agent": agent}
        if project_id:
            duplicate_scope["project_id"] = project_id
        if vector_memory.exists_duplicate(text_hash, duplicate_scope):
            return
        vector_memory.add(
            text,
            vector_memory.embed(f"{clean_prompt}\n{clean_response}"),
            {"user_id": user_id, "session_id": session_id, "project_id": project_id or projects.default_id(), "agent": agent, "model": active_llm_model(), "text_hash": text_hash},
        )
    except Exception:
        pass

def _task_status_value(task) -> str:
    status = getattr(task, "status", None)
    return getattr(status, "value", status) or "unknown"


def _latest_tool_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        result = event.get("result") or {}
        if result.get("name"):
            return {
                "step": event.get("step"),
                "name": result.get("name"),
                "ok": result.get("ok"),
                "error": result.get("error"),
                "duration_ms": result.get("duration_ms"),
            }
    return {}


def _tool_context(events: list[dict[str, Any]], agent: str | None = None) -> dict[str, Any]:
    tools = []
    for event in events:
        result = event.get("result") or {}
        name = result.get("name")
        if name:
            tools.append({
                "name": name,
                "ok": result.get("ok"),
                "error": result.get("error"),
            })
    if not tools:
        return {"primary_tool": None, "context_tools": []}
    if agent == "polymrkt":
        primary = next((tool for tool in tools if tool["name"] == "polymarket"), tools[0])
        context = [tool for tool in tools if tool["name"] != primary["name"]]
        return {"primary_tool": primary, "context_tools": context}
    return {"primary_tool": tools[-1], "context_tools": tools[:-1]}


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((finish - start).total_seconds() * 1000))


def summarize_latest_task(state) -> dict[str, Any] | None:
    if not state.tasks:
        return None

    task = state.tasks[-1]
    metadata = task.metadata or {}
    events = metadata.get("events") or []
    tool_events = [event for event in events if (event.get("result") or {}).get("name")]
    latest_tool = _latest_tool_event(events)
    tool_context = _tool_context(events, task.agent)
    last_error = metadata.get("error") or latest_tool.get("error")

    return {
        "id": task.id,
        "agent": task.agent,
        "objective": task.objective,
        "status": _task_status_value(task),
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "duration_ms": _duration_ms(metadata.get("started_at"), metadata.get("finished_at")),
        "steps_count": len(events),
        "tool_count": len(tool_events),
        "last_tool": latest_tool.get("name"),
        "last_tool_ok": latest_tool.get("ok"),
        "last_tool_error": latest_tool.get("error"),
        "primary_tool": (tool_context.get("primary_tool") or {}).get("name"),
        "primary_tool_ok": (tool_context.get("primary_tool") or {}).get("ok"),
        "primary_tool_error": (tool_context.get("primary_tool") or {}).get("error"),
        "context_tools": tool_context.get("context_tools") or [],
        "last_error": last_error,
        "trajectory_path": metadata.get("trajectory_path"),
    }


def build_status_payload(state, role=None) -> dict[str, Any]:
    metadata = state.metadata or {}
    return {
        "ok": True,
        "service": "quantlab_harness",
        "session_id": state.session_id,
        "system": {
            "cpu_percent": psutil.cpu_percent(),
            "ram_percent": psutil.virtual_memory().percent,
            "compute": detect_compute_profile(),
        },
        "tokens": {
            "last_prompt_tokens": metadata.get("last_prompt_tokens", 0),
            "last_completion_tokens": metadata.get("last_completion_tokens", 0),
            "tokens_generated_total": metadata.get("tokens_generated_total", 0),
            "context_window": metadata.get("context_window", settings.model_context_window),
        },
        "latest_task": summarize_latest_task(state),
        "message_count": len(state.messages),
        "artifact_count": len(state.artifacts),
        "tools": tools.visible_tools(role),
    }


MICROSERVICE_CATALOG = {
    "shell": {"category": "Infra", "purpose": "Comandos controlados para diagnóstico operativo.", "inputs": ["command"], "outputs": ["stdout", "stderr", "exit_code"], "reusable_for": ["soporte", "deploy", "auditoría"]},
    "python": {"category": "Compute", "purpose": "Ejecución Python local para análisis y transformación.", "inputs": ["code"], "outputs": ["stdout", "artefactos"], "reusable_for": ["datos", "validación", "prototipos"]},
    "file": {"category": "Storage", "purpose": "Lectura/escritura segura de archivos permitidos.", "inputs": ["path", "content"], "outputs": ["archivo", "metadata"], "reusable_for": ["documentación", "configuración"]},
    "docker": {"category": "Infra", "purpose": "Inspección de contenedores permitidos.", "inputs": ["target", "action"], "outputs": ["status", "logs"], "reusable_for": ["healthchecks", "operación"]},
    "financial": {"category": "Market Data", "purpose": "Datos financieros y cálculos de mercado.", "inputs": ["ticker", "period"], "outputs": ["series", "indicadores"], "reusable_for": ["acciones", "reportes"]},
    "web_api": {"category": "Integration", "purpose": "Consulta HTTP a hosts permitidos.", "inputs": ["url", "method"], "outputs": ["json", "text"], "reusable_for": ["conectores", "monitoreo"]},
    "mexc_spot": {"category": "Trading", "purpose": "Análisis MEXC spot con reglas de riesgo.", "inputs": ["symbol", "timeframe"], "outputs": ["señal", "riesgo"], "reusable_for": ["scanner cripto"]},
    "polymarket": {"category": "Trading", "purpose": "Señales BTC Up/Down 5m/15m con Chainlink y CLOB.", "inputs": ["asset", "window"], "outputs": ["decision", "candidatos", "microestructura"], "reusable_for": ["predicción", "validación"]},
    "paper_trading": {"category": "Trading Ops", "purpose": "Automatización observe/paper/live con candados operativos.", "inputs": ["rules", "mode"], "outputs": ["orders", "observations", "actions"], "reusable_for": ["control room", "backtesting"]},
    "deep_research": {"category": "Research", "purpose": "Investigación profunda estructurada.", "inputs": ["objective"], "outputs": ["tesis", "fuentes"], "reusable_for": ["reportes"]},
    "dexter_research": {"category": "Research", "purpose": "Research financiero contextual.", "inputs": ["tickers", "horizon"], "outputs": ["contexto", "artefactos"], "reusable_for": ["polymarket", "acciones"]},
    "jupyter_gpu": {"category": "GPU Lab", "purpose": "Ejecución de notebooks/código GPU.", "inputs": ["code"], "outputs": ["stdout", "modelos"], "reusable_for": ["entrenamiento", "validación CUDA"]},
    "file_analyst": {"category": "Documents", "purpose": "Análisis local de documentos privados.", "inputs": ["file_id", "text"], "outputs": ["resumen", "riesgos", "plan"], "reusable_for": ["legal", "finanzas", "auditoría"]},
}


def build_microservice_catalog(role=None) -> dict[str, Any]:
    visible = set(tools.visible_tools(role))
    nodes = []
    for name in sorted(visible):
        spec = MICROSERVICE_CATALOG.get(name, {})
        nodes.append({
            "id": name,
            "label": name.replace("_", " ").title(),
            "category": spec.get("category", "Tool"),
            "purpose": spec.get("purpose", "Herramienta disponible en Harness."),
            "inputs": spec.get("inputs", []),
            "outputs": spec.get("outputs", []),
            "reusable_for": spec.get("reusable_for", []),
            "roles": sorted(role_name for role_name, allowed in tools.allowed_by_role.items() if name in allowed),
        })
    edges = [
        {"from": "file_analyst", "to": "research", "label": "documentos -> criterio"},
        {"from": "polymarket", "to": "paper_trading", "label": "señal -> operación"},
        {"from": "dexter_research", "to": "polymarket", "label": "contexto -> señal"},
        {"from": "jupyter_gpu", "to": "polymarket", "label": "modelo -> predicción"},
        {"from": "docker", "to": "shell", "label": "infra -> comandos"},
        {"from": "financial", "to": "dexter_research", "label": "datos -> research"},
    ]
    edges = [edge for edge in edges if edge["from"] in visible and edge["to"] in visible]
    return {
        "catalog_version": "2026-06-02",
        "concept": "Microservicios reutilizables tipo n8n: cada nodo declara entradas, salidas, rol y posibilidad de reciclaje.",
        "nodes": nodes,
        "edges": edges,
        "recommended_flows": [
            {"name": "Polymarket Predictivo", "nodes": ["dexter_research", "polymarket", "paper_trading"], "use": "contexto + señal + control operativo"},
            {"name": "Documentos Privados", "nodes": ["file_analyst", "research", "planner"], "use": "documento + análisis + plan"},
            {"name": "GPU Model Lab", "nodes": ["jupyter_gpu", "python", "file"], "use": "entrenar, guardar y documentar modelos"},
        ],
    }


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(max(size, 0))
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


def _tail_lines(path: Path, limit: int = 80) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            block = 4096
            data = b""
            while end > 0 and data.count(b"\n") <= limit:
                step = min(block, end)
                end -= step
                fh.seek(end)
                data = fh.read(step) + data
        return data.decode("utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


def _actionable_log_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not any(noise in line for noise in IGNORED_LOG_LINES)]


def _summarize_runner_log(lines: list[str]) -> list[str]:
    summary: list[str] = []
    for line in _actionable_log_lines(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            summary.append(line)
            continue
        if not isinstance(payload, dict) or "orders_count" not in payload:
            summary.append(line)
            continue
        status = "ok" if not payload.get("errors") else "error"
        summary.append(
            " | ".join(
                [
                    f"mode {payload.get('mode', 'paper')}",
                    f"trades {payload.get('orders_count', 0)}",
                    f"observaciones {payload.get('observations_count', 0)}",
                    f"errores {len(payload.get('errors') or [])}",
                    status,
                ]
            )
        )
    return summary


def _jsonl_tail(path: Path | None, limit: int = 12) -> list[dict[str, Any]]:
    if not path:
        return []
    records: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _paper_trading_dir() -> Path:
    return Path(settings.artifact_root) / "paper_trading"


def _polymarket_positions_snapshot() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    try:
        tool = tools.tools["paper_trading"]
        positions = tool._fetch_polymarket_positions()
    except Exception as exc:
        return [], [], str(exc)[:240]
    open_rows: list[dict[str, Any]] = []
    resolved_rows: list[dict[str, Any]] = []
    for position in positions:
        try:
            if not tool._is_managed_polymarket_position(position):
                continue
            summary = tool._polymarket_position_summary(position)
        except Exception:
            continue
        size = float(summary.get("size") or 0)
        asset = str(summary.get("asset") or "").strip()
        if not asset or size <= 0:
            continue
        row = {
            **summary,
            "percent_pnl": round(float(position.get("percentPnl") or 0), 4),
            "cash_pnl": round(float(position.get("cashPnl") or 0), 4),
            "event_slug": position.get("eventSlug") or position.get("slug") or position.get("marketSlug"),
            "secret_exposed": False,
        }
        if row.get("redeemable") or float(row.get("current_price") or 0) <= 0 or float(row.get("current_value") or 0) <= 0:
            resolved_rows.append(row)
        else:
            open_rows.append(row)
    return open_rows, resolved_rows, None


def build_paper_trading_snapshot(root: Path | None = None) -> dict[str, Any]:
    root = root or _paper_trading_dir()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}

    audit_files = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    latest_audit = audit_files[-1] if audit_files else None
    records = _jsonl_tail(latest_audit, 16)
    latest = records[-1] if records else {}
    orders = latest.get("orders") or []
    observations = latest.get("observations") or []
    errors = latest.get("errors") or []
    position_actions = latest.get("position_actions") or []
    claim_actions = latest.get("claim_actions") or []
    open_positions, resolved_positions, open_positions_error = _polymarket_positions_snapshot()
    enabled = bool(config.get("enabled", False))
    mode = str(config.get("mode") or latest.get("mode") or "observe").lower()
    server_live_trading_enabled = bool(settings.polymarket_live_trading_enabled)
    runtime_live_execution_enabled = bool(config.get("live_execution_enabled", False))
    live_blocked = enabled and mode == "live" and not (server_live_trading_enabled and runtime_live_execution_enabled)
    if not enabled:
        mode = "observe"
        live_blocked = False
    created_at = latest.get("created_at")
    last_age_seconds = None
    if created_at:
        try:
            started = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            last_age_seconds = max(0, round((datetime.now(started.tzinfo) - started).total_seconds()))
        except ValueError:
            last_age_seconds = None

    log_files = [p for p in root.iterdir() if p.is_file()]
    total_log_bytes = sum(p.stat().st_size for p in log_files)
    file_summary = [
        {
            "name": p.name,
            "size_bytes": p.stat().st_size,
            "size": _format_bytes(p.stat().st_size),
            "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        }
        for p in sorted(log_files, key=lambda item: item.name)
    ]
    status = "ok"
    if not enabled:
        status = "stopped"
    elif live_blocked:
        status = "blocked"
    elif errors:
        status = "error"
    elif not latest:
        status = "waiting"
    elif last_age_seconds is not None and last_age_seconds > 180:
        status = "stale"

    recent_cycles = [
        {
            "cycle_id": record.get("cycle_id"),
            "created_at": record.get("created_at"),
            "mode": record.get("mode"),
            "orders_count": len(record.get("orders") or []),
            "observations_count": len(record.get("observations") or []),
            "errors_count": len(record.get("errors") or []),
            "position_actions_count": len(record.get("position_actions") or []),
            "claim_actions_count": len(record.get("claim_actions") or []),
        }
        for record in records[-8:]
    ]

    return {
        "name": "Paper Trading",
        "status": status,
        "enabled": enabled,
        "mode": mode,
        "live_execution_enabled": runtime_live_execution_enabled,
        "server_live_trading_enabled": server_live_trading_enabled,
        "live_blocked": live_blocked,
        "cycle_id": latest.get("cycle_id"),
        "last_run_at": created_at,
        "last_age_seconds": last_age_seconds,
        "success": bool(latest) and not errors,
        "bankroll_usdt": latest.get("bankroll_usdt") or config.get("bankroll_usdt"),
        "max_stake_pct": latest.get("max_stake_pct") or config.get("max_stake_pct"),
        "polymarket_stake_usdt": config.get("polymarket_stake_usdt", latest.get("polymarket_stake_usdt", 1)),
        "polymarket_auto_liquidate_enabled": config.get("polymarket_auto_liquidate_enabled", latest.get("polymarket_auto_liquidate_enabled", True)),
        "polymarket_time_stop_pct": _bounded_percent(config.get("polymarket_time_stop_pct", latest.get("polymarket_time_stop_pct", 75)), 75, 10, 99),
        "polymarket_take_profit_pct": _bounded_percent(config.get("polymarket_take_profit_pct", latest.get("polymarket_take_profit_pct", 100)), 100, 10, 500),
        "polymarket_invert_prediction_enabled": config.get("polymarket_invert_prediction_enabled", latest.get("polymarket_invert_prediction_enabled", False)),
        "orders": orders,
        "observations": observations,
        "position_actions": position_actions,
        "claim_actions": claim_actions,
        "polymarket_open_positions": open_positions,
        "polymarket_open_position_assets": [row.get("asset") for row in open_positions if row.get("asset")],
        "polymarket_resolved_positions": resolved_positions,
        "polymarket_resolved_position_assets": [row.get("asset") for row in resolved_positions if row.get("asset")],
        "polymarket_positions_error": open_positions_error,
        "observations_count": len(observations),
        "position_actions_count": len(position_actions),
        "claim_actions_count": len(claim_actions),
        "errors": errors,
        "recent_cycles": recent_cycles,
        "logs": {
            "stdout": _summarize_runner_log(_tail_lines(root / "systemd.log", 80)),
            "stderr": _actionable_log_lines(_tail_lines(root / "systemd.err", 80)),
            "audit": recent_cycles,
            "total_bytes": total_log_bytes,
            "total_size": _format_bytes(total_log_bytes),
            "files": file_summary,
            "retention": "logrotate diario, 30 rotaciones, compresión y maxsize 10M",
        },
    }


def _paper_trading_config_path() -> Path:
    return _paper_trading_dir() / "config.json"


POLYMARKET_STAKE_CHOICES = {1.0, 2.0, 3.0}


def _bounded_percent(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number < minimum:
        number = default
    return max(minimum, min(maximum, float(number)))


def _load_paper_trading_config() -> dict[str, Any]:
    config_path = _paper_trading_config_path()
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_paper_trading_config(config: dict[str, Any]) -> None:
    config_path = _paper_trading_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def _default_polymarket_rules() -> dict[str, Any]:
    return {
        "polymarket_btc_updown": {
            "trade": ["enabled=true", "confidence>=0.80", "edge>=0.03", "spread<=0.08", "ask_size>=1", "seconds_to_close>=60", "one_trade_per_event_window"],
            "stake": ["manual fixed stake only: 1, 2 or 3 USDT", "no martingale", "no averaging down"],
            "exit": ["SL: liquidate after configured window percent if PnL remains negative", "TP: liquidate when position value is up 100% or more, e.g. 3.00 -> 6.00 USDT", "manual liquidation button remains available per trade", "time stop defaults to 75% of window when PnL remains negative"],
            "prediction": ["optional invert switch: Down becomes Up and Up becomes Down before order sizing"],
        }
    }


def _sanitize_paper_trading_update(data: dict[str, Any]) -> dict[str, Any]:
    config = _load_paper_trading_config()
    if "enabled" in data:
        config["enabled"] = bool(data.get("enabled"))
    mode = data.get("mode")
    if mode in {"observe", "paper"}:
        config["mode"] = mode
    elif mode == "live":
        if not settings.polymarket_live_trading_enabled:
            raise PermissionError("live_mode_requires_server_enablement")
        config["mode"] = "live"
    if "polymarket_stake_usdt" in data or "stake" in data:
        raw_stake = float(data.get("polymarket_stake_usdt", data.get("stake")))
        if raw_stake not in POLYMARKET_STAKE_CHOICES:
            raise ValueError("stake_must_be_1_2_or_3_usdt")
        config["polymarket_stake_usdt"] = raw_stake
    if "polymarket_auto_liquidate_enabled" in data or "auto_liquidate" in data:
        config["polymarket_auto_liquidate_enabled"] = bool(data.get("polymarket_auto_liquidate_enabled", data.get("auto_liquidate")))
    if "polymarket_time_stop_pct" in data:
        config["polymarket_time_stop_pct"] = _bounded_percent(data["polymarket_time_stop_pct"], 75, 10, 99)
    if "polymarket_take_profit_pct" in data:
        config["polymarket_take_profit_pct"] = _bounded_percent(data["polymarket_take_profit_pct"], 100, 10, 500)
    if "polymarket_invert_prediction_enabled" in data:
        config["polymarket_invert_prediction_enabled"] = bool(data.get("polymarket_invert_prediction_enabled"))
    if "live_execution_enabled" in data:
        config["live_execution_enabled"] = bool(data.get("live_execution_enabled")) and bool(settings.polymarket_live_trading_enabled)
    if isinstance(data.get("trading_rules"), dict):
        config["trading_rules"] = data["trading_rules"]
    else:
        config.setdefault("trading_rules", _default_polymarket_rules())
    config.setdefault("enabled", False)
    config.setdefault("mode", "observe")
    config.setdefault("venues", ["polymarket"])
    config.setdefault("polymarket_stake_usdt", 1)
    config.setdefault("polymarket_auto_liquidate_enabled", True)
    config["polymarket_time_stop_pct"] = _bounded_percent(config.get("polymarket_time_stop_pct"), 75, 10, 99)
    config["polymarket_take_profit_pct"] = _bounded_percent(config.get("polymarket_take_profit_pct"), 100, 10, 500)
    config.setdefault("polymarket_invert_prediction_enabled", False)
    config.setdefault("live_execution_enabled", False)
    return config


def paper_trading_rules_payload() -> dict[str, Any]:
    config_path = _paper_trading_config_path()
    config = _load_paper_trading_config()
    rules = config.get("trading_rules") or getattr(tools.tools.get("paper_trading"), "default_rules", {})
    return {
        "agent": "paper_trading",
        "enabled": bool(config.get("enabled", False)),
        "mode": config.get("mode", "observe"),
        "polymarket_stake_usdt": config.get("polymarket_stake_usdt", 1),
        "polymarket_auto_liquidate_enabled": config.get("polymarket_auto_liquidate_enabled", True),
        "polymarket_time_stop_pct": _bounded_percent(config.get("polymarket_time_stop_pct"), 75, 10, 99),
        "polymarket_take_profit_pct": _bounded_percent(config.get("polymarket_take_profit_pct"), 100, 10, 500),
        "polymarket_invert_prediction_enabled": config.get("polymarket_invert_prediction_enabled", False),
        "rules": rules,
        "config_path": str(config_path),
        "live_execution_enabled": bool(config.get("live_execution_enabled", False)),
        "server_live_trading_enabled": bool(settings.polymarket_live_trading_enabled),
        "live_ready": bool(settings.polymarket_live_trading_enabled and config.get("live_execution_enabled", False)),
        "allowed_modes": ["observe", "paper", "live"],
        "allowed_stakes": sorted(POLYMARKET_STAKE_CHOICES),
    }


@app.get('/')
def index():
    return send_from_directory('../frontend', 'index.html')


@app.post('/v1/chat')
@rate_limit(settings.rate_limit_per_minute)
@require_auth({'admin', 'teacher', 'trader'})
def chat():
    try:
        data = request.get_json(force=True)
        prompt = (data.get('message') or '').strip()
        if not prompt:
            return jsonify({'error': 'message_required'}), 400
        original_prompt = prompt
        show_rag = bool(data.get('show_rag'))
        file_ids = data.get('file_ids') or []
        session_id = data.get('session_id', 'default')
        project_id = (data.get('project_id') or projects.default_id()).strip()
        project = projects.get(request.identity['user_id'], project_id)
        project_id = project['id']
        agent = route_agent_for_model(data.get('agent', 'planner'))
        context = uploads.context(request.identity['user_id'], file_ids)
        if context:
            prompt = f"{prompt}\n\n[Archivos adjuntos disponibles]\n{context}"
        use_rag = rag_enabled_for_agent(agent)
        memory_context = rag_context(request.identity['user_id'], session_id, agent, prompt, project_id) if use_rag else ""
        if use_rag and memory_context:
            prompt = f"{prompt}\n\n[Memoria RAG relevante]\n{memory_context}"
        user_message_metadata = {}
        if show_rag and memory_context:
            user_message_metadata["rag_context"] = memory_context
        result = engine.chat(
            session_id,
            prompt,
            agent,
            request.identity['user_id'],
            request.identity.get('role'),
            display_prompt=original_prompt,
            user_message_metadata=user_message_metadata,
        )
        result.setdefault("metadata", {})["active_model"] = active_llm_model()
        result["metadata"]["project_id"] = project_id
        result["metadata"]["project_name"] = project.get("name")
        result["metadata"]["effective_agent"] = agent
        result["metadata"]["rag_enabled"] = use_rag
        result["metadata"]["rag_available"] = bool(memory_context)
        result["metadata"]["rag_visible"] = bool(show_rag and memory_context)
        if show_rag and memory_context:
            result["metadata"]["rag_context"] = memory_context
        remember_exchange(request.identity['user_id'], session_id, agent, original_prompt, result.get("response", ""), project_id)
        return jsonify(result)
    except PermissionError as exc:
        return jsonify({'error': str(exc)}), 403
    except Exception as exc:
        return jsonify({'error': 'chat_failed', 'detail': str(exc)}), 500


@app.get('/v1/agents')
@require_auth({'admin', 'teacher', 'trader'})
def list_agents():
    return jsonify({'agents': agents.list()})


@app.get('/v1/tools')
@require_auth({'admin', 'teacher', 'trader'})
def list_tools():
    return jsonify({'tools': tools.visible_tools(request.identity.get('role'))})


@app.get('/v1/tasks')
@require_auth({'admin', 'teacher', 'trader'})
def tasks():
    state = sessions.load(request.args.get('session_id', 'default'), request.identity['user_id'])
    return jsonify({'tasks': [t.model_dump() for t in state.tasks]})


@app.get('/v1/models')
@require_auth({'admin', 'teacher', 'trader'})
def models():
    model = active_llm_model()
    return jsonify({'models': [model], 'active_model': model, 'model_status': 'activo' if model else 'sin modelo', 'rag_enabled': True})


@app.get('/v1/memory')
@require_auth({'admin', 'teacher', 'trader'})
def memory():
    return jsonify(sessions.load(request.args.get('session_id', 'default'), request.identity['user_id']).model_dump())


@app.get('/v1/conversations')
@require_auth({'admin', 'teacher', 'trader'})
def list_conversations():
    return jsonify({'conversations': sessions.list(request.identity['user_id'])})


@app.post('/v1/conversations')
@require_auth({'admin', 'teacher', 'trader'})
def create_conversation():
    data = request.get_json(silent=True) or {}
    state = sessions.create(request.identity['user_id'], data.get('title') or 'Nueva conversación')
    return jsonify({'conversation': {'id': state.session_id, 'title': state.metadata['title'], 'messages': 0}}), 201


@app.patch('/v1/conversations/<session_id>')
@require_auth({'admin', 'teacher', 'trader'})
def rename_conversation(session_id):
    data = request.get_json(silent=True) or {}
    state = sessions.rename(request.identity['user_id'], session_id, data.get('title') or '')
    if not state:
        return jsonify({'error': 'no encontrado'}), 404
    return jsonify({'conversation': {'id': state.session_id, 'title': state.metadata['title']}})


@app.delete('/v1/conversations/<session_id>')
@require_auth({'admin', 'teacher', 'trader'})
def delete_conversation(session_id):
    if not sessions.delete(request.identity['user_id'], session_id):
        return jsonify({'error': 'no encontrado'}), 404
    return jsonify({'ok': True})


@app.get('/v1/projects')
@require_auth({'admin', 'teacher', 'trader'})
def list_projects():
    return jsonify({'projects': projects.list(request.identity['user_id'])})


@app.post('/v1/projects')
@require_auth({'admin', 'teacher', 'trader'})
def create_project():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or 'Nuevo proyecto Harness').strip()[:120]
    raw_id = data.get('id') or name.lower().replace(' ', '-')
    project_id = re.sub(r'[^a-zA-Z0-9_.-]+', '-', raw_id).strip('-').lower()[:80] or str(uuid.uuid4())
    return jsonify({'project': projects.ensure(request.identity['user_id'], project_id, name)}), 201


@app.get('/v1/projects/<project_id>')
@require_auth({'admin', 'teacher', 'trader'})
def get_project(project_id):
    data = projects.get(request.identity['user_id'], project_id)
    data['rag_stats'] = vector_memory.stats({'user_id': request.identity['user_id'], 'project_id': data['id']})
    return jsonify({'project': data})


@app.patch('/v1/projects/<project_id>')
@require_auth({'admin', 'teacher', 'trader'})
def update_project(project_id):
    data = request.get_json(silent=True) or {}
    return jsonify({'project': projects.update(request.identity['user_id'], project_id, data)})


@app.post('/v1/projects/<project_id>/memory')
@require_auth({'admin', 'teacher', 'trader'})
def append_project_memory(project_id):
    data = request.get_json(silent=True) or {}
    project = projects.append_memory_note(request.identity['user_id'], project_id, data.get('note') or '')
    return jsonify({'project': project})


@app.get('/v1/microservices/catalog')
@require_auth({'admin', 'teacher', 'trader'})
def microservices_catalog():
    return jsonify(build_microservice_catalog(request.identity.get('role')))


@app.get('/v1/system')
@require_auth({'admin', 'teacher', 'trader'})
def system():
    return jsonify({
        'cpu_percent': psutil.cpu_percent(),
        'ram_percent': psutil.virtual_memory().percent,
        'compute': detect_compute_profile(),
    })


@app.get('/v1/status')
@require_auth({'admin', 'teacher', 'trader'})
def status():
    state = sessions.load(request.args.get('session_id', 'default'), request.identity['user_id'])
    return jsonify(build_status_payload(state, request.identity.get('role')))


@app.get('/v1/user/usage')
@require_auth({'admin', 'teacher', 'trader'})
def user_usage():
    usage = sessions.token_usage(request.identity['user_id'])
    return jsonify({
        'user_id': request.identity['user_id'],
        'username': request.identity.get('username'),
        'role': request.identity.get('role'),
        'usage': usage,
    })


@app.get('/v1/automations/paper-trading')
@require_auth({'admin', 'teacher', 'trader'})
def paper_trading_automation():
    return jsonify({"automation": build_paper_trading_snapshot()})

@app.patch('/v1/automations/paper-trading')
@require_auth({'admin', 'trader'})
def update_paper_trading_automation():
    data = request.get_json(force=True)
    try:
        config = _sanitize_paper_trading_update(data)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    _write_paper_trading_config(config)
    return jsonify({"ok": True, "automation": build_paper_trading_snapshot(), "rules": paper_trading_rules_payload()})


@app.post('/v1/automations/paper-trading/liquidate')
@require_auth({'admin', 'trader'})
def liquidate_polymarket_trade():
    data = request.get_json(force=True)
    if not settings.polymarket_live_trading_enabled:
        return jsonify({"error": "live_liquidation_requires_server_enablement"}), 403
    token_id = str(data.get("token_id") or data.get("asset") or "").strip()
    shares = float(data.get("shares") or data.get("size") or 0)
    current_price = float(data.get("current_price") or data.get("price") or 0)
    if not token_id:
        return jsonify({"error": "token_id_required"}), 400
    try:
        if shares <= 0 or current_price <= 0:
            positions = tools.tools["paper_trading"]._fetch_polymarket_positions()
            match = next((row for row in positions if str(row.get("asset") or "") == token_id), None)
            if match:
                shares = float(match.get("size") or 0)
                current_price = float(match.get("curPrice") or match.get("avgPrice") or current_price or 0)
        if shares <= 0 or current_price <= 0:
            return jsonify({"error": "open_position_not_found_for_token"}), 400
        result = tools.tools["paper_trading"]._place_polymarket_market_sell(token_id, shares, current_price)
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    return jsonify({"ok": True, "liquidation": result})


@app.get('/v1/agents/status')
@require_auth({'admin', 'teacher', 'trader'})
def agents_status():
    return jsonify(ValidationCollector().snapshot())


@app.get('/v1/agents/health')
@require_auth({'admin', 'teacher', 'trader'})
def agents_health():
    return jsonify(ValidationCollector().health())


@app.get('/v1/agents/logs')
@require_auth({'admin', 'teacher', 'trader'})
def agents_logs():
    limit = int(request.args.get('limit', 80))
    return jsonify(ValidationCollector().logs(limit=max(10, min(limit, 300))))


@app.get('/v1/agents/performance')
@require_auth({'admin', 'teacher', 'trader'})
def agents_performance():
    return jsonify(ValidationCollector().performance())


@app.get('/v1/agents/transactions')
@require_auth({'admin', 'teacher', 'trader'})
def agents_transactions():
    limit = int(request.args.get('limit', 200))
    return jsonify(ValidationCollector().transactions(limit=max(10, min(limit, 1000))))


@app.get('/v1/agents/rules')
@require_auth({'admin', 'teacher', 'trader'})
def agents_rules():
    return jsonify(paper_trading_rules_payload())


@app.patch('/v1/agents/rules')
@require_auth({'admin', 'trader'})
def update_agents_rules():
    data = request.get_json(force=True)
    if isinstance(data.get("rules"), dict) and "trading_rules" not in data:
        data["trading_rules"] = data["rules"]
    try:
        config = _sanitize_paper_trading_update(data)
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    _write_paper_trading_config(config)
    return jsonify(paper_trading_rules_payload())


@app.get('/v1/agents/report')
@require_auth({'admin', 'teacher', 'trader'})
def agents_report():
    return Response(ValidationCollector().report(), mimetype='text/html')


@app.post('/v1/files')
@require_auth({'admin', 'teacher', 'trader'})
def upload_file():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'archivo requerido'}), 400
    try:
        return jsonify({'file': uploads.save(request.identity['user_id'], file)}), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@app.get('/v1/files')
@require_auth({'admin', 'teacher', 'trader'})
def list_files():
    return jsonify({'files': uploads.list(request.identity['user_id'])})


@app.delete('/v1/files/<file_id>')
@require_auth({'admin', 'teacher', 'trader'})
def delete_file(file_id):
    if not uploads.delete(request.identity['user_id'], file_id):
        return jsonify({'error': 'no encontrado'}), 404
    return jsonify({'ok': True})


@app.get('/v1/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.get('/healthz')
def healthz():
    return jsonify({'ok': True, 'service': 'quantlab_harness'})


@sock.route('/ws')
def ws(sock):
    while True:
        raw = sock.receive()
        if raw is None:
            break
        data = json.loads(raw)
        sock.send(json.dumps(engine.chat(
            data.get('session_id', 'default'),
            data['message'],
            data.get('agent', 'planner'),
            'websocket',
            'guest',
        )))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
