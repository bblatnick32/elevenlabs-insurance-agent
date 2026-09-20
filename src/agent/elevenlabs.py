import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    AudioInterface,
    ClientTools,
    Conversation,
)

from agent.conversation import (
    AgentCorrection,
    AgentLine,
    CallFailed,
    ConversationPort,
    Failed,
    HttpExchange,
    Item,
    SessionRecord,
    SessionRequest,
    Stop,
    ToolCall,
    ToolHandler,
    UserLine,
)


def open_conversation(
    client: ElevenLabs,
    audio: AudioInterface | None,
    *,
    requires_auth: bool,
) -> ConversationPort:
    return _SdkConversation(client, audio, requires_auth)


class _SdkConversation:
    def __init__(
        self,
        client: ElevenLabs,
        audio: AudioInterface | None,
        requires_auth: bool,
    ) -> None:
        self._client = client
        self._audio = audio
        self._requires_auth = requires_auth

    def session(self, request: SessionRequest) -> SessionRecord:
        ended = threading.Event()
        tools: Any = ClientTools()
        for name, handler in request.handlers.items():
            tools.register(name, _handler(handler, request.echo))
        audio = None if self._audio is None else _GuardedAudio(self._audio)
        conversation = Conversation(
            self._client,
            request.agent_id,
            requires_auth=self._requires_auth,
            audio_interface=audio,
            client_tools=tools,
            callback_agent_response=lambda text: request.echo(AgentLine(text)),
            callback_agent_response_correction=lambda original, corrected: request.echo(
                AgentCorrection(original, corrected)
            ),
            callback_user_transcript=lambda text: request.echo(UserLine(text)),
            callback_end_session=ended.set,
        )
        stopped = False

        def end() -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            with suppress(Exception):
                conversation.end_session()
            if audio is not None:
                with suppress(Exception):
                    audio.stop()

        try:
            conversation.start_session()
        except KeyboardInterrupt:
            end()
            return SessionRecord(None, "interrupted")
        except Exception as error:
            end()
            return SessionRecord(None, _failed(error))

        conversation_id: str | None
        stop: Stop
        try:
            conversation_id = conversation.wait_for_session_end()
            stop = "ended" if ended.is_set() else "dropped"
        except (KeyboardInterrupt, Exception) as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            if interrupted:
                print("hanging up")
            stop = "interrupted" if interrupted else _failed(error)
            end()
            try:
                conversation_id = conversation.wait_for_session_end()
            except Exception as wait_error:
                if interrupted:
                    return SessionRecord(None, _failed(wait_error))
                conversation_id = None
        finally:
            end()
        return SessionRecord(conversation_id, stop)


def _failed(error: BaseException) -> Failed:
    return Failed(type(error).__name__)


class ToolCallError(Exception):
    pass


def result_for_agent(call: ToolCall) -> str:
    match call.result:
        case HttpExchange(status=status, response_text=text) if 200 <= status < 300:
            return text or "{}"
        case HttpExchange(status=status, response_text=text):
            raise ToolCallError(f"local API {status}: {text}")
        case CallFailed(detail=detail):
            raise ToolCallError(detail)


def _handler(
    handler: ToolHandler, echo: Callable[[Item], None]
) -> Callable[[dict[str, object]], object]:
    def run(parameters: dict[str, object]) -> object:
        call = handler(parameters)
        echo(call)
        return result_for_agent(call)

    return run


class _GuardedAudio(AudioInterface):
    def __init__(self, inner: AudioInterface) -> None:
        self._inner = inner
        self._started = False
        self._stopped = False

    def start(self, input_callback: Callable[[bytes], None]) -> None:
        self._inner.start(input_callback)
        self._started = True

    def stop(self) -> None:
        if not self._started or self._stopped:
            return
        self._stopped = True
        self._inner.stop()

    def output(self, audio: bytes) -> None:
        self._inner.output(audio)

    def interrupt(self) -> None:
        self._inner.interrupt()
