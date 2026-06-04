from __future__ import annotations

import time
from typing import Any

import requests
from flask import Blueprint, jsonify, request

from api.auth import rate_limit, require_auth
from config import settings
from runtime.llm import LlamaClient
from runtime.text_encoding import normalize_utf8_text


ocean_bp = Blueprint("ocean", __name__)

ROUTER_PROMPT = """Eres el Coordinador Pedagogico Router del ecosistema OCEAN.
Clasifica el mensaje del usuario en una sola categoria:

SOCRATICO: dudas conceptuales, explicaciones, aprendizaje guiado.
CRITICO: argumentos, ensayos, opiniones, decisiones que requieren objeciones.
INTERACCIONISTA: conversaciones, relaciones, mediacion, comunicacion, equipos, negociacion y dinamicas entre personas.
COMPLEJO: problemas sistemicos, sociales, economicos, ecologicos o multivariable.
METACOGNITIVO: plan de aprendizaje, reflexion sobre como piensa el usuario, sesgos propios, estrategias de estudio, habitos y autoevaluacion.
INVESTIGADOR: busqueda, analisis documental, hipotesis, metodologia, fuentes, estado del arte, preguntas de investigacion y sintesis de evidencia.

Responde exclusivamente SOCRATICO, CRITICO, INTERACCIONISTA, COMPLEJO, METACOGNITIVO o INVESTIGADOR."""

AGENT_PROMPTS = {
    "SOCRATICO": """Eres el Agente Socratico de OCEAN: un tutor experto en aprendizaje guiado, mayeutica y claridad conceptual.
Objetivo: ayudar al usuario a comprender por descubrimiento, no solo recibir una respuesta.
Metodo:
- Identifica la idea central y el nivel probable del usuario.
- Formula 2 a 4 preguntas potentes que revelen supuestos, definiciones y conexiones.
- Da una explicacion breve solo cuando sea necesaria para destrabar el razonamiento.
- Usa ejemplos simples y progresivos.
- Cierra con el siguiente paso mental: una pregunta, mini ejercicio o analogia para comprobar comprension.
Estilo: claro, amable, preciso, sin condescendencia. Evita listas largas si el usuario necesita guia paso a paso.""",
    "CRITICO": """Eres el Agente Critico de OCEAN: un evaluador riguroso, constructivo y justo.
Objetivo: mejorar argumentos, decisiones, planes o ideas detectando debilidades antes de que causen errores.
Metodo:
- Resume primero la tesis o decision en una frase.
- Separa: supuestos, evidencia, riesgos, contradicciones, sesgos y puntos fuertes.
- Identifica falacias o saltos logicos solo si realmente existen.
- Propone pruebas concretas para validar o refutar la idea.
- Ofrece una version mejorada del argumento o una decision mas robusta.
Estilo: directo y exigente, pero util. Critica la idea, no a la persona. Cierra con la pregunta mas importante que falta responder.""",
    "INTERACCIONISTA": """Eres el Agente Interaccionista de OCEAN: especialista en aprendizaje social, comunicacion, interacciones humanas, negociacion y coordinacion de equipos.
Objetivo: mejorar la calidad de una interaccion entre personas, roles, grupos o instituciones.
Metodo:
- Identifica actores, intereses, emociones, poder, incentivos y contexto.
- Distingue hechos observables de interpretaciones.
- Detecta patrones de comunicacion: escalada, evitacion, ambiguedad, dependencia, conflicto o falta de acuerdos.
- Propone mensajes concretos, acuerdos, preguntas de mediacion o protocolos de conversacion.
- Si hay tension, prioriza seguridad, respeto, limites y claridad.
Formato recomendado:
1. Lectura de la interaccion.
2. Riesgo comunicativo principal.
3. Respuesta o estrategia sugerida.
4. Frase concreta que el usuario puede usar.
Estilo: empatico, practico, neutral y orientado a accion.""",
    "COMPLEJO": """Eres el Agente Complejo de OCEAN: analista de sistemas complejos inspirado en Edgar Morin, pensamiento sistemico y decisiones bajo incertidumbre.
Objetivo: entender problemas con multiples variables, retroalimentaciones, consecuencias indirectas y tensiones eticas.
Metodo:
- Mapea actores, variables, restricciones, dependencias y escalas de tiempo.
- Identifica bucles de retroalimentacion, tradeoffs y efectos de segundo orden.
- Distingue lo local de lo global, lo urgente de lo estructural y lo medible de lo incierto.
- Evita soluciones lineales cuando el sistema requiere intervenciones adaptativas.
- Propone escenarios, indicadores tempranos y una intervencion de bajo riesgo para aprender.
Formato recomendado:
1. Mapa del sistema.
2. Tensiones y bucles.
3. Escenarios posibles.
4. Intervencion recomendada.
5. Indicadores para monitorear.
Estilo: profundo, ordenado y accionable.""",
    "METACOGNITIVO": """Eres el Agente Metacognitivo de OCEAN: entrenador de pensamiento, aprendizaje y autoevaluacion.
Objetivo: ayudar al usuario a observar como piensa, aprende, decide y se equivoca, para mejorar su metodo.
Metodo:
- Detecta la estrategia mental actual del usuario: memoria, comprension, aplicacion, analisis, sintesis o evaluacion.
- Senala posibles sesgos cognitivos, lagunas de atencion, carga mental o malos habitos de estudio.
- Convierte la tarea en un plan de aprendizaje con objetivos, practica, retroalimentacion y criterio de dominio.
- Propone una tecnica concreta: Feynman, retrieval practice, spaced repetition, mapa conceptual, diario de errores, checklist o rubrica.
- Pide al usuario una autoevaluacion breve cuando sea util.
Formato recomendado:
1. Que esta pasando en tu forma de pensar.
2. Estrategia recomendada.
3. Ejercicio de 5 a 15 minutos.
4. Criterio para saber que mejoraste.
Estilo: reflexivo, motivador, concreto y sin psicologizar de mas.""",
    "INVESTIGADOR": """Eres el Agente Investigador de OCEAN: especialista en investigacion, lectura critica, sintesis de evidencia y diseno metodologico.
Objetivo: convertir una duda en una ruta de investigacion clara, verificable y util.
Metodo:
- Define la pregunta de investigacion y sus terminos clave.
- Distingue conocimiento confirmado, hipotesis, datos faltantes y supuestos.
- Propone fuentes, criterios de calidad, variables y metodologia adecuada.
- Si no tienes acceso a fuentes actuales, dilo con claridad y sugiere que se use busqueda, archivos o RAG antes de concluir.
- Sintetiza evidencia sin inventar citas, autores, papers, leyes ni datos.
- Entrega una ruta de trabajo: que buscar, como evaluarlo y como convertirlo en conclusion.
Formato recomendado:
1. Pregunta refinada.
2. Hipotesis o enfoques.
3. Fuentes y evidencia requerida.
4. Metodo de analisis.
5. Entregable sugerido.
Estilo: sobrio, verificable, metodico y transparente sobre incertidumbre.""",
}

AGENT_META = {
    "SOCRATICO": {"label": "Agente Socratico", "tone": "guia reflexiva"},
    "CRITICO": {"label": "Agente Critico", "tone": "evaluacion rigurosa"},
    "INTERACCIONISTA": {"label": "Agente Interaccionista", "tone": "comunicacion y mediacion"},
    "COMPLEJO": {"label": "Agente Complejo", "tone": "pensamiento sistemico"},
    "METACOGNITIVO": {"label": "Agente Metacognitivo", "tone": "aprendizaje consciente"},
    "INVESTIGADOR": {"label": "Agente Investigador", "tone": "evidencia y metodo"},
}

PROVIDERS = {
    "local": {"label": "LLM Local QuantLabs", "requires_token": False},
    "openai": {"label": "OpenAI", "requires_token": True},
    "anthropic": {"label": "Anthropic", "requires_token": True},
    "deepseek": {"label": "DeepSeek", "requires_token": True},
}


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _route_by_rules(message: str) -> tuple[str, str]:
    text = str(message or "").lower()
    if any(token in text for token in ["investiga", "investigación", "investigacion", "paper", "papers", "fuente", "fuentes", "bibliografia", "bibliografía", "metodologia", "metodología", "hipotesis", "hipótesis", "estado del arte", "evidencia", "documento", "reporte"]):
        return "INVESTIGADOR", "rule_keyword"
    if any(token in text for token in ["aprendo", "aprender", "estudiar", "estudio", "memoriz", "habito", "hábito", "sesgo propio", "me distraigo", "no avanzo", "plan de aprendizaje", "autoevalu", "metacogn", "como pienso", "cómo pienso"]):
        return "METACOGNITIVO", "rule_keyword"
    if any(token in text for token in ["conversacion", "conversación", "mensaje", "equipo", "cliente", "jefe", "colaborador", "negoci", "conflicto", "mediacion", "mediación", "relacion", "relación", "interaccion", "interacción", "comunicacion", "comunicación"]):
        return "INTERACCIONISTA", "rule_keyword"
    if any(token in text for token in ["ensayo", "argumento", "opinion", "opinión", "critica", "crítica", "evalua", "evalúa", "falacia", "sesgo"]):
        return "CRITICO", "rule_keyword"
    if any(token in text for token in ["sistema", "complejo", "ecosistema", "economico", "económico", "social", "ecologico", "ecológico", "variables", "interdepend"]):
        return "COMPLEJO", "rule_keyword"
    if any(token in text for token in ["no entiendo", "explica", "enseñame", "ensename", "concepto", "que es", "qué es", "como funciona", "cómo funciona"]):
        return "SOCRATICO", "rule_keyword"
    return "SOCRATICO", "default"


def _normalize_agent(value: str) -> str:
    upper = str(value or "").upper()
    if "INVESTIGADOR" in upper:
        return "INVESTIGADOR"
    if "METACOGNITIVO" in upper or "METACOGNITIVO" in upper:
        return "METACOGNITIVO"
    if "INTERACCIONISTA" in upper:
        return "INTERACCIONISTA"
    if "CRITICO" in upper or "CRÍTICO" in upper:
        return "CRITICO"
    if "COMPLEJO" in upper:
        return "COMPLEJO"
    return "SOCRATICO"


def _call_local(messages: list[dict[str, str]], temperature: float, max_tokens: int) -> dict[str, Any]:
    completion = LlamaClient().chat(messages, temperature=temperature, max_tokens=max_tokens)
    return {
        "content": normalize_utf8_text(completion.get("content") or ""),
        "finish_reason": completion.get("finish_reason"),
        "usage": completion.get("usage") or {},
        "model": completion.get("model") or "llm-local",
    }


def _call_openai_compatible(base_url: str, model: str, messages: list[dict[str, str]], token: str, temperature: float, max_tokens: int) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False},
        timeout=(10, 90),
    )
    response.raise_for_status()
    payload = response.json()
    choice = payload["choices"][0]
    return {
        "content": normalize_utf8_text(choice["message"]["content"]),
        "finish_reason": choice.get("finish_reason"),
        "usage": payload.get("usage") or {},
        "model": payload.get("model") or model,
    }


def _call_anthropic(messages: list[dict[str, str]], token: str, model: str, temperature: float, max_tokens: int) -> dict[str, Any]:
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user_messages = [m for m in messages if m.get("role") != "system"]
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"model": model, "system": system, "messages": user_messages, "temperature": temperature, "max_tokens": max_tokens},
        timeout=(10, 90),
    )
    response.raise_for_status()
    payload = response.json()
    parts = payload.get("content") or []
    content = "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")
    return {"content": normalize_utf8_text(content), "finish_reason": payload.get("stop_reason"), "usage": payload.get("usage") or {}, "model": payload.get("model") or model}


def call_ocean_llm(provider: str, messages: list[dict[str, str]], token: str = "", model: str = "", temperature: float = 0.4, max_tokens: int = 500) -> dict[str, Any]:
    provider = provider if provider in PROVIDERS else "local"
    if provider == "local":
        return _call_local(messages, temperature, max_tokens)
    if not token:
        raise ValueError(f"token_required_for_{provider}")
    if provider == "openai":
        return _call_openai_compatible("https://api.openai.com/v1", model or "gpt-4o-mini", messages, token, temperature, max_tokens)
    if provider == "deepseek":
        return _call_openai_compatible("https://api.deepseek.com/v1", model or "deepseek-chat", messages, token, temperature, max_tokens)
    if provider == "anthropic":
        return _call_anthropic(messages, token, model or "claude-3-5-haiku-20241022", temperature, max_tokens)
    raise ValueError("unsupported_provider")


@ocean_bp.get("/models")
@require_auth({"admin", "teacher", "trader"})
def ocean_models():
    return jsonify({
        "providers": PROVIDERS,
        "agents": AGENT_META,
        "local_model": settings.llama_base_url,
    })


@ocean_bp.post("/chat")
@rate_limit(settings.rate_limit_per_minute)
@require_auth({"admin", "teacher", "trader"})
def ocean_chat():
    started = time.perf_counter()
    data = request.get_json(force=True) or {}
    message = str(data.get("message") or "").strip()
    provider = str(data.get("provider") or "local").strip().lower()
    token = str(data.get("token") or data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    force_agent = str(data.get("agent") or "auto").strip().upper()

    if not message:
        return jsonify({"error": "message_required"}), 400
    if provider not in PROVIDERS:
        return jsonify({"error": "unsupported_provider"}), 400

    try:
        if force_agent in AGENT_PROMPTS:
            agent_type, route_source = force_agent, "manual"
        else:
            agent_type, route_source = _route_by_rules(message)
            if route_source == "default":
                router = call_ocean_llm(provider, _messages(ROUTER_PROMPT, f"Mensaje del usuario: {message}"), token, model, temperature=0.0, max_tokens=12)
                agent_type, route_source = _normalize_agent(router["content"]), "llm_router"

        prompt = AGENT_PROMPTS[agent_type]
        requested_max_tokens = int(data.get("max_tokens") or 900)
        completion = call_ocean_llm(provider, _messages(prompt, message), token, model, temperature=float(data.get("temperature") or 0.45), max_tokens=requested_max_tokens)
        finish_reason = str(completion.get("finish_reason") or "").lower()
        usage = completion.get("usage") or {}
        completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("tokens_predicted") or 0
        incomplete = finish_reason in {"length", "max_tokens", "model_length"} or (completion_tokens and completion_tokens >= requested_max_tokens - 8)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return jsonify({
            "agent_type": agent_type,
            "agent": AGENT_META[agent_type],
            "provider": provider,
            "model": completion.get("model"),
            "route_source": route_source,
            "response": completion["content"],
            "finish_reason": completion.get("finish_reason"),
            "incomplete": bool(incomplete),
            "usage": usage,
            "elapsed_ms": elapsed_ms,
        })
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        return jsonify({"error": "provider_http_error", "detail": detail}), min(status, 599)
    except Exception as exc:
        return jsonify({"error": "ocean_chat_failed", "detail": str(exc)}), 500
