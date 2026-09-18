from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import cast

import pydantic
from elevenlabs.conversational_ai.tests.types import (
    TestsCreateRequestBody_Simulation,
    TestsCreateRequestBody_Tool,
)
from elevenlabs.types import (
    ConversationalConfig,
    KnowledgeBaseLocator,
    ToolRequestModel,
    ToolRequestModelToolConfig_Webhook,
)

type SdkIssue = tuple[tuple[str, ...], str]


@dataclass(frozen=True, slots=True)
class ParseFail:
    issues: tuple[SdkIssue, ...]


CONVERSATIONAL_CONFIG: pydantic.TypeAdapter[ConversationalConfig] = (
    pydantic.TypeAdapter(ConversationalConfig)
)
TOOL_REQUEST: pydantic.TypeAdapter[ToolRequestModel] = pydantic.TypeAdapter(
    ToolRequestModel
)
SIMULATION_TEST: pydantic.TypeAdapter[TestsCreateRequestBody_Simulation] = (
    pydantic.TypeAdapter(TestsCreateRequestBody_Simulation)
)
TOOL_TEST: pydantic.TypeAdapter[TestsCreateRequestBody_Tool] = pydantic.TypeAdapter(
    TestsCreateRequestBody_Tool
)


def parse_model[T](
    adapter: pydantic.TypeAdapter[T],
    data: object,
    prefix: tuple[str, ...] = (),
) -> T | ParseFail:
    try:
        model = adapter.validate_python(data)
    except pydantic.ValidationError as error:
        issues: list[SdkIssue] = []
        for item in error.errors():
            loc = (*prefix, *(str(part) for part in item["loc"]))
            issues.append((loc, str(item["msg"])))
        return ParseFail(tuple(issues))
    extras = tuple(_model_extras(model, prefix))
    if extras:
        return ParseFail(extras)
    return model


def _model_extras(node: object, parts: tuple[str, ...]) -> Iterator[SdkIssue]:
    # Generated SDK models set extra=allow, so extras survive validate_python.
    if isinstance(node, pydantic.BaseModel):
        extra = node.model_extra
        if extra:
            for key in extra:
                yield (*parts, str(key)), "unknown field"
        for name in type(node).model_fields:
            yield from _model_extras(getattr(node, name), (*parts, name))
        return
    if isinstance(node, dict):
        mapping = cast(dict[object, object], node)
        for key, value in mapping.items():
            yield from _model_extras(value, (*parts, str(key)))
        return
    if isinstance(node, list | tuple):
        sequence = cast(Sequence[object], node)
        for index, value in enumerate(sequence):
            yield from _model_extras(value, (*parts, str(index)))


__all__ = [
    "CONVERSATIONAL_CONFIG",
    "SIMULATION_TEST",
    "TOOL_REQUEST",
    "TOOL_TEST",
    "ConversationalConfig",
    "KnowledgeBaseLocator",
    "ParseFail",
    "TestsCreateRequestBody_Simulation",
    "TestsCreateRequestBody_Tool",
    "ToolRequestModel",
    "ToolRequestModelToolConfig_Webhook",
    "parse_model",
]
