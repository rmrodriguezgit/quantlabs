import json
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
from memory.store import SessionStore
from memory.uploads import UploadStore
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
uploads = UploadStore()
app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
IGNORED_LOG_LINES = ("Importing plotly failed. Interactive plots will not work.",)


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
    mode = str(latest.get("mode") or config.get("mode") or "paper").lower()
    enabled = bool(config.get("enabled", True))
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
        "cycle_id": latest.get("cycle_id"),
        "last_run_at": created_at,
        "last_age_seconds": last_age_seconds,
        "success": bool(latest) and not errors,
        "bankroll_usdt": latest.get("bankroll_usdt") or config.get("bankroll_usdt"),
        "max_stake_pct": latest.get("max_stake_pct") or config.get("max_stake_pct"),
        "polymarket_stake_usdt": config.get("polymarket_stake_usdt", latest.get("polymarket_stake_usdt", 1)),
        "polymarket_auto_liquidate_enabled": config.get("polymarket_auto_liquidate_enabled", latest.get("polymarket_auto_liquidate_enabled", True)),
        "polymarket_stop_loss_pct": config.get("polymarket_stop_loss_pct", latest.get("polymarket_stop_loss_pct", -8.34)),
        "polymarket_take_profit_pct": config.get("polymarket_take_profit_pct", latest.get("polymarket_take_profit_pct", 100)),
        "orders": orders,
        "observations": observations,
        "position_actions": position_actions,
        "claim_actions": claim_actions,
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
            "exit": ["SL: liquidate when position value is down 8.34% or more, e.g. 3.00 -> 2.75 USDT", "TP: liquidate when position value is up 100% or more, e.g. 3.00 -> 6.00 USDT", "manual liquidation button remains available per trade"],
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
    if "polymarket_stop_loss_pct" in data:
        config["polymarket_stop_loss_pct"] = max(-100.0, min(0.0, float(data["polymarket_stop_loss_pct"])))
    if "polymarket_take_profit_pct" in data:
        config["polymarket_take_profit_pct"] = max(1.0, min(500.0, float(data["polymarket_take_profit_pct"])))
    if isinstance(data.get("trading_rules"), dict):
        config["trading_rules"] = data["trading_rules"]
    else:
        config.setdefault("trading_rules", _default_polymarket_rules())
    config.setdefault("enabled", False)
    config.setdefault("mode", "observe")
    config.setdefault("venues", ["polymarket"])
    config.setdefault("polymarket_stake_usdt", 1)
    config.setdefault("polymarket_auto_liquidate_enabled", True)
    config.setdefault("polymarket_stop_loss_pct", -8.34)
    config.setdefault("polymarket_take_profit_pct", 100)
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
        "polymarket_stop_loss_pct": config.get("polymarket_stop_loss_pct", -8.34),
        "polymarket_take_profit_pct": config.get("polymarket_take_profit_pct", 100),
        "rules": rules,
        "config_path": str(config_path),
        "live_execution_enabled": settings.polymarket_live_trading_enabled,
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
        file_ids = data.get('file_ids') or []
        context = uploads.context(request.identity['user_id'], file_ids)
        if context:
            prompt = f"{prompt}\n\n[Archivos adjuntos disponibles]\n{context}"
        return jsonify(
            engine.chat(
                data.get('session_id', 'default'),
                prompt,
                data.get('agent', 'planner'),
                request.identity['user_id'],
                request.identity.get('role'),
            )
        )
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
    return jsonify({'models': ['Nous-Hermes-2-Mistral-7B-DPO.Q4_K_M.gguf', 'Qwen2.5']})


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
    if not token_id or shares <= 0 or current_price <= 0:
        return jsonify({"error": "token_id_shares_current_price_required"}), 400
    try:
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
