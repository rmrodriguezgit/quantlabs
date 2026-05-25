from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class Role(str, Enum):
    user='user'; assistant='assistant'; system='system'; tool='tool'

class Message(BaseModel):
    role: Role
    content: str
    name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

class ToolResult(BaseModel):
    name: str
    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0

class TaskStatus(str, Enum):
    pending='pending'; running='running'; completed='completed'; failed='failed'

class AgentTask(BaseModel):
    id: str
    objective: str
    agent: str
    status: TaskStatus = TaskStatus.pending
    metadata: dict[str, Any] = Field(default_factory=dict)

class SessionState(BaseModel):
    session_id: str
    messages: list[Message] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    summary: str = ''
    metadata: dict[str, Any] = Field(default_factory=dict)
