from __future__ import annotations

import json
from typing import TYPE_CHECKING

import requests

from runtime.llm import LlamaClient
from orchestrator.workflows import WorkflowRegistry

if TYPE_CHECKING:
    from agents.base import AgentContext

SYSTEM = '''Eres un agente autónomo de QuantLab. Responde SIEMPRE en español claro, natural y profesional.
Debes devolver EXCLUSIVAMENTE un solo objeto JSON válido, sin markdown, sin texto antes o después y sin concatenar múltiples objetos JSON.

Contrato obligatorio:
{"thought":"resumen breve de tu razonamiento operativo","action":"respond","final":"respuesta final en español"}

Si necesitas una herramienta, usa:
{"thought":"por qué necesitas la herramienta","action":"tool","tool":"nombre_de_herramienta","arguments":{"action":"nombre_de_accion"}}

Reglas:
- Para una respuesta normal, action debe ser "respond" y final debe estar presente, útil y no vacío.
- El JSON es solo contrato interno: el usuario final nunca debe ver thought, action, tool ni llaves JSON.
- No uses valores placeholder como "optional", "opcional", null o strings vacíos.
- Sé conciso: máximo 8 líneas salvo que el usuario pida detalle.
- Mantén tu especialidad: cada agente debe responder desde su rol, no como asistente genérico.
- Usa herramientas solo cuando aporten verificación real.
- Nunca pidas ni reveles secretos.'''

class AgentLoop:
    def __init__(self, llm=None, max_steps: int = 4):
        self.llm = llm or LlamaClient()
        self.max_steps = max_steps

    def run(self, objective: str, ctx: AgentContext, agent_instructions: str, workflow_name: str | None = None):
        workflow = WorkflowRegistry().get(workflow_name)
        workflow_text = (
            ""
            if not workflow
            else f"\nWorkflow operativo: {workflow['name']} -> {' -> '.join(workflow['steps'])}. Úsalo solo si mejora la respuesta."
        )
        transcript = [
            {"role": "system", "content": SYSTEM + "\n\n" + agent_instructions + workflow_text},
            {"role": "user", "content": objective},
        ]
        events = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        last_usage = {}

        for step in range(1, self.max_steps + 1):
            try:
                completion = self.llm.chat(transcript)
            except requests.Timeout as exc:
                events.append({"step": step, "action": "llm_timeout", "error": str(exc)})
                fallback = self._fallback_from_tool_result(events)
                if fallback:
                    return {"final": fallback, "events": events, "usage": usage, "last_usage": last_usage}
                raise
            raw = completion["content"]
            current_usage = completion.get("usage") or {}
            last_usage = current_usage
            usage["prompt_tokens"] += current_usage.get("prompt_tokens", 0)
            usage["completion_tokens"] += current_usage.get("completion_tokens", 0)
            usage["total_tokens"] += current_usage.get("total_tokens", 0)

            try:
                decision = json.loads(raw)
            except json.JSONDecodeError:
                events.append({"step": step, "action": "parse_error", "raw": raw[:2000]})
                return {"final": raw.strip(), "events": events, "usage": usage, "last_usage": last_usage}

            event = {"step": step, "decision": decision}
            if decision.get("action") == "tool" and decision.get("tool"):
                try:
                    result = ctx.tools.execute(
                        decision["tool"],
                        role=ctx.role,
                        **decision.get("arguments", {}),
                    ).model_dump()
                except Exception as exc:
                    result = {
                        "name": decision["tool"],
                        "ok": False,
                        "output": None,
                        "error": str(exc),
                        "duration_ms": 0,
                    }
                event["result"] = result
                events.append(event)
                transcript.append({"role": "assistant", "content": raw})
                transcript.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False)})
                continue

            events.append(event)
            final = (decision.get("final") or decision.get("thought") or "").strip()
            if not final or final.lower() in {"optional", "opcional", "none", "null"}:
                final = "No obtuve una respuesta válida del agente. Reintenta con una instrucción más concreta."
            return {"final": final, "events": events, "usage": usage, "last_usage": last_usage}

        events.append({"step": self.max_steps, "action": "step_limit"})
        last_error = ""
        for event in reversed(events):
            result = event.get("result") if isinstance(event, dict) else None
            if isinstance(result, dict) and result.get("error"):
                last_error = f" Último error de herramienta: {result['error']}"
                break
        return {
            "final": (
                "El agente alcanzó el límite de pasos antes de completar la tarea. "
                "La solicitud sí fue procesada, pero faltó una respuesta final del modelo."
                f"{last_error}"
            ),
            "events": events,
            "usage": usage,
            "last_usage": last_usage,
        }

    def _fallback_from_tool_result(self, events: list[dict]) -> str | None:
        for event in reversed(events):
            result = event.get("result") if isinstance(event, dict) else None
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            output = result.get("output") or {}
            if result.get("name") == "polymarket" and output.get("signals"):
                return self._format_polymarket_signals(output["signals"])
            if result.get("name") == "polymarket" and output.get("markets"):
                return self._format_polymarket_markets(output["markets"])
        return None

    def _format_polymarket_signals(self, signals: list[dict]) -> str:
        lines = ["Señal operativa Polymarket BTC:"]
        for signal in signals[:4]:
            prophet = signal.get("prophet") or {}
            model = signal.get("model") or prophet.get("model") or prophet.get("status") or "n/d"
            confidence = signal.get("confidence")
            confidence_text = f"{confidence:.1%}" if isinstance(confidence, int | float) else "sin probabilidad"
            decision = self._trade_decision(signal)
            lines.append(
                "- "
                f"Decisión {decision} | {signal.get('interval')}: countdown {signal.get('countdown')}, "
                f"lado {signal.get('preferred_side') or signal.get('side_now_reference')}, "
                f"confianza {confidence_text}, "
                f"umbral 80% {'sí' if signal.get('meets_threshold') else 'no'}, "
                f"precio {signal.get('current_price_reference')} vs inicio {signal.get('start_price_reference')}, "
                f"modelo {model}."
            )
        return "\n".join(lines)

    def _format_polymarket_markets(self, markets: list[dict]) -> str:
        lines = ["Mercados Polymarket BTC: decisión NO TRADE sin probabilidad >= 80% confirmada."]
        for market in markets[:4]:
            tokens = []
            for token in market.get("tokens") or []:
                book = token.get("book") or {}
                bid = (book.get("best_bid") or {}).get("price")
                ask = (book.get("best_ask") or {}).get("price")
                tokens.append(f"{token.get('outcome')} bid/ask {bid}/{ask}")
            lines.append(
                "- "
                f"NO TRADE | {market.get('interval')}: countdown {market.get('countdown')}, "
                f"precio {market.get('current_price_reference')} vs inicio {market.get('start_price_reference')}, "
                f"lado actual {market.get('side_now_reference')}; "
                + "; ".join(tokens)
            )
        return "\n".join(lines)

    def _trade_decision(self, signal: dict) -> str:
        if not signal.get("meets_threshold"):
            return "NO TRADE"
        side = signal.get("preferred_side")
        if side == "Up":
            return "UP"
        if side == "Down":
            return "DOWN"
        return "NO TRADE"
