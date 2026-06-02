from __future__ import annotations

import re
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class DecisionRoute:
    route: str
    confidence: float
    reason: str
    expected_latency_ms: int
    agent: str
    tool: str | None = None
    risk: str = "normal"
    response: str | None = None

    def event(self) -> dict:
        return {
            "step": "decision_router",
            "decision": {
                "action": "route_decision",
                **asdict(self),
            },
        }


class DecisionRouter:
    """Fast-path router for Harness specialists."""

    RULE_LATENCY_MS = 20
    TOOL_LATENCY_MS = 900
    HYBRID_LATENCY_MS = 4500
    LLM_LATENCY_MS = 3500

    def classify(self, agent: str, objective: str, role: str | None = None) -> DecisionRoute:
        agent = (agent or "planner").strip()
        text = self._normalize(objective)

        rule = self._rule_response(agent, text)
        if rule:
            return DecisionRoute(
                route="rule",
                confidence=0.98,
                reason=rule[0],
                expected_latency_ms=self.RULE_LATENCY_MS,
                agent=agent,
                risk="low",
                response=rule[1],
            )

        if self._is_polymarket(text):
            return DecisionRoute(
                route="hybrid",
                confidence=0.94,
                reason="Polymarket requiere datos CLOB/Chainlink, reglas de riesgo y síntesis explicativa.",
                expected_latency_ms=self.HYBRID_LATENCY_MS,
                agent=agent,
                tool="polymarket",
                risk="high",
            )

        if agent == "finance" and self._is_finance_tool_request(text):
            return DecisionRoute(
                route="hybrid",
                confidence=0.9,
                reason="Finance requiere indicadores/datos locales y explicación del LLM Local.",
                expected_latency_ms=self.HYBRID_LATENCY_MS,
                agent=agent,
                tool=self._finance_tool(text),
                risk="market_analysis",
            )

        if agent in {"validation", "execution"} and self._is_observability(text):
            return DecisionRoute(
                route="hybrid",
                confidence=0.88,
                reason="La solicitud pide estado real del sistema; primero se consultan herramientas locales.",
                expected_latency_ms=self.HYBRID_LATENCY_MS,
                agent=agent,
                tool="docker",
                risk="operational",
            )

        if agent == "file_analyst" or self._is_file_analysis(text):
            return DecisionRoute(
                route="hybrid",
                confidence=0.86,
                reason="El análisis documental necesita microservicio local y síntesis posterior.",
                expected_latency_ms=self.HYBRID_LATENCY_MS,
                agent=agent,
                tool="file_analyst",
                risk="document",
            )

        if agent in {"coding", "codex4u"} and self._is_coding_task(text):
            return DecisionRoute(
                route="hybrid",
                confidence=0.84,
                reason="Tarea técnica: conviene inspeccionar/validar con herramientas y sintetizar con LLM Local.",
                expected_latency_ms=self.HYBRID_LATENCY_MS,
                agent=agent,
                tool="shell",
                risk="code_change",
            )

        if self._is_simple_question(text):
            return DecisionRoute(
                route="llm_local",
                confidence=0.72,
                reason="Pregunta conceptual sin herramienta evidente; LLM Local es el camino más rápido útil.",
                expected_latency_ms=self.LLM_LATENCY_MS,
                agent=agent,
                risk="low",
            )

        return DecisionRoute(
            route="llm_local",
            confidence=0.62,
            reason="No hay regla determinística confiable; usar LLM Local con el especialista solicitado.",
            expected_latency_ms=self.LLM_LATENCY_MS,
            agent=agent,
        )

    def _rule_response(self, agent: str, text: str) -> tuple[str, str] | None:
        if "nginx" in text and any(token in text for token in ["reinicio", "reiniciar", "restart"]):
            if "docker" in text or "contenedor" in text or "compose" in text:
                return (
                    "Comando operativo conocido en Docker; no requiere LLM.",
                    "Para reiniciar nginx en Docker usa:\n\n`docker restart quantlab_nginx`\n\nSi quieres hacerlo desde el compose del proyecto:\n\n`docker compose restart nginx`",
                )
            return (
                "Comando operativo conocido; no requiere LLM.",
                "Para reiniciar nginx en un servidor Linux tradicional usa:\n\n`sudo systemctl restart nginx`\n\nEn QuantLabs normalmente nginx corre en Docker, así que el camino correcto suele ser:\n\n`docker restart quantlab_nginx`",
            )
        return None

    def _normalize(self, value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _is_polymarket(self, text: str) -> bool:
        return any(token in text for token in ["polymarket", "up/down", "btc up", "btc down", "clob", "kelly", "order book"])

    def _is_finance_tool_request(self, text: str) -> bool:
        return any(token in text for token in ["scalping", "rsi", "macd", "vwap", "atr", "mexc", "paper trading", "btc-usd", "btc/usd", "btc/usdt"])

    def _finance_tool(self, text: str) -> str:
        if "mexc" in text:
            return "mexc_spot"
        if "paper" in text:
            return "paper_trading"
        return "financial"

    def _is_observability(self, text: str) -> bool:
        return any(token in text for token in ["status", "estado", "health", "logs", "servicio", "servicios", "docker", "nginx", "gpu"])

    def _is_file_analysis(self, text: str) -> bool:
        return bool(re.search(r"\b(pdf|docx|xlsx|csv|archivo|documento|contrato|analiza el archivo)\b", text))

    def _is_coding_task(self, text: str) -> bool:
        return any(token in text for token in ["python", "docker", "javascript", "node", "html", "css", "api", "endpoint", "commit", "push", "bug", "error", "500", "502"])

    def _is_simple_question(self, text: str) -> bool:
        starters = ("que es", "qué es", "como", "cómo", "por que", "por qué", "explica", "dime")
        return text.startswith(starters) and not self._is_observability(text) and not self._is_polymarket(text)


class RoutedAgent:
    def __init__(self, agent, router: DecisionRouter | None = None):
        self._agent = agent
        self._router = router or DecisionRouter()
        self.name = getattr(agent, "name", "agent")
        self.workflow = getattr(agent, "workflow", None)
        self.instructions = getattr(agent, "instructions", "")

    def act(self, objective: str, ctx) -> dict:
        route = self._router.classify(self.name, objective, getattr(ctx, "role", None))
        route_event = route.event()
        if route.route == "rule" and route.response:
            return {
                "agent": self.name,
                "objective": objective,
                "result": route.response,
                "events": [route_event],
                "usage": {},
                "last_usage": {},
            }
        result = self._agent.act(objective, ctx)
        events = result.get("events") or []
        result["events"] = [route_event, *events]
        result.setdefault("agent", self.name)
        result.setdefault("objective", objective)
        return result
