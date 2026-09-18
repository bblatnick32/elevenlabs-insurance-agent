import json
import re
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Literal

import httpx
import pydantic
from elevenlabs import ElevenLabs
from elevenlabs.core.api_error import ApiError
from elevenlabs.core.request_options import RequestOptions
from elevenlabs.errors.not_found_error import NotFoundError
from elevenlabs.types.add_knowledge_base_response_model import (
    AddKnowledgeBaseResponseModel,
)
from elevenlabs.types.conversation_history_transcript_common_model_output import (
    ConversationHistoryTranscriptCommonModelOutput,
)
from elevenlabs.types.conversation_history_transcript_other_tools_result_common_model import (
    ConversationHistoryTranscriptOtherToolsResultCommonModel,
)
from elevenlabs.types.create_agent_response_model import CreateAgentResponseModel
from elevenlabs.types.create_agent_test_response_model import (
    CreateAgentTestResponseModel,
)
from elevenlabs.types.get_test_suite_invocation_response_model import (
    GetTestSuiteInvocationResponseModel,
)
from elevenlabs.types.single_test_run_request_model import SingleTestRunRequestModel
from elevenlabs.types.tool_response_model import ToolResponseModel
from elevenlabs.types.unit_test_run_response_model import UnitTestRunResponseModel

from verify.agent_run import (
    ApiFailure,
    ConversationalConfig,
    Invocation,
    RemoteCallFailed,
    RemoteId,
    RemoteRef,
    RemoteWorkspace,
    TestBody,
    TestRun,
    ToolCall,
    ToolResult,
)
from verify.artifacts import Json, to_json
from verify.elevenlabs_local import Slug
from verify.elevenlabs_sdk import ToolRequestModel

NO_RETRY: RequestOptions = {"max_retries": 0}
_SK = re.compile(r"sk_[A-Za-z0-9]{8,}")


def open_remote() -> RemoteWorkspace:
    return _SdkRemote(ElevenLabs())


class _SdkRemote:
    def __init__(self, client: ElevenLabs) -> None:
        self._client = client

    def create_tool(self, slug: Slug, tool: ToolRequestModel) -> RemoteId:
        created: ToolResponseModel
        with _translate(f"tools.create {slug}"):
            created = self._client.conversational_ai.tools.create(
                request=tool, request_options=NO_RETRY
            )
        return RemoteId(created.id)

    def create_document(self, name: str, text: str) -> RemoteId:
        created: AddKnowledgeBaseResponseModel
        with _translate("knowledge_base.documents.create_from_text"):
            created = self._client.conversational_ai.knowledge_base.documents.create_from_text(
                text=text, name=name, request_options=NO_RETRY
            )
        return RemoteId(created.id)

    def create_agent(self, name: str, config: ConversationalConfig) -> RemoteId:
        created: CreateAgentResponseModel
        with _translate("agents.create"):
            created = self._client.conversational_ai.agents.create(
                conversation_config=config, name=name, request_options=NO_RETRY
            )
        return RemoteId(created.agent_id)

    def create_test(self, stem: str, body: TestBody) -> RemoteId:
        created: CreateAgentTestResponseModel
        with _translate(f"tests.create {stem}"):
            created = self._client.conversational_ai.tests.create(
                request=body, request_options=NO_RETRY
            )
        return RemoteId(created.id)

    def run_tests(
        self, agent_id: RemoteId, test_ids: Sequence[RemoteId], repeat_count: int
    ) -> Invocation:
        model: GetTestSuiteInvocationResponseModel
        with _translate("agents.run_tests"):
            model = self._client.conversational_ai.agents.run_tests(
                agent_id,
                tests=[
                    SingleTestRunRequestModel(test_id=test_id) for test_id in test_ids
                ],
                repeat_count=repeat_count,
                request_options=NO_RETRY,
            )
        return _invocation(model)

    def invocation(self, invocation_id: str) -> Invocation:
        model: GetTestSuiteInvocationResponseModel
        with _translate("tests.invocations.get"):
            model = self._client.conversational_ai.tests.invocations.get(invocation_id)
        return _invocation(model)

    def delete(self, ref: RemoteRef) -> Literal["deleted", "absent"]:
        with _translate(_delete_call(ref)):
            try:
                match ref.kind:
                    case "tool":
                        self._client.conversational_ai.tools.delete(ref.id, force=True)
                    case "document":
                        self._client.conversational_ai.knowledge_base.documents.delete(
                            ref.id, force=True
                        )
                    case "agent":
                        self._client.conversational_ai.agents.delete(ref.id)
                    case "test":
                        self._client.conversational_ai.tests.delete(ref.id)
            except NotFoundError:
                return "absent"
        return "deleted"


@contextmanager
def _translate(call: str) -> Generator[None]:
    try:
        yield
    except ApiError as error:
        raise RemoteCallFailed(call, _api_failure(error)) from None
    except httpx.HTTPError as error:
        raise RemoteCallFailed(call, _http_failure(error)) from None
    except pydantic.ValidationError as error:
        raise RemoteCallFailed(call, _validation_failure(error)) from None


def scrub(value: Json) -> Json:
    if isinstance(value, str):
        return _SK.sub("<redacted>", value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def _invocation(model: GetTestSuiteInvocationResponseModel) -> Invocation:
    bucketing = model.bucketing_status
    return Invocation(
        model.id,
        model.repeat_count,
        None if bucketing is None else str(bucketing),
        tuple(_run(item) for item in model.test_runs),
        to_json(model.model_dump(mode="json", by_alias=True)),
    )


def _run(model: UnitTestRunResponseModel) -> TestRun:
    condition = model.condition_result
    rationale = None if condition is None else condition.rationale
    responses = model.agent_responses
    if responses is None:
        transcript: Json = []
        calls: tuple[ToolCall, ...] = ()
    else:
        transcript = [
            to_json(turn.model_dump(mode="json", by_alias=True)) for turn in responses
        ]
        calls = _tool_calls(responses)
    return TestRun(
        RemoteId(model.test_id),
        model.test_run_id,
        str(model.status),
        None if condition is None else str(condition.result),
        None if rationale is None else rationale.summary,
        tuple(rationale.messages or ()) if rationale is not None else (),
        calls,
        transcript,
    )


def _tool_calls(
    turns: Sequence[ConversationHistoryTranscriptCommonModelOutput],
) -> tuple[ToolCall, ...]:
    results: dict[str, ToolResult] = {}
    for turn in turns:
        for item in turn.tool_results or []:
            if isinstance(
                item, ConversationHistoryTranscriptOtherToolsResultCommonModel
            ):
                results[item.request_id] = ToolResult(
                    item.result_value, item.is_error, item.raw_error_message
                )
    calls: list[ToolCall] = []
    for turn in turns:
        calls.extend(
            ToolCall(
                item.request_id,
                item.tool_name,
                item.params_as_json,
                item.tool_has_been_called,
                results.get(item.request_id),
            )
            for item in turn.tool_calls or []
        )
    return tuple(calls)


def _delete_call(ref: RemoteRef) -> str:
    match ref.kind:
        case "tool":
            return f"tools.delete {ref.label}"
        case "document":
            return f"knowledge_base.documents.delete {ref.label}"
        case "agent":
            return f"agents.delete {ref.label}"
        case "test":
            return f"tests.delete {ref.label}"


def _api_failure(error: ApiError) -> ApiFailure:
    return ApiFailure(
        type(error).__name__, error.status_code, scrub(to_json(error.body))
    )


def _http_failure(error: httpx.HTTPError) -> ApiFailure:
    response = getattr(error, "response", None)
    if not isinstance(response, httpx.Response):
        return ApiFailure(type(error).__name__, None, scrub(type(error).__name__))
    try:
        body: object = response.json()
    except json.JSONDecodeError, ValueError:
        body = response.text
    return ApiFailure(type(error).__name__, response.status_code, scrub(to_json(body)))


def _validation_failure(error: pydantic.ValidationError) -> ApiFailure:
    return ApiFailure(
        type(error).__name__,
        None,
        scrub(
            to_json(
                error.errors(
                    include_url=False, include_context=False, include_input=False
                )
            )
        ),
    )
