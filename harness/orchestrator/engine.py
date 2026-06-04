from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import uuid

from core.models import Message, Role, AgentTask, TaskStatus
from agents.base import AgentContext
from agents.registry import AgentRegistry
from memory.store import SessionStore, ArtifactStore
from runtime.context import ContextManager
from runtime.text_encoding import normalize_utf8_text
from tools.registry import ToolRegistry
from telemetry.metrics import AGENT_RUNS, ACTIVE_SESSIONS
from telemetry.trajectory import append_trajectory
from policies.security import PromptInjectionGuard
from config import settings


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    return max(1, round(len(str(text or "")) / 4))


class HarnessEngine:
    def __init__(self):
        self.sessions = SessionStore()
        self.artifacts = ArtifactStore()
        self.tools = ToolRegistry()
        self.agents = AgentRegistry()
        self.context = ContextManager()
        self.guard = PromptInjectionGuard()

    def chat(
        self,
        session_id: str,
        prompt: str,
        agent: str = "planner",
        user_id="shared",
        role=None,
        display_prompt: str | None = None,
        user_message_metadata: dict | None = None,
    ):
        if self.guard.flagged(prompt):
            raise PermissionError("prompt injection pattern detected")

        display_prompt = display_prompt or prompt
        user_message_metadata = user_message_metadata or {}
        state = self.sessions.load(session_id, user_id)
        ACTIVE_SESSIONS.inc()
        state.messages.append(
            Message(role=Role.user, content=display_prompt, metadata=user_message_metadata)
        )
        self.sessions.maybe_title_from_first_prompt(state, display_prompt)
        task = AgentTask(id=str(uuid.uuid4()), objective=display_prompt, agent=agent, status=TaskStatus.running)
        task.metadata["started_at"] = utc_now()
        if prompt != display_prompt:
            task.metadata["context_enriched"] = True
        state.tasks.append(task)
        self.sessions.save(state, user_id)
        ctx = AgentContext(state=state, tools=self.tools, role=role)
        usage = {}
        result = {}
        response = ""

        try:
            result = self.agents.get(agent).act(prompt, ctx)
            response = self._synthesize(prompt, result)
            usage = result.get("usage") or {}
            last_usage = result.get("last_usage") or usage
            if not usage.get("completion_tokens"):
                usage = {
                    **usage,
                    "prompt_tokens": usage.get("prompt_tokens") or estimate_tokens(prompt),
                    "completion_tokens": estimate_tokens(response),
                }
                usage["total_tokens"] = usage.get("total_tokens") or (
                    usage["prompt_tokens"] + usage["completion_tokens"]
                )
            if not last_usage.get("prompt_tokens"):
                last_usage = {**last_usage, "prompt_tokens": usage.get("prompt_tokens", 0)}
            total_generated = (state.metadata.get("tokens_generated_total") or 0) + usage.get("completion_tokens", 0)
            state.metadata.update({
                "last_usage": usage,
                "last_prompt_tokens": last_usage.get("prompt_tokens", 0),
                "last_completion_tokens": usage.get("completion_tokens", 0),
                "tokens_generated_total": total_generated,
                "context_window": settings.model_context_window,
            })
            task.status = TaskStatus.completed
            AGENT_RUNS.labels(agent, "true").inc()
        except Exception as exc:
            task.status = TaskStatus.failed
            task.metadata["error"] = str(exc)
            response = f"Agent failure: {exc}"
            AGENT_RUNS.labels(agent, "false").inc()
        finally:
            task.metadata.update({
                "finished_at": utc_now(),
                "events": result.get("events") or [],
                "usage": usage,
            })
            try:
                task.metadata["trajectory_path"] = append_trajectory({
                    "session_id": state.session_id,
                    "task_id": task.id,
                    "agent": agent,
                    "role": role,
                    "status": task.status.value,
                    "objective": display_prompt,
                    "response": response,
                    "events": task.metadata["events"],
                    "usage": usage,
                })
            except Exception as exc:
                task.metadata["trajectory_error"] = str(exc)

            ACTIVE_SESSIONS.dec()

        state.messages.append(Message(role=Role.assistant, content=response))
        self.context.compact(state)
        self.sessions.save(state, user_id)
        return {
            "session_id": session_id,
            "response": response,
            "tasks": [t.model_dump() for t in state.tasks[-10:]],
            "artifacts": state.artifacts,
            "usage": usage,
            "metadata": state.metadata,
        }

    def _synthesize(self, prompt: str, result: dict):
        return self._clean_response(result.get("result", ""))

    def _clean_response(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "No obtuve una respuesta final del agente."

        parsed = self._parse_json_response(text)
        if isinstance(parsed, dict):
            final = str(parsed.get("final") or parsed.get("response") or "").strip()
            if final:
                return normalize_utf8_text(final)

        final_match = re.search(r'"final"\s*:\s*"(?P<final>.*)"\s*[,}]', text, flags=re.DOTALL)
        if final_match:
            final = final_match.group("final")
            final = final.replace("\\n", "\n").replace('\\"', '"').strip()
            if final:
                return normalize_utf8_text(final)

        text = re.sub(r"^\[[a-zA-Z0-9_-]+\]\s*", "", text).strip()
        text = re.sub(r'^\{?\s*"thought"\s*:\s*".*?"\s*,\s*"action"\s*:\s*"respond"\s*,\s*', "", text, flags=re.DOTALL)
        return normalize_utf8_text(text.strip()) or "No obtuve una respuesta final del agente."

    def _parse_json_response(self, text: str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
