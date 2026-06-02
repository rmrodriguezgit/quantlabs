from __future__ import annotations
import json
from dataclasses import dataclass
from core.models import SessionState
from config import settings
from tools.registry import ToolRegistry
from runtime.agent_loop import AgentLoop
from runtime.llm import LlamaClient

@dataclass
class AgentContext:
    state: SessionState
    tools: ToolRegistry
    role: str | None = None

class BaseAgent:
    name='base'
    workflow=None
    instructions=''
    def act(self, objective: str, ctx: AgentContext) -> dict:
        outcome = AgentLoop(max_steps=settings.max_agent_steps).run(objective, ctx, self.instructions, self.workflow)
        return {'agent': self.name, 'objective': objective, 'result': outcome['final'], 'events': outcome['events'], 'usage': outcome.get('usage', {}), 'last_usage': outcome.get('last_usage', {})}

    def hybrid_finalize(
        self,
        objective: str,
        draft: str,
        evidence=None,
        *,
        mode: str = 'analysis',
        preserve_evidence: bool = True,
        max_evidence_chars: int = 9000,
    ) -> dict:
        evidence_text = json.dumps(evidence, ensure_ascii=False, default=str)[:max_evidence_chars]
        system = (
            'Eres la capa de síntesis LLM Local de QuantLabs Harness. '
            'Trabajas sobre evidencia producida por herramientas locales y reglas del especialista. '
            'No inventes datos, no cambies candados de riesgo, no autorices ejecución real y no contradigas la evidencia. '
            'Devuelve exclusivamente JSON válido: {"final":"respuesta final en español"}. '
            'Incluye una línea "LLM Local:" indicando cómo ayudaste a sintetizar.'
        )
        user = (
            f'Especialista: {self.name}\n'
            f'Modo híbrido: {mode}\n'
            f'Objetivo del usuario:\n{objective}\n\n'
            f'Borrador determinístico del especialista:\n{draft[:max_evidence_chars]}\n\n'
            f'Evidencia estructurada:\n{evidence_text}\n\n'
            'Redacta una respuesta clara, útil y accionable. Si hay tablas o métricas en el borrador, respétalas y no alteres números.'
        )
        try:
            completion = LlamaClient().chat([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ], temperature=0.2)
            raw = completion.get('content') or ''
            try:
                parsed = json.loads(raw)
                final = str(parsed.get('final') or '').strip()
            except json.JSONDecodeError:
                final = raw.strip()
            if not final:
                final = draft
            if preserve_evidence and draft and draft not in final:
                final = f"{final}\n\nEvidencia del especialista:\n{draft}"
            usage = completion.get('usage') or {}
            return {
                'result': final,
                'usage': usage,
                'last_usage': usage,
                'event': {'step': 'llm_synthesis', 'decision': {'action': 'llm_local_synthesis', 'mode': mode}, 'result': {'name': 'llm_local', 'ok': True, 'model': completion.get('model'), 'usage': usage}},
            }
        except Exception as exc:
            return {
                'result': draft,
                'usage': {},
                'last_usage': {},
                'event': {'step': 'llm_synthesis', 'decision': {'action': 'llm_local_synthesis', 'mode': mode}, 'result': {'name': 'llm_local', 'ok': False, 'error': str(exc)[:240]}},
            }
