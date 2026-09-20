import json
import sys
from collections.abc import Callable, Mapping
from typing import cast
from urllib.parse import urlparse

from agent.conversation import (
    AgentCorrection,
    AgentId,
    AgentLine,
    CallFailed,
    ConversationPort,
    Failed,
    HttpExchange,
    Item,
    SessionRecord,
    SessionRequest,
    ToolCall,
    ToolHandler,
    UserLine,
)


def run_agent(
    agent_id: AgentId,
    conversation: ConversationPort,
    handlers: Mapping[str, ToolHandler],
    base_url: str,
    echo: Callable[[Item], None] | None = None,
) -> int:
    emit = echo_line if echo is None else echo
    print(f"local API {base_url} (in-process, fresh fixture store, real clock)")
    print(
        "If macOS asks, allow microphone access for your terminal. "
        "Speak after the greeting. Ctrl+C hangs up."
    )
    record = conversation.session(SessionRequest(agent_id, handlers, emit))
    _report(record)
    return _exit_code(record)


def echo_line(item: Item) -> None:
    print(_render(item))


def _render(item: Item) -> str:
    match item:
        case AgentLine(text=text):
            return f"agent > {text}"
        case AgentCorrection(corrected=corrected):
            return f"agent ~ {corrected}"
        case UserLine(text=text):
            return f"you   > {text}"
        case ToolCall(name=name, result=HttpExchange() as exchange):
            return (
                f"tool  > {name} {exchange.method} {urlparse(exchange.url).path} "
                f"{exchange.status} {_result_kind(exchange.response_text)} "
                f"{exchange.elapsed_seconds:.2f}s"
            )
        case ToolCall(name=name, result=CallFailed() as failed):
            return f"tool  > {name} - - - {failed.reason} {failed.elapsed_seconds:.2f}s"
        case ToolCall():
            raise AssertionError("unreachable tool result")


def _result_kind(text: str) -> str:
    try:
        payload_object: object = json.loads(text)
    except json.JSONDecodeError:
        return "error"
    if not isinstance(payload_object, dict):
        return "error"
    payload = cast(dict[str, object], payload_object)
    kind = payload.get("kind")
    return kind if isinstance(kind, str) else "error"


def _report(record: SessionRecord) -> None:
    match record:
        case SessionRecord(conversation_id=None, stop=stop):
            print(
                f"no conversation id: the session never connected ({_stop_text(stop)})",
                file=sys.stderr,
            )
        case SessionRecord(conversation_id=conversation_id, stop=Failed(detail=detail)):
            print(f"conversation {conversation_id} failed: {detail}", file=sys.stderr)
        case SessionRecord(conversation_id=conversation_id, stop=stop):
            print(f"conversation {conversation_id} {stop}")


def _stop_text(stop: object) -> str:
    return stop.detail if isinstance(stop, Failed) else str(stop)


def _exit_code(record: SessionRecord) -> int:
    return 1 if record.conversation_id is None or isinstance(record.stop, Failed) else 0
