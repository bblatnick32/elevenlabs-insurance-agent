from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, NewType, Protocol

AgentId = NewType("AgentId", str)
type ToolHandler = Callable[[Mapping[str, object]], ToolCall]
type Item = AgentLine | AgentCorrection | UserLine | ToolCall
type Stop = Literal["ended", "dropped", "interrupted"] | Failed


@dataclass(frozen=True, slots=True)
class HttpExchange:
    method: Literal["GET", "POST"]
    url: str
    status: int
    response_text: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class CallFailed:
    reason: Literal["missing_parameter", "unexpected_parameter", "connection"]
    detail: str
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    result: HttpExchange | CallFailed


@dataclass(frozen=True, slots=True)
class AgentLine:
    text: str


@dataclass(frozen=True, slots=True)
class AgentCorrection:
    original: str
    corrected: str


@dataclass(frozen=True, slots=True)
class UserLine:
    text: str


@dataclass(frozen=True, slots=True)
class Failed:
    detail: str


@dataclass(frozen=True, slots=True)
class SessionRequest:
    agent_id: AgentId
    handlers: Mapping[str, ToolHandler]
    echo: Callable[[Item], None]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    conversation_id: str | None
    stop: Stop


class ConversationPort(Protocol):
    def session(self, request: SessionRequest) -> SessionRecord: ...
