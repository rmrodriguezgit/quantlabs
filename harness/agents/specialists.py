from datetime import datetime
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from observability import ValidationCollector

from .base import BaseAgent

class PlannerAgent(BaseAgent):
    name='planner'
    workflow='planning'
    instructions='''Rol: PLANNER.
Eres el arquitecto de decisiones. Convierte objetivos ambiguos en planes ejecutables, verificables y medibles.

Prioriza siempre:
1. objetivo
2. supuestos
3. restricciones
4. dependencias
5. secuencia de trabajo
6. riesgos y mitigaciones
7. metrica de exito
8. siguiente paso unico

No implementes, no ejecutes herramientas operativas y no tomes decisiones financieras. Cuando falte evidencia, solicita una investigacion concreta o deriva al agente adecuado.'''

class CodingAgent(BaseAgent):
    name='coding'
    workflow='coding'
    instructions='''Rol: CODING.
Eres el ingeniero de implementacion. Convierte requerimientos aprobados en cambios tecnicos seguros, pequenos, probados y reversibles.

Antes de cambiar algo identifica componentes, impacto, pruebas y rollback. Si la tarea menciona GPU, CUDA, Torch, entrenamiento, LSTM, Jupyter o notebooks, usa primero jupyter_gpu para verificar el entorno.

No expongas secretos, no ejecutes trading live y no modifiques produccion sin validacion y ruta de reversa.'''

    def act(self, objective: str, ctx) -> dict:
        if self._should_use_jupyter_gpu(objective):
            code = self._gpu_probe_code(objective)
            result = ctx.tools.execute('jupyter_gpu', role=ctx.role, code=code, timeout=120).model_dump()
            final = self._format_jupyter_gpu_response(result.get('output') or {})
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {'action': 'tool', 'tool': 'jupyter_gpu', 'arguments': {'purpose': 'gpu_cuda_torch_priority_probe'}},
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        return super().act(objective, ctx)

    def _should_use_jupyter_gpu(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return any(token in text for token in ['gpu', 'cuda', 'torch', 'pytorch', 'deep learning', 'lstm', 'tensor', 'entrenamiento', 'entrena', 'train', 'jupyter', 'notebook'])

    def _gpu_probe_code(self, objective: str) -> str:
        objective_json = __import__('json').dumps(str(objective or '')[:1000], ensure_ascii=False)
        return f"""
import json
import torch
payload = {{
    'objective': {objective_json},
    'python_executable': __import__('sys').executable,
    'torch_version': torch.__version__,
    'cuda_available': torch.cuda.is_available(),
    'cuda_device_count': torch.cuda.device_count(),
    'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
}}
if torch.cuda.is_available():
    x = torch.randn((512, 512), device='cuda')
    y = x @ x.T
    payload['cuda_smoke_mean'] = float(y.mean().detach().cpu())
print(json.dumps(payload, ensure_ascii=False, indent=2))
"""

    def _format_jupyter_gpu_response(self, output: dict) -> str:
        stdout = (output.get('stdout') or '').strip()
        stderr = (output.get('stderr') or '').strip()
        code = output.get('code')
        if code == 0:
            return 'Jupyter GPU priorizado y verificado para esta petición.\n\n' + stdout
        return 'No pude ejecutar correctamente en Jupyter GPU.\n\nSTDERR:\n' + (stderr or 'sin error disponible')


class Codex4UAgent(CodingAgent):
    name='codex4u'
    workflow='ubuntu_server_programming'
    instructions='''Rol: CODEX4U.
Eres programador especialista para servidores Ubuntu. Trabajas con Docker, Python, shell scripts, JavaScript, Node.js, HTML y CSS.

Prioriza diagnostico reproducible, cambios pequenos, comandos no destructivos, permisos correctos, servicios verificables y rollback claro.

Cuando el modelo activo sea Qwen Coder, eres el especialista preferido para implementacion. Antes de editar revisa archivos reales, estado del servicio y dependencias. Despues valida con pruebas, sintaxis, logs o healthchecks. No expongas secretos, no rompas .env y no reinicies servicios criticos sin razon operativa.'''


class FileAnalystAgent(BaseAgent):
    name='file_analyst'
    workflow='private_document_analysis'
    instructions='''Rol: FILE_ANALYST.
Analizas archivos privados con el microservicio local file_analyst. No uses APIs externas de IA.
Prioriza resumen ejecutivo, interpretación, riesgos, conclusiones y plan de acción.'''

    def act(self, objective: str, ctx) -> dict:
        file_id = self._extract_file_id(objective)
        mode = 'specialist' if any(token in str(objective).lower() for token in ['profundo', 'specialist', 'contrato', 'legal', 'riesgo', 'auditoria', 'auditoría']) else 'chatbot'
        result = ctx.tools.execute(
            'file_analyst',
            role=ctx.role,
            user_id=(ctx.state.metadata or {}).get('owner_id') or 'shared',
            action='analyze_file' if file_id else 'analyze_text',
            file_id=file_id,
            text=None if file_id else objective,
            mode=mode,
            language='es',
        ).model_dump()
        output = result.get('output') or {}
        final = self._format_file_analysis(output) if result.get('ok') else f"File Analyst no pudo analizar: {result.get('error')}"
        return {
            'agent': self.name,
            'objective': objective,
            'result': final,
            'events': [{
                'step': 1,
                'decision': {'action': 'tool', 'tool': 'file_analyst', 'arguments': {'file_id': file_id, 'mode': mode}},
                'result': result,
            }],
            'usage': {},
            'last_usage': {},
        }

    def _extract_file_id(self, objective: str) -> str | None:
        text = str(objective or '')
        match = re.search(r'\bfile[_ -]?id\s*[:=]\s*([0-9a-fA-F-]{24,})', text)
        if match:
            return match.group(1)
        match = re.search(r'\barchivo\s+([0-9a-fA-F-]{24,})', text)
        return match.group(1) if match else None

    def _format_file_analysis(self, output: dict) -> str:
        lines = [
            'File Analyst completado.',
            '',
            f"Resumen: {output.get('summary','sin resumen')}",
            '',
            f"Interpretación: {output.get('interpretation','sin interpretación')}",
            '',
            'Observaciones:',
        ]
        for item in (output.get('observations') or [])[:8]:
            lines.append(f"- [{item.get('severity','info')}] {item.get('detail','')}")
        lines.extend(['', 'Conclusiones:'])
        for item in (output.get('conclusions') or [])[:8]:
            lines.append(f"- {item}")
        lines.extend(['', 'Plan de acción:'])
        for item in (output.get('action_plan') or [])[:8]:
            suffix = f" · Responsable: {item.get('responsible')}" if item.get('responsible') else ''
            lines.append(f"{item.get('priority','-')}. {item.get('action','')}{suffix}")
        metadata = output.get('metadata') or {}
        lines.extend([
            '',
            f"Motor: {metadata.get('analysis_engine','n/d')} · Modelo: {metadata.get('model','n/d')} · Palabras: {metadata.get('word_count','n/d')}",
        ])
        return '\n'.join(lines)


class PolymrktAgent(BaseAgent):
    name='polymrkt'
    workflow='polymarket_signal_flow'
    instructions='''Rol: POLYMRKT.
Eres el especialista de Polymarket BTC Up/Down. Produces decision, evidencia, bloqueo y artefactos auditables usando senal 5m/15m, microestructura CLOB, contexto macro, Kelly y validacion.

Reglas base: confidence >= 0.80, edge >= 0.03, spread <= 0.08, ask_size >= 1, seconds_to_close >= 60 y una operacion por ventana. Nunca firmas ni envias ordenes live; execution y preflight conservan esa frontera.'''

    def act(self, objective: str, ctx) -> dict:
        events = []
        signal_result = ctx.tools.execute(
            'polymarket',
            role=ctx.role,
            action='btc_updown_5m15m_coordinated_signal',
            asset='btc',
            threshold=0.8,
            candle_interval='5m',
            lookback_window='1d',
            lookback=288,
            prediction_candle_interval='1m',
            prediction_lookback=90,
            min_edge=0.03,
            max_spread=0.08,
            min_ask_size=1,
            min_seconds_to_close=45,
        ).model_dump()
        events.append({'step': 1, 'decision': {'action': 'tool', 'tool': 'polymarket', 'arguments': {'action': 'btc_updown_5m15m_coordinated_signal', 'asset': 'btc'}}, 'result': signal_result})
        research_result = ctx.tools.execute(
            'dexter_research',
            role=ctx.role,
            objective=f'Polymarket BTC Up/Down macro context: {objective}',
            tickers=['BTC-USD'],
            horizon='6mo',
            session_id=ctx.state.session_id,
        ).model_dump()
        events.append({'step': 2, 'decision': {'action': 'tool', 'tool': 'dexter_research', 'arguments': {'tickers': ['BTC-USD'], 'horizon': '6mo'}}, 'result': research_result})
        try:
            snapshot = ValidationCollector().snapshot()
        except Exception as exc:
            snapshot = {'error': str(exc)}
        events.append({'step': 3, 'decision': {'action': 'observability_snapshot'}, 'result': {'ok': not bool(snapshot.get('error')), 'agents': len(snapshot.get('agents') or []), 'error': snapshot.get('error')}})
        signal = signal_result.get('output') or {}
        research = research_result.get('output') or {}
        artifact = self._write_signal_artifact(ctx.state.session_id, objective, signal, research, snapshot)
        if artifact not in ctx.state.artifacts:
            ctx.state.artifacts.append(artifact)
        for item in (research.get('artifacts') or {}).values():
            if item and item not in ctx.state.artifacts:
                ctx.state.artifacts.append(item)
        if self._prefers_hybrid_prediction_simulation(objective):
            final = self._format_hybrid_prediction_simulation(signal, artifact)
        elif self._prefers_btc_updown_table(objective):
            final = FinanceAgent()._format_btc_updown_response(signal)
        else:
            final = self._format_signal_flow(signal, research, artifact)
        return {'agent': self.name, 'objective': objective, 'result': final, 'events': events, 'usage': {}, 'last_usage': {}}

    def _prefers_hybrid_prediction_simulation(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return (
            ('simula' in text or 'simulación' in text or 'simulacion' in text)
            and ('predicción' in text or 'prediccion' in text or 'predictor' in text or 'modelo híbrido' in text or 'modelo hibrido' in text)
            and ('polymarket' in text or 'btc' in text or 'bitcoin' in text)
        )

    def _prefers_btc_updown_table(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return (
            'btc-updown-5m' in text
            or 'btc-updown-15m' in text
            or 'chainlink_1m_bounded_nowcast' in text
            or 'mismo criterio del cron polymarket' in text
        )

    def _format_hybrid_prediction_simulation(self, signal: dict, artifact: str) -> str:
        decision = signal.get('side') if signal.get('action') == 'TRADE' else 'NO TRADE'
        lines = [
            f'Decisión simulada: {decision}',
            '',
            'Simulación predictiva Polymarket BTC Up/Down',
            '',
            'Modelo activo: hybrid_chainlink_technical_nowcast',
            'Alcance: solo predicción y filtros; no ejecuta órdenes, no firma CLOB, no toca posiciones abiertas.',
            '',
            '| Intervalo | Ventana ET | Countdown | Lado predicho | Prob final | Nowcast UP | Técnico UP | Peso técnico | Modelo técnico | Ask | Edge | Pasa filtros | Riesgo |',
            '|---|---|---:|---|---:|---:|---:|---:|---|---:|---:|---|---|',
        ]
        candidates = signal.get('candidates') or []
        signals_by_interval = {item.get('interval'): item for item in signal.get('signals') or []}
        for candidate in candidates:
            interval = candidate.get('interval')
            raw_signal = signals_by_interval.get(interval) or {}
            components = candidate.get('model_components') or raw_signal.get('model_components') or {}
            technical = raw_signal.get('technical') or {}
            micro = candidate.get('microstructure') or {}
            lines.append(
                f"| {self._cell(interval)} | "
                f"{self._cell(candidate.get('window_et'))} | "
                f"{self._cell(candidate.get('countdown'))} | "
                f"{self._cell(candidate.get('preferred_side'))} | "
                f"{self._pct(candidate.get('probability'))} | "
                f"{self._pct(components.get('nowcast_probability_up'))} | "
                f"{self._pct(components.get('technical_probability_up'))} | "
                f"{self._pct(components.get('technical_weight'))} | "
                f"{self._cell(technical.get('status') or components.get('technical_status'))} | "
                f"{self._num(micro.get('ask'))} | "
                f"{self._num(candidate.get('edge'))} | "
                f"{'sí' if candidate.get('passes_filters') else 'no'} | "
                f"{self._cell(', '.join(candidate.get('reasons') or []) or 'sin bloqueo')} |"
            )
        if not candidates:
            lines.append('| — | — | — | — | — | — | — | — | — | — | — | no | sin candidatos |')
        lines.extend([
            '',
            'Lectura:',
            '- Prob final combina nowcast Chainlink 1m con score técnico inspirado en el notebook.',
            '- El modelo técnico entrenado es secundario; si falta o falla, el sistema usa reglas técnicas y fallback al nowcast.',
            '- TRADE simulado solo aparece si pasa confianza, edge, spread, profundidad y tiempo al cierre.',
            '',
            f"Artefacto auditoría: {artifact}",
        ])
        return '\n'.join(lines)

    def _write_signal_artifact(self, session_id: str, objective: str, signal: dict, research: dict, snapshot: dict) -> str:
        root = Path('storage/artifacts/polymrkt') / self._safe(session_id)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        path = root / f'{stamp}_signal_flow.json'
        payload = {
            'agent': self.name,
            'objective': objective,
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'signal': signal,
            'research': research,
            'observability': snapshot,
            'execution': {'signed_order': False, 'reason': 'live trading and EIP-712 signing remain blocked by default'},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def _format_signal_flow(self, signal: dict, research: dict, artifact: str) -> str:
        side = signal.get('side') or 'NONE'
        action = signal.get('action') or 'NO_TRADE'
        decision = side if action == 'TRADE' and side in {'UP','DOWN'} else 'NO TRADE'
        lines = [
            f'Decisión: {decision}',
            '',
            'Polymrkt Signal Flow:',
            '',
            '| Etapa | Estado | Evidencia |',
            '|---|---|---|',
            f"| World event | Observado | {self._cell('Objetivo del usuario como evento inicial')} |",
            f"| Polyscope + Polywhaler | Pendiente | {self._cell('Conectar whale/order-flow cuando tengamos fuente on-chain/CLOB agregada')} |",
            f"| Dexter research + macro context | Activo parcial | {self._cell(research.get('thesis') or (research.get('report') or {}).get('thesis') or 'Contexto macro vía Dexter research')} |",
            f"| Binance Collector + fair value | Activo | {self._cell(signal.get('strategy') or 'BTC Up/Down coordinated signal')} |",
            f"| Crucix Polygon on-chain | Pendiente | {self._cell('No conectado todavía; marcado como filtro futuro')} |",
            f"| Claude Squad / debate | Activo parcial | {self._cell(self._debate_line(signal))} |",
            f"| Risk check | Activo | {self._cell(self._risk_line(signal))} |",
            f"| Backtest validator | Parcial | {self._cell('Usar paper_trading/polybot antes de live; ejecución live bloqueada')} |",
            f"| py-clob-client signed order | Bloqueado | {self._cell('No se firma EIP-712 ni se envía orden sin habilitación explícita')} |",
            '',
            '| Intervalo | Ventana ET | Side | Probabilidad | Edge | Ask | Spread | Pasa filtros | Razones |',
            '|---|---|---|---:|---:|---:|---:|---|---|',
        ]
        for candidate in signal.get('candidates') or []:
            micro = candidate.get('microstructure') or {}
            lines.append(
                f"| {self._cell(candidate.get('interval'))} | {self._cell(candidate.get('window_et'))} | "
                f"{self._cell(candidate.get('preferred_side'))} | {self._pct(candidate.get('probability'))} | "
                f"{self._num(candidate.get('edge'))} | {self._num(micro.get('ask'))} | {self._num(micro.get('spread'))} | "
                f"{'sí' if candidate.get('passes_filters') else 'no'} | {self._cell(', '.join(candidate.get('reasons') or []))} |"
            )
        if not signal.get('candidates'):
            lines.append('| — | — | — | — | — | — | — | no | sin candidatos |')
        reasons = signal.get('reasons') or []
        lines.extend([
            '',
            f"Razones coordinador: {', '.join(reasons) if reasons else 'sin bloqueos coordinados'}",
            f"Artefacto: {artifact}",
            '',
            'Nota: Dexter aporta investigación; Polymrkt/Execution conservan la frontera de riesgo y CLOB.',
        ])
        return '\n'.join(lines)

    def _debate_line(self, signal: dict) -> str:
        candidates = signal.get('candidates') or []
        sides = [c.get('preferred_side') for c in candidates if c.get('preferred_side')]
        if not sides:
            return 'sin lado dominante'
        if len(set(sides)) == 1:
            return f'bull/bear convergen en {sides[0]}'
        return f'conflicto de dirección: {", ".join(sides)}'

    def _risk_line(self, signal: dict) -> str:
        filters = signal.get('filters') or {}
        return f"threshold {signal.get('threshold')} · min_edge {filters.get('min_edge')} · max_spread {filters.get('max_spread')}"

    def _safe(self, value: str) -> str:
        return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or 'default'))[:120] or 'default'

    def _cell(self, value) -> str:
        return str(value if value not in {None, ''} else '—').replace('|', '/').replace('\n', ' ')

    def _num(self, value) -> str:
        try:
            return f'{float(value):.4f}'
        except (TypeError, ValueError):
            return '—'

    def _pct(self, value) -> str:
        try:
            return f'{float(value):.1%}'
        except (TypeError, ValueError):
            return '—'


class DexterAgent(BaseAgent):
    name='dexter'
    workflow='research'
    instructions="""Rol: DEXTER RESEARCH.
Eres el investigador financiero profundo. Entregas tesis, evidencia, catalizadores, escenarios, riesgos, confianza y handoff para validacion.

Separa hechos, inferencias e incertidumbre. No ejecutes operaciones, no cambies CRONs, no modifiques estrategias live y no presentes investigacion como autorizacion para operar."""

    def act(self, objective: str, ctx) -> dict:
        tickers = self._extract_tickers(objective)
        result = ctx.tools.execute(
            'dexter_research',
            role=ctx.role,
            objective=objective,
            tickers=tickers,
            horizon='6mo',
            session_id=ctx.state.session_id,
        ).model_dump()
        output = result.get('output') or {}
        for artifact in (output.get('artifacts') or {}).values():
            if artifact and artifact not in ctx.state.artifacts:
                ctx.state.artifacts.append(artifact)
        final = self._format_response(output)
        return {
            'agent': self.name,
            'objective': objective,
            'result': final,
            'events': [{
                'step': 1,
                'decision': {'action': 'tool', 'tool': 'dexter_research', 'arguments': {'tickers': tickers, 'horizon': '6mo'}},
                'result': result,
            }],
            'usage': {},
            'last_usage': {},
        }

    def _extract_tickers(self, objective: str) -> list[str]:
        text = str(objective or '').upper()
        symbols = []
        if re.search(r'\bBTC|BITCOIN\b', text):
            symbols.append('BTC-USD')
        if re.search(r'\bETH|ETHEREUM\b', text):
            symbols.append('ETH-USD')
        for token in re.findall(r'\b[A-Z]{1,6}(?:[/\-]?USDT|[-/]USD)?\b', text):
            normalized = token.replace('/', '').replace('USDT', '-USD')
            if normalized in {'BTC', 'ETH', 'SOL'}:
                normalized = f'{normalized}-USD'
            if normalized in {'DEXTER','RESEARCH','TESIS','RIESGO','RIESGOS','ANALISIS','ANÁLISIS','PARA','CON','LIVE','TRADE','Y','O','DE','DEL','LA','EL','LOS','LAS','UN','UNA','EN'}:
                continue
            if normalized not in symbols:
                symbols.append(normalized)
        return symbols[:8]

    def _format_response(self, output: dict) -> str:
        report = output.get('report') if isinstance(output.get('report'), dict) else output
        thesis = report.get('thesis') or output.get('thesis') or 'Sin tesis suficiente.'
        recommendation = report.get('recommendation') or output.get('recommendation') or 'OBSERVE'
        confidence = report.get('confidence') or output.get('confidence') or 0
        risks = report.get('risks') or output.get('risks') or []
        evidence = report.get('evidence') or output.get('evidence') or []
        artifacts = output.get('artifacts') or report.get('artifacts') or {}
        lines = [
            'Dexter Research:',
            f'- Recomendación: {recommendation}',
            f'- Confianza: {confidence}',
            f'- Tesis: {thesis}',
            '',
            'Evidencia clave:',
        ]
        for item in evidence[:5]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('ticker') or 'Dato'}: {item.get('finding') or item.get('summary') or item}")
            else:
                lines.append(f'- {item}')
        if not evidence:
            lines.append('- Sin evidencia cuantitativa suficiente; usar como investigación preliminar.')
        lines.extend(['', 'Riesgos:'])
        for risk in risks[:5]:
            lines.append(f'- {risk}')
        if not risks:
            lines.append('- No se registraron riesgos específicos.')
        lines.extend([
            '',
            'Handoff:',
            '- Dexter queda en modo research_only.',
            '- Polymrkt/Execution deben validar cualquier señal antes de CLOB.',
        ])
        if artifacts:
            lines.append(f"- Artefactos: {', '.join(str(v) for v in artifacts.values() if v)}")
        return '\n'.join(lines)


class FinanceAgent(BaseAgent):
    name='finance'
    workflow='finance'
    instructions='''Rol: FINANCE.
Eres el analista cuantitativo de mercados. Evalua oportunidades con datos, probabilidad, edge, liquidez, riesgo temporal y sizing.

Para Polymarket usa reglas deterministicas: confidence >= 80%, edge >= 3%, spread <= 8%, profundidad ask >= 1, al menos 60s al cierre, stake manual fijo de 1/2/3 USDT, SL por 75% de ventana con PnL negativo y TP +100%. Nunca ejecutes dinero real; entrega recomendacion y razon de bloqueo cuando no haya trade.'''

    def act(self, objective: str, ctx) -> dict:
        if self._should_use_deep_research(objective):
            tickers = self._extract_research_tickers(objective)
            result = ctx.tools.execute(
                'dexter_research',
                role=ctx.role,
                objective=objective,
                tickers=tickers,
                horizon='6mo',
                session_id=ctx.state.session_id,
            ).model_dump()
            output = result.get('output') or {}
            for artifact in (output.get('artifacts') or {}).values():
                if artifact and artifact not in ctx.state.artifacts:
                    ctx.state.artifacts.append(artifact)
            final = self._format_deep_research_response(output)
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {'action': 'tool', 'tool': 'dexter_research', 'arguments': {'tickers': tickers, 'horizon': '6mo'}},
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        if self._should_train_btc_deep_model(objective):
            start_et, end_et = self._extract_et_window(objective)
            result = ctx.tools.execute(
                'polymarket',
                role=ctx.role,
                action='btc_updown_deep_train',
                interval='5m',
                lookback_window='1d',
                window_start_et=start_et or '2026-05-23 23:50:00 EDT',
                window_end_et=end_et or '2026-05-23 23:55:00 EDT',
                sequence_length=30,
                hidden_size=16,
            ).model_dump()
            final = self._format_btc_deep_train_response(result.get('output') or {})
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {'action': 'tool', 'tool': 'polymarket', 'arguments': {'action': 'btc_updown_deep_train', 'interval': '5m', 'lookback_window': '1d', 'window_start_et': start_et, 'window_end_et': end_et}},
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        if self._should_use_paper_trading(objective):
            symbols = self._extract_symbols(objective)
            result = ctx.tools.execute(
                'paper_trading',
                role=ctx.role,
                action='run_cycle',
                mode='paper',
                venues=['polymarket', 'mexc'],
                mexc_tickers=symbols,
                mexc_interval='15m',
                bankroll_usdt=10000,
                max_stake_pct=0.05,
                polymarket_stake_usdt=1,
                threshold=0.8,
                polymarket_intervals=['15m', '5m'],
                candle_interval='5m',
                lookback_window='1d',
                lookback=288,
            ).model_dump()
            final = self._format_paper_trading_response(result.get('output') or {}, symbols)
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {
                        'action': 'tool',
                        'tool': 'paper_trading',
                        'arguments': {
                            'action': 'run_cycle',
                            'mode': 'paper',
                            'venues': ['polymarket', 'mexc'],
                            'mexc_tickers': symbols,
                            'threshold': 0.8,
                        },
                    },
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        if self._should_use_btc_updown(objective):
            result = ctx.tools.execute(
                'polymarket',
                role=ctx.role,
                action='btc_updown_5m15m_coordinated_signal',
                asset='btc',
                threshold=0.8,
                candle_interval='5m',
                lookback_window='1d',
                lookback=288,
                prediction_candle_interval='1m',
                prediction_lookback=90,
                min_edge=0.03,
                max_spread=0.08,
                min_ask_size=1,
                min_seconds_to_close=45,
            ).model_dump()
            final = self._format_btc_updown_response(result.get('output') or {})
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {
                        'action': 'tool',
                        'tool': 'polymarket',
                        'arguments': {
                            'action': 'btc_updown_5m15m_coordinated_signal',
                            'asset': 'btc',
                            'threshold': 0.8,
                            'candle_interval': '5m',
                            'lookback_window': '1d',
                            'lookback': 288,
                            'prediction_candle_interval': '1m',
                            'prediction_lookback': 90,
                        },
                    },
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        if self._should_use_mexc_spot_long(objective):
            symbols = self._extract_symbols(objective)
            result = ctx.tools.execute(
                'mexc_spot',
                role=ctx.role,
                action='scan_spot_long_candidates',
                tickers=symbols,
                interval='15m',
                limit=200,
            ).model_dump()
            final = self._format_mexc_spot_response(result.get('output') or {}, symbols)
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {
                        'action': 'tool',
                        'tool': 'mexc_spot',
                        'arguments': {'action': 'scan_spot_long_candidates', 'tickers': symbols, 'interval': '15m', 'limit': 200},
                    },
                    'result': result,
                }],
                'usage': {},
                'last_usage': {},
            }
        return super().act(objective, ctx)

    def _should_use_deep_research(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return any(token in text for token in [
            'dexter', 'deep research', 'investigación profunda', 'investigacion profunda',
            'research profundo', 'tesis', 'dcf', 'due diligence', 'analisis profundo',
            'análisis profundo',
        ])

    def _extract_research_tickers(self, objective: str) -> list[str]:
        text = str(objective or '')
        matches = re.findall(r'\b[A-Z]{1,6}(?:[/\-]?USDT|[-/]USD)?\b', text.upper())
        tickers = []
        if re.search(r'\bBTC|BITCOIN\b', text, flags=re.IGNORECASE):
            tickers.append('BTC-USD')
        if re.search(r'\bETH|ETHEREUM\b', text, flags=re.IGNORECASE):
            tickers.append('ETH-USD')
        blacklist = {'DCF','API','USD','USDT','IA','AI','ET','EDT','MEXC','RSI','MACD','VWAP','UP','DOWN','DEXTER','DE','DEL','DAME','TESIS','RIESGO','RIESGOS','EVIDENCIA','INVESTIGACION','INVESTIGACIÓN','PROFUNDA','PROFUNDO','ANALISIS','ANÁLISIS','Y','O','PARA','CON'}
        for match in matches:
            normalized = match.replace('/', '').replace('USDT', '-USD')
            if normalized in {'BTC', 'ETH', 'SOL'}:
                normalized = f'{normalized}-USD'
            if match in blacklist or normalized in blacklist:
                continue
            if normalized not in tickers:
                tickers.append(normalized)
        return tickers[:8]

    def _format_deep_research_response(self, output: dict) -> str:
        if not output or output.get('status') != 'completed':
            return f"Deep Research no disponible: {output.get('status','sin salida') if output else 'sin salida'}"
        artifacts = output.get('artifacts') or {}
        lines = [
            'Deep Research financiero completado.',
            '',
            f"Tesis: {output.get('thesis','—')}",
            f"Recomendación: {output.get('recommendation','—')}",
            f"Confianza: {self._pct(output.get('confidence'))}",
            '',
            '| Ticker | Evidencia | Peso |',
            '|---|---|---|',
        ]
        for item in output.get('evidence') or []:
            lines.append(f"| {self._cell(item.get('ticker'))} | {self._cell(item.get('finding'))} | {self._cell(item.get('weight'))} |")
        lines.extend(['', 'Riesgos:'])
        for risk in output.get('risks') or []:
            lines.append(f"- {risk}")
        lines.extend([
            '',
            f"Scratchpad: {artifacts.get('scratchpad')}",
            f"Reporte: {artifacts.get('report')}",
        ])
        return '\n'.join(lines)

    def _should_train_btc_deep_model(self, objective: str) -> bool:
        text = (objective or '').lower()
        return ('polymarket' in text and 'btc' in text and ('lstm' in text or 'deep learning' in text or 'aprendizaje profundo' in text or 'modelo avanzado' in text) and ('entrena' in text or 'aprendizaje' in text or 'guard' in text or 'modelo' in text))

    def _extract_et_window(self, objective: str):
        text = objective or ''
        match = re.search(r'(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s*(?:EDT|EST)?)\s*-\s*(\d{2}:\d{2}(?::\d{2})?\s*(?:EDT|EST)?)', text, flags=re.IGNORECASE)
        if not match:
            return None, None
        start = match.group(1).strip()
        end_tail = match.group(2).strip()
        date = start[:10]
        zone = 'EDT' if 'EDT' in start.upper() else ('EST' if 'EST' in start.upper() else 'EDT')
        if not re.match(r'20\d{2}-', end_tail):
            end_tail = f'{date} {end_tail}'
        if 'EDT' not in end_tail.upper() and 'EST' not in end_tail.upper():
            end_tail = f'{end_tail} {zone}'
        if len(start.split()) == 2:
            start = f'{start} {zone}'
        if re.match(r'.*\d{2}:\d{2}\s+(EDT|EST)$', start, flags=re.IGNORECASE):
            start = start.replace(' EDT', ':00 EDT').replace(' EST', ':00 EST') if len(start.split()[1].split(':')) == 2 else start
        if re.match(r'.*\d{2}:\d{2}\s+(EDT|EST)$', end_tail, flags=re.IGNORECASE):
            end_tail = end_tail.replace(' EDT', ':00 EDT').replace(' EST', ':00 EST') if len(end_tail.split()[1].split(':')) == 2 else end_tail
        return start, end_tail

    def _format_btc_deep_train_response(self, output: dict) -> str:
        if not output or output.get('status') in {'unavailable','insufficient_data','insufficient_samples','missing_model'}:
            return f"No pude entrenar el modelo BTC Up/Down 5m: {output.get('status','sin salida')} · {output.get('error') or output.get('hint') or ''}"
        metrics = output.get('metrics') or {}
        target = output.get('target_window') or {}
        lines = [
            'Modelo BTC Up/Down 5m entrenado y guardado.',
            f"Artefacto: {output.get('model_path')}",
            f"Dataset: {output.get('dataset_path')}",
            f"Muestras entrenamiento: {metrics.get('train_samples')} · Accuracy test: {metrics.get('accuracy')} · Log loss: {metrics.get('log_loss')}",
            '',
            '| Ventana ET | Precio a superar | Cierre | Movimiento USD | Ganador real | Prob UP modelo | Lado modelo |',
            '|---|---:|---:|---:|---|---:|---|',
            f"| {target.get('window_et','—')} | {self._num(target.get('price_to_beat_reference'))} | {self._num(target.get('close_price'))} | {self._num(target.get('movement_usd'))} | {target.get('winner','—')} | {self._pct(target.get('model_probability_up'))} | {target.get('model_side','—')} |",
            '',
            'Uso recomendado: aplicar como señal secundaria del agente finance, nunca como orden automática. Confirma book, edge, Kelly y tiempo al cierre antes de cualquier decisión paper/live.',
        ]
        return '\n'.join(lines)

    def _should_use_paper_trading(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return (
            ('mode=paper' in text or 'paper' in text or 'paper trading' in text)
            and ('polymarket' in text or 'mexc' in text)
            and any(token in text for token in ['automat', 'continuo', 'runner', 'ciclo', 'scan'])
        )

    def _should_use_btc_updown(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return (
            ('polymarket' in text)
            and ('bitcoin' in text or 'btc' in text)
            and any(token in text for token in ['5m', '15m', 'scalping', 'up', 'down', 'probabilidad'])
        )

    def _should_use_mexc_spot_long(self, objective: str) -> bool:
        text = str(objective or '').lower()
        return (
            ('mexc' in text or 'spot' in text)
            and ('rsi' in text or 'macd' in text or 'vwap' in text or 'long' in text)
            and ('/usdt' in text or 'usdt' in text)
        )

    def _extract_symbols(self, objective: str) -> list[str]:
        matches = re.findall(r'\b[A-Z0-9]{2,12}\s*/?\s*USDT\b', str(objective or '').upper())
        symbols = []
        for match in matches:
            symbol = match.replace(' ', '').replace('/', '')
            if symbol not in symbols:
                symbols.append(symbol)
        return symbols[:40]

    def _format_paper_trading_response(self, output: dict, requested_symbols: list[str]) -> str:
        orders = output.get('orders') or []
        observations = output.get('observations') or []
        errors = output.get('errors') or []
        now_cdmx = datetime.now(ZoneInfo('America/Mexico_City')).strftime('%Y-%m-%d %H:%M:%S CST')
        lines = [
            f"Paper Trading: {len(orders)} señales simuladas",
            '',
            f"Actualización: {now_cdmx}",
            f"Modo: {output.get('mode', 'paper').upper()} | Bankroll simulado: {self._num(output.get('bankroll_usdt'))} USDT | Tope stake: {float(output.get('max_stake_pct') or 0):.1%}",
            f"Auditoría: {self._cell(output.get('audit_path'))}",
            '',
            '| Venue | Mercado/Ticker | Intervalo | Señal | Precio | Probabilidad | Kelly | Stake | Riesgo/Razón |',
            '|-------|----------------|-----------|-------|--------|--------------|-------|-------|--------------|',
        ]
        for order in orders:
            venue = order.get('venue')
            label = order.get('symbol') or order.get('market')
            signal = order.get('side') or order.get('signal')
            price = order.get('price')
            probability = order.get('probability')
            kelly = order.get('fractional_kelly')
            stake = order.get('stake_usdt')
            reason = order.get('risk') or order.get('reason') or order.get('execution')
            lines.append(
                f"| {self._cell(venue)} | {self._cell(label)} | {self._cell(order.get('interval'))} | "
                f"{self._cell(signal)} | {self._num(price)} | {self._pct(probability)} | "
                f"{self._pct(kelly)} | {self._num(stake)} | {self._cell(reason)} |"
            )
        if not orders:
            lines.append('| — | — | — | NO TRADE | — | — | — | 0 | Sin señales que pasen filtros |')
        lines.extend([
            '',
            f"Observaciones sin orden simulada: {len(observations)} | Errores: {len(errors)} | Tickers MEXC: {len(requested_symbols)}",
            'MODE=paper: no ejecuta órdenes reales en MEXC ni Polymarket. Solo registra señales simuladas y auditoría.',
        ])
        if errors:
            lines.append('')
            lines.append('Errores:')
            for err in errors[:5]:
                lines.append(f"- {self._cell(err.get('venue'))}: {self._cell(err.get('error'))}")
        return '\n'.join(lines)

    def _format_mexc_spot_response(self, output: dict, requested_symbols: list[str]) -> str:
        rows = output.get('results') or []
        buy_count = sum(1 for row in rows if row.get('signal') == 'BUY')
        sell_count = sum(1 for row in rows if row.get('signal') == 'SELL')
        now_cdmx = datetime.now(ZoneInfo('America/Mexico_City')).strftime('%Y-%m-%d %H:%M:%S CST')
        decision = f'BUY {buy_count} / SELL {sell_count}' if (buy_count or sell_count) else 'NONE'
        lines = [
            f'Señal MEXC Spot: {decision}',
            '',
            f'Actualización: {now_cdmx}',
            f'Intervalo: {output.get("interval", "15m")} | Universo: {len(requested_symbols)} tickers | BUY: {buy_count} | SELL: {sell_count}',
            '',
            'Criterios BUY: RSI<=30, MACD hist<0, precio<VWAP. Criterios SELL: RSI>=70, MACD hist>0, precio>VWAP. Filtros extra: volumen relativo, EMA50, Bollinger y ATR/riesgo.',
            '',
            '| Ticker | Señal | Precio | RSI | MACD Hist | VWAP | <VWAP | >VWAP | Vol Rel | BB Inf | BB Sup | EMA20 | EMA50 | BUY Score | SELL Score | Riesgo | Motivo |',
            '|--------|-------|--------|-----|-----------|------|-------|-------|---------|--------|--------|-------|-------|-----------|------------|--------|--------|',
        ]
        for row in rows:
            if row.get('error'):
                lines.append(f"| {self._cell(row.get('symbol'))} | NONE | N/D | N/D | N/D | N/D | N/D | N/D | N/D | N/D | N/D | N/D | N/D | 0/6 | 0/5 | Alto | {self._cell(row.get('error'))} |")
                continue
            lines.append(
                f"| {self._cell(row.get('symbol'))} | "
                f"{row.get('signal', 'NONE')} | "
                f"{self._num(row.get('price'))} | "
                f"{self._num(row.get('rsi'))} | "
                f"{self._num(row.get('macd_histogram'))} | "
                f"{self._num(row.get('vwap'))} | "
                f"{self._yes(row.get('price_below_vwap'))} | "
                f"{self._yes(row.get('price_above_vwap'))} | "
                f"{self._num(row.get('volume_ratio'))} | "
                f"{self._yes(row.get('lower_band_touch'))} | "
                f"{self._yes(row.get('upper_band_touch'))} | "
                f"{self._num(row.get('ema20'))} | "
                f"{self._num(row.get('ema50'))} | "
                f"{row.get('setup_score', 0)}/6 | "
                f"{row.get('sell_score', 0)}/5 | "
                f"{self._cell(row.get('risk'))} | "
                f"{self._mexc_reason(row)} |"
            )
        lines.extend([
            '',
            'BUY/SELL solo indican aviso técnico para revisión; no ejecutan órdenes. Si no cumple los criterios base, queda en NONE.',
        ])
        return '\n'.join(lines)

    def _mexc_reason(self, row: dict) -> str:
        if row.get('signal') == 'BUY':
            return 'BUY: cumple sobreventa y filtros'
        if row.get('signal') == 'SELL':
            return 'SELL: cumple sobrecompra y filtros'
        missing = []
        if not row.get('rsi_oversold'):
            missing.append('BUY RSI>30')
        if not row.get('macd_negative'):
            missing.append('BUY MACD no negativo')
        if not row.get('price_below_vwap'):
            missing.append('BUY precio>=VWAP')
        sell_missing = []
        if not row.get('rsi_overbought'):
            sell_missing.append('SELL RSI<70')
        if not row.get('macd_positive'):
            sell_missing.append('SELL MACD no positivo')
        if not row.get('price_above_vwap'):
            sell_missing.append('SELL precio<=VWAP')
        if len(sell_missing) < len(missing):
            return ', '.join(sell_missing[:3])
        return ', '.join(missing[:3]) or 'Filtros extra no confirman'

    def _yes(self, value) -> str:
        return 'Sí' if value else 'No'

    def _format_btc_updown_response(self, output: dict) -> str:
        signals = output.get('signals') or []
        markets = {m.get('interval'): m for m in output.get('markets') or []}
        if output.get('candidates') is not None:
            decision = output.get('side') if output.get('action') == 'TRADE' else 'NO TRADE'
            reason = self._coordinated_reason(output)
        else:
            decision, reason = self._final_trade_decision(signals, markets)
        now_cdmx = datetime.now(ZoneInfo('America/Mexico_City')).strftime('%Y-%m-%d %H:%M:%S CST')
        now_et = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S %Z')
        lines = [
            f'Decisión: {decision}',
            '',
            f'Actualización: {now_cdmx} / {now_et}',
            'Modelo: señal coordinada BTC Up/Down 5m + 15m; predicción híbrida Chainlink nowcast + score técnico contra Precio a superar fijo de Polymarket.',
            'Sizing: Kelly fraccional conservador; no ejecuta órdenes.',
            '',
            '| Mercado | Intervalo | Ventana ET | Countdown | Precio a superar | Cotización actual | Predicción cierre | Delta | Bid Up | Ask Up | Bid Down | Ask Down | Probabilidad | Certeza | Trade | Kelly | Stake Máx | Liquidez | Riesgo |',
            '|---------|-----------|------------|-----------|------------------|-------------------|-------------------|-------|--------|--------|----------|----------|--------------|---------|-------|-------|-----------|----------|--------|',
        ]
        for signal in signals:
            market = markets.get(signal.get('interval')) or {}
            books = self._books_by_outcome(market)
            probability = self._probability_text(signal)
            trade = self._trade_for_signal(signal, market)
            kelly = self._kelly_for_signal(signal, books, trade)
            lines.append(
                '| Bitcoin | '
                f"{self._cell(signal.get('interval'))} | "
                f"{self._cell(self._window_et(signal))} | "
                f"{self._cell(signal.get('countdown'))} | "
                f"{self._num(signal.get('price_to_beat_reference') or signal.get('start_price_reference'))} | "
                f"{self._num(signal.get('current_price_reference'))} | "
                f"{self._num(signal.get('forecast_price_at_close'))} | "
                f"{self._num(signal.get('price_delta_reference'))} | "
                f"{self._num((books.get('Up') or {}).get('bid'))} | "
                f"{self._num((books.get('Up') or {}).get('ask'))} | "
                f"{self._num((books.get('Down') or {}).get('bid'))} | "
                f"{self._num((books.get('Down') or {}).get('ask'))} | "
                f"{probability} | "
                f"{self._confidence_text(signal)} | "
                f"{trade} | "
                f"{kelly['label']} | "
                f"{kelly['stake']} | "
                f"{self._num(market.get('liquidity'))} | "
                f"{self._risk(signal, market, books)} |"
            )
        lines.extend([
            '',
            reason,
            'Kelly: fraccion = (p - precio) / (1 - precio), usando el ask del lado elegido como costo de entrada. Se muestra recortado a 25% de Kelly y con tope operativo de 5% de banca por la volatilidad de ventanas 5m/15m.',
            'Nota: Polymarket resuelve con Chainlink BTC/USD; precio a superar y cotización actual se sincronizan con velas Chainlink/Polymarket. No ejecuta órdenes.',
        ])
        return '\n'.join(lines)

    def _coordinated_reason(self, output: dict) -> str:
        if output.get('action') == 'TRADE':
            return f"Entrada candidata {output.get('side')}: 5m y 15m pasan filtros de dirección, edge, spread, profundidad y tiempo al cierre."
        reasons = output.get('reasons') or []
        candidate_reasons = []
        for candidate in output.get('candidates') or []:
            if candidate.get('reasons'):
                candidate_reasons.append(f"{candidate.get('interval')}: {', '.join(candidate.get('reasons')[:3])}")
        detail = '; '.join(reasons + candidate_reasons)
        return f"No hay entrada: {detail or 'la coordinación 5m/15m no confirmó una operación con filtros completos.'}"

    def _final_trade_decision(self, signals: list[dict], markets: dict) -> tuple[str, str]:
        eligible = []
        for signal in signals:
            market = markets.get(signal.get('interval')) or {}
            confidence = signal.get('confidence')
            if (
                signal.get('meets_threshold')
                and isinstance(confidence, int | float)
                and confidence >= 0.8
                and signal.get('preferred_side') in {'Up', 'Down'}
                and (market.get('seconds_to_close') or 0) >= 45
            ):
                eligible.append(signal)
        if not eligible:
            return (
                'NO TRADE',
                'No hay entrada: ninguna ventana confirma probabilidad >= 80% con tiempo suficiente antes del cierre.',
            )
        best = max(eligible, key=lambda s: s.get('confidence') or 0)
        side = 'UP' if best.get('preferred_side') == 'Up' else 'DOWN'
        return (
            side,
            f"Entrada candidata {side}: la ventana {best.get('interval')} supera el umbral de 80% y conserva tiempo operativo antes del cierre.",
        )

    def _books_by_outcome(self, market: dict) -> dict:
        out = {}
        for token in market.get('tokens') or []:
            book = token.get('book') or {}
            out[token.get('outcome')] = {
                'bid': (book.get('best_bid') or {}).get('price'),
                'ask': (book.get('best_ask') or {}).get('price'),
            }
        return out

    def _probability_text(self, signal: dict) -> str:
        confidence = signal.get('confidence')
        side = signal.get('preferred_side')
        if not isinstance(confidence, int | float) or not side:
            return 'N/D'
        up_probability = (signal.get('prophet') or {}).get('up_probability')
        if isinstance(up_probability, int | float):
            return f"UP {up_probability:.1%} / DOWN {1 - up_probability:.1%}"
        normalized = 'UP' if side == 'Up' else 'DOWN'
        return f"{normalized} {confidence:.1%}"

    def _confidence_text(self, signal: dict) -> str:
        confidence = signal.get('confidence')
        if not isinstance(confidence, int | float):
            return 'N/D'
        return f'{confidence:.1%}'

    def _trade_for_signal(self, signal: dict, market: dict) -> str:
        confidence = signal.get('confidence')
        if (
            signal.get('meets_threshold')
            and isinstance(confidence, int | float)
            and confidence >= 0.8
            and (market.get('seconds_to_close') or 0) >= 45
        ):
            if signal.get('preferred_side') == 'Up':
                return 'UP'
            if signal.get('preferred_side') == 'Down':
                return 'DOWN'
        return 'NONE'

    def _kelly_for_signal(self, signal: dict, books: dict, trade: str) -> dict[str, str]:
        if trade not in {'UP', 'DOWN'}:
            return {'label': 'N/D', 'stake': '0%'}
        side = 'Up' if trade == 'UP' else 'Down'
        probability = self._side_probability(signal, side)
        ask = self._float((books.get(side) or {}).get('ask'))
        if probability is None or ask is None or ask <= 0 or ask >= 1:
            return {'label': 'N/D', 'stake': '0%'}
        full_kelly = (probability - ask) / (1 - ask)
        if full_kelly <= 0:
            return {'label': '0.0% edge<=0', 'stake': '0%'}
        fractional = full_kelly * 0.25
        capped = min(fractional, 0.05)
        return {
            'label': f'{full_kelly:.1%} full / {fractional:.1%} 1/4',
            'stake': f'{capped:.1%} banca',
        }

    def _side_probability(self, signal: dict, side: str):
        up_probability = (signal.get('prophet') or {}).get('up_probability')
        if isinstance(up_probability, int | float):
            return up_probability if side == 'Up' else 1 - up_probability
        confidence = signal.get('confidence')
        if isinstance(confidence, int | float) and signal.get('preferred_side') == side:
            return confidence
        return None

    def _window_et(self, signal: dict) -> str:
        start = signal.get('start_time_et') or ''
        end = signal.get('end_time_et') or ''
        if start and end:
            return f'{start} - {end}'
        return start or end or 'N/D'

    def _risk(self, signal: dict, market: dict, books: dict) -> str:
        if (market.get('seconds_to_close') or 0) < 45:
            return 'Alto'
        if not isinstance(signal.get('confidence'), int | float):
            return 'Alto'
        spreads = []
        for outcome in ['Up', 'Down']:
            bid = self._float((books.get(outcome) or {}).get('bid'))
            ask = self._float((books.get(outcome) or {}).get('ask'))
            if bid is not None and ask is not None:
                spreads.append(max(0, ask - bid))
        if any(spread > 0.05 for spread in spreads):
            return 'Alto'
        if not signal.get('meets_threshold'):
            return 'Moderado'
        return 'Controlado'

    def _cell(self, value) -> str:
        return str(value if value not in (None, '') else 'N/D').replace('|', '/')

    def _num(self, value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 'N/D'
        if number == 0:
            return '0'
        if abs(number) < 0.01:
            return f'{number:.8f}'.rstrip('0').rstrip('.')
        if abs(number) < 1:
            return f'{number:.6f}'.rstrip('0').rstrip('.')
        return f'{number:.2f}'

    def _pct(self, value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 'N/D'
        return f'{number:.1%}'

    def _float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

class ResearchAgent(BaseAgent):
    name='research'
    workflow='research'
    instructions='''Rol: RESEARCH.
Eres el investigador. Separa hechos, inferencias, incertidumbre y proximos experimentos. No vendas certeza falsa.

Si el tema requiere datos actuales, indica que debe verificarse con fuentes primarias. Para Polymarket puedes usar endpoints_status, search_markets, market_detail, order_book o recent_trades.'''

class ValidationAgent(BaseAgent):
    name='validation'
    workflow='validation'
    instructions='''Rol: VALIDATION.
Eres la capa institucional de observabilidad, auditoria y riesgo de QuantLab AI Capital.

Audita cronjobs, bots, senales, metricas, logs, infraestructura, GPU, Docker, performance y health score. Separa hechos observados, alertas, riesgos, criterio pase/fallo y recomendacion operativa. Si falta evidencia, di exactamente que prueba falta.'''

    def act(self, objective: str, ctx) -> dict:
        text = str(objective or '').lower()
        if any(token in text for token in ['cron', 'observability', 'observabilidad', 'status', 'health', 'logs', 'performance', 'reporte', 'audita', 'monitorea', 'transaccion', 'transacción', 'trade', 'orden', 'regla', 'rules']):
            collector = ValidationCollector()
            snapshot = collector.snapshot()
            transactions = collector.transactions(limit=80)
            final = self._format_validation_snapshot(snapshot, transactions)
            return {
                'agent': self.name,
                'objective': objective,
                'result': final,
                'events': [{
                    'step': 1,
                    'decision': {'action': 'observability_snapshot'},
                    'result': {'name': 'validation_observability', 'ok': True, 'agents': len(snapshot.get('agents') or [])},
                }],
                'usage': {},
                'last_usage': {},
            }
        return super().act(objective, ctx)

    def _format_validation_snapshot(self, snapshot: dict, transactions: dict | None = None) -> str:
        summary = snapshot.get('summary') or {}
        agents = snapshot.get('agents') or []
        infra = snapshot.get('infrastructure') or {}
        alerts = snapshot.get('alerts') or []
        lines = [
            'Validation Report: QuantLab AI Capital',
            '',
            f"Health Score: {summary.get('health_score', 0)} | Agentes: {summary.get('agents_total', 0)} | Activos: {summary.get('agents_active', 0)} | Errores: {summary.get('agents_error', 0)}",
            f"Infra: CPU {infra.get('cpu_percent', 0)}% | RAM {infra.get('ram_percent', 0)}% | GPU {self._gpu_label(infra.get('gpu') or [])}",
            '',
            '| Agente | Mode | Status | Uptime | Mercado | Señal | Confianza | PnL | Accuracy | Health |',
            '|--------|------|--------|--------|---------|-------|-----------|-----|----------|--------|',
        ]
        for agent in agents:
            lines.append(
                f"| {self._cell(agent.get('name') or agent.get('agent'))} | {self._cell(agent.get('mode'))} | {self._cell(agent.get('status'))} | "
                f"{self._cell(agent.get('uptime'))} | {self._cell(agent.get('market'))} | {self._cell(agent.get('signal'))} | "
                f"{self._num(agent.get('confidence'))}% | {self._num(agent.get('pnl'))} | {self._num(agent.get('accuracy'))}% | {self._cell(agent.get('health_score'))} |"
            )
        if not agents:
            lines.append('| — | — | — | — | — | — | — | — | — | 100 |')
        lines.extend(['', 'Alertas:'])
        if alerts:
            lines.extend([f"- {self._cell(alert)}" for alert in alerts[:12]])
        else:
            lines.append('- Sin alertas críticas.')
        all_tx_rows = (transactions or {}).get('transactions') or []
        tx_rows = [tx for tx in all_tx_rows if str(tx.get('venue') or '').lower() == 'polymarket']
        exposure = sum(float(tx.get('stake_usdt') or 0) for tx in tx_rows)
        pnl = sum(float(tx.get('pnl') or 0) for tx in tx_rows)

        def market_symbol(tx):
            interval = str(tx.get('interval') or ((tx.get('indicators') or {}).get('candidate') or {}).get('interval') or '').lower()
            if '15' in interval:
                return 'BTC-UP-DOWN_15m'
            if '5' in interval:
                return 'BTC-UP-DOWN_5m'
            return 'BTC-UP-DOWN'

        def price_to_beat(tx):
            indicators = tx.get('indicators') or {}
            candidate = indicators.get('candidate') or {}
            return indicators.get('price_to_beat_reference') or candidate.get('price_to_beat_reference') or tx.get('price')

        def predicted_price(tx):
            indicators = tx.get('indicators') or {}
            candidate = indicators.get('candidate') or {}
            return indicators.get('forecast_price_at_close') or candidate.get('forecast_price_at_close') or tx.get('forecast_price_at_close')

        def close_price(tx):
            indicators = tx.get('indicators') or {}
            candidate = indicators.get('candidate') or {}
            return indicators.get('final_price_reference') or candidate.get('final_price_reference') or tx.get('final_price_reference')

        def window_time(tx):
            value = tx.get('window') or (((tx.get('indicators') or {}).get('candidate') or {}).get('window_et')) or tx.get('timestamp')
            return value or 'hora actual'

        def trade_reason(tx):
            indicators = tx.get('indicators') or {}
            candidate = indicators.get('candidate') or {}
            reasons = candidate.get('reasons') or []
            return ', '.join(reasons) or tx.get('risk') or '—'

        def outcome(tx):
            status = str(tx.get('status') or '').lower()
            side = str(tx.get('side') or 'NONE').upper()
            indicators = tx.get('indicators') or {}
            actual = str(indicators.get('winning_side') or indicators.get('actual_close_side') or '').upper()
            if actual:
                if side == 'NONE':
                    return f'Cerró {actual} / Sin trade'
                return f'Cerró {actual} / ' + ('Acierto' if side == actual else 'Error')
            if status == 'won':
                return 'Acierto'
            if status == 'lost':
                return 'Error'
            if status == 'no_trade' or side == 'NONE':
                return 'Pendiente / Sin trade'
            if float(tx.get('pnl') or 0) > 0:
                return 'Acierto'
            if float(tx.get('pnl') or 0) < 0:
                return 'Error'
            return 'Pendiente'

        lines.extend([
            '',
            f"Polymarket auditado: {len(tx_rows)} transacciones | Exposure paper: {self._num(exposure)} USDT | PnL paper: {self._num(pnl)} USDT",
            '',
            '| Hora ET | Venue | Mercado | Side | Mode | Status | Precio a superar | Precio predicho | Precio cierre | Stake | Confianza | Motivo | Acierto/Error | PnL paper |',
            '|---------|-------|---------|------|------|--------|------------------|-----------------|---------------|-------|-----------|--------|---------------|-----|',
        ])
        for tx in tx_rows[:12]:
            side = str(tx.get('side') or 'NONE').upper()
            if side not in {'UP', 'DOWN', 'NONE'}:
                side = 'NONE'
            lines.append(
                f"| {self._cell(window_time(tx))} | Polymarket | {self._cell(market_symbol(tx))} | "
                f"{self._cell(side)} | {self._cell(tx.get('mode'))} | {self._cell(tx.get('status'))} | "
                f"{self._num(price_to_beat(tx))} | {self._num(predicted_price(tx))} | {self._num(close_price(tx))} | {self._num(tx.get('stake_usdt'))} | "
                f"{self._num(tx.get('confidence'))}% | {self._cell(trade_reason(tx))} | {self._cell(outcome(tx))} | {self._num(tx.get('pnl'))} paper |"
            )
        if not tx_rows:
            lines.append('| hora actual | Polymarket | BTC-UP-DOWN | NONE | — | — | 0 | 0 | 0 | 0 | 0% | — | Sin trade | 0 paper |')
        rules = (agents[0].get('rules') if agents else None) or {}
        lines.extend(['', 'Reglas operativas vigentes:'])
        if rules:
            for name, rule in list(rules.items())[:5]:
                lines.append(f"- {self._cell(name)}: {self._cell(rule)}")
        else:
            lines.append('- Consulta /harness-api/v1/agents/rules para reglas editables del CRON.')
        lines.extend([
            '',
            'Endpoints institucionales disponibles: /harness-api/v1/agents/status, /health, /logs, /performance, /transactions, /rules y /report.',
        ])
        return '\n'.join(lines)

    def _gpu_label(self, gpus: list[dict]) -> str:
        if not gpus:
            return 'N/D'
        gpu = gpus[0]
        return f"{gpu.get('name', 'GPU')} {gpu.get('memory_percent', 0)}% VRAM"

    def _cell(self, value) -> str:
        return str(value if value not in (None, '') else 'N/D').replace('|', '/')

    def _num(self, value) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return '0'

class ExecutionAgent(BaseAgent):
    name='execution'
    workflow='deployment'
    instructions='''Rol: EXECUTION.
Eres el operador SRE/trading operations. Mantienes servicios y procesos de forma segura, verificable y reversible.

Antes de actuar verifica estado actual, PID/contenedor, logs, dependencias, impacto y rollback. Para acciones con dinero real exige preflight, saldo, riesgo, tamano, mercado activo y confirmacion explicita del usuario. Si algo falla, no ejecutes.'''
