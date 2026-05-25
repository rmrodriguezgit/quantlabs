from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from core.models import SessionState, TaskStatus
from config import settings

_SAFE_USER_RE = re.compile(r'[^A-Za-z0-9_.-]+')
_TITLE_STOPWORDS = {
    'analiza', 'analizar', 'ayudame', 'ayúdame', 'puedes', 'quiero', 'vamos',
    'hacer', 'para', 'con', 'del', 'los', 'las', 'una', 'uno', 'que', 'como',
    'cómo', 'por', 'favor', 'agente', 'finance', 'coding', 'planner',
}

def user_key(user_id) -> str:
    value = str(user_id or 'anonymous').strip() or 'anonymous'
    value = _SAFE_USER_RE.sub('_', value)[:96]
    return value or 'anonymous'


class SessionStore:
    def __init__(self):
        self.root = Path(settings.conversation_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.legacy_root = Path(settings.session_root)
        self.legacy_root.mkdir(parents=True, exist_ok=True)

    def _user_root(self, user_id) -> Path:
        path = self.root / user_key(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _path(self, user_id, session_id: str) -> Path:
        safe_session = _SAFE_USER_RE.sub('_', str(session_id or 'default'))[:120] or 'default'
        return self._user_root(user_id) / f'{safe_session}.json'

    def load(self, session_id: str, user_id='shared') -> SessionState:
        path = self._path(user_id, session_id)
        if path.exists():
            state = SessionState.model_validate_json(path.read_text())
            self._ensure_meta(state, user_id=user_id)
            if self._finalize_stale_tasks(state):
                self.save(state, user_id)
            return state
        state = SessionState(session_id=str(session_id or 'default'))
        self._ensure_meta(state, user_id=user_id)
        return state

    def save(self, state: SessionState, user_id='shared') -> None:
        self._ensure_meta(state, user_id=user_id)
        state.metadata['owner_id'] = user_key(user_id)
        state.metadata['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        self._path(user_id, state.session_id).write_text(state.model_dump_json(indent=2))

    def _finalize_stale_tasks(self, state: SessionState, timeout_minutes: int = 30) -> bool:
        changed = False
        now = datetime.now(UTC)
        for task in state.tasks:
            status = getattr(task.status, 'value', task.status)
            metadata = task.metadata or {}
            if status != TaskStatus.running.value or metadata.get('finished_at'):
                continue
            started_at = metadata.get('started_at')
            try:
                started = datetime.fromisoformat(str(started_at).replace('Z', '+00:00'))
            except (TypeError, ValueError):
                started = None
            if started and (now - started).total_seconds() < timeout_minutes * 60:
                continue
            task.status = TaskStatus.failed
            metadata['finished_at'] = now.isoformat().replace('+00:00', 'Z')
            metadata['error'] = metadata.get('error') or 'Ejecución marcada como detenida por timeout de sesión/API.'
            metadata['stale_finalized'] = True
            task.metadata = metadata
            changed = True
        return changed

    def create(self, user_id, title='Nueva conversación') -> SessionState:
        state = SessionState(session_id=str(uuid.uuid4()))
        self._ensure_meta(state, title, user_id=user_id)
        self.save(state, user_id)
        return state

    def list(self, user_id):
        user_root = self._user_root(user_id)
        items = []
        for path in user_root.glob('*.json'):
            try:
                state = SessionState.model_validate_json(path.read_text())
                self._ensure_meta(state, user_id=user_id)
                items.append({
                    'id': state.session_id,
                    'title': state.metadata.get('title') or 'Nueva conversación',
                    'created_at': state.metadata.get('created_at'),
                    'updated_at': state.metadata.get('updated_at'),
                    'messages': len(state.messages),
                })
            except Exception:
                pass
        return sorted(items, key=lambda x: x.get('updated_at') or '', reverse=True)

    def token_usage(self, user_id):
        user_root = self._user_root(user_id)
        totals = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'estimated_tokens': 0,
            'sessions': 0,
            'messages': 0,
            'tasks': 0,
            'last_activity_at': None,
        }
        by_session = []
        for path in user_root.glob('*.json'):
            try:
                state = SessionState.model_validate_json(path.read_text())
            except Exception:
                continue
            metadata = state.metadata or {}
            session_usage = {
                'id': state.session_id,
                'title': metadata.get('title') or self._derive_title(state),
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0,
                'estimated_tokens': 0,
                'messages': len(state.messages),
                'tasks': len(state.tasks),
                'updated_at': metadata.get('updated_at'),
            }
            for task in state.tasks:
                usage = (task.metadata or {}).get('usage') or {}
                prompt_tokens = int(usage.get('prompt_tokens') or 0)
                completion_tokens = int(usage.get('completion_tokens') or 0)
                total_tokens = int(usage.get('total_tokens') or prompt_tokens + completion_tokens)
                session_usage['prompt_tokens'] += prompt_tokens
                session_usage['completion_tokens'] += completion_tokens
                session_usage['total_tokens'] += total_tokens
            if session_usage['total_tokens'] == 0:
                session_usage['completion_tokens'] = int(metadata.get('tokens_generated_total') or 0)
                session_usage['prompt_tokens'] = int(metadata.get('last_prompt_tokens') or 0)
                session_usage['total_tokens'] = session_usage['prompt_tokens'] + session_usage['completion_tokens']
            if session_usage['total_tokens'] == 0:
                session_usage['estimated_tokens'] = sum(
                    max(1, round(len(str(msg.content or '')) / 4))
                    for msg in state.messages
                )
            totals['sessions'] += 1
            totals['messages'] += session_usage['messages']
            totals['tasks'] += session_usage['tasks']
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'estimated_tokens'):
                totals[key] += session_usage[key]
            updated_at = session_usage.get('updated_at')
            if updated_at and (not totals['last_activity_at'] or updated_at > totals['last_activity_at']):
                totals['last_activity_at'] = updated_at
            by_session.append(session_usage)
        by_session.sort(key=lambda item: item.get('updated_at') or '', reverse=True)
        return {'totals': totals, 'sessions': by_session[:25]}

    def rename(self, user_id, session_id: str, title: str):
        path = self._path(user_id, session_id)
        if not path.exists():
            return None
        state = self.load(session_id, user_id)
        state.metadata['title'] = title.strip()[:120] or 'Nueva conversación'
        self.save(state, user_id)
        return state

    def delete(self, user_id, session_id: str) -> bool:
        path = self._path(user_id, session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def maybe_title_from_first_prompt(self, state: SessionState, prompt: str) -> bool:
        current = str(state.metadata.get('title') or '').strip().lower()
        if current not in {'', 'nueva conversación', 'nueva conversacion'}:
            return False
        user_messages = [
            msg for msg in state.messages
            if str(msg.role.value if hasattr(msg.role, 'value') else msg.role) == 'user'
        ]
        if len(user_messages) != 1:
            return False
        title = self.title_from_prompt(prompt)
        if title == 'Nueva conversación':
            return False
        state.metadata['title'] = title
        return True

    def _ensure_meta(self, state: SessionState, title: str | None = None, user_id='shared'):
        now = datetime.utcnow().isoformat() + 'Z'
        state.metadata.setdefault('title', title or self._derive_title(state))
        state.metadata.setdefault('created_at', now)
        state.metadata.setdefault('updated_at', now)
        state.metadata.setdefault('owner_id', user_key(user_id))

    def _derive_title(self, state: SessionState):
        for msg in state.messages:
            if str(msg.role.value if hasattr(msg.role, 'value') else msg.role) == 'user':
                return msg.content[:80]
        return 'Nueva conversación'

    def title_from_prompt(self, prompt: str) -> str:
        text = re.sub(r'^\s*agente\s+\w+\s*:\s*', '', str(prompt or ''), flags=re.I)
        text = re.sub(r'\[[^\]]+\]|https?://\S+|[`*_#>{}\[\]()"\\\']', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            return 'Nueva conversación'
        lower = text.lower()
        pairs = []
        for match in re.findall(r'\b[A-Z0-9]{2,12}\s*/?\s*USDT\b', text.upper()):
            pair = match.replace(' ', '').replace('/', '')
            if pair not in pairs:
                pairs.append(pair)
        if 'mexc' in lower:
            return (f"MEXC Spot {', '.join(pairs[:3])}" if pairs else 'MEXC Spot scanner')[:60]
        if 'polymarket' in lower:
            return 'Polymarket BTC' if re.search(r'\bbtc|bitcoin\b', lower) else 'Polymarket mercados'
        if 'harness' in lower:
            return 'Mejoras del Harness'
        if 'server' in lower or 'servidor' in lower:
            return 'Revisión del servidor'
        words = [w for w in text.split() if len(w) > 2 and w.lower() not in _TITLE_STOPWORDS]
        title = ' '.join(words[:6]) or text[:60]
        return title[:1].upper() + title[1:60].rstrip(' .,;:!?')


class ArtifactStore:
    def __init__(self):
        self.root = Path(settings.artifact_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_text(self, name: str, content: str) -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)
