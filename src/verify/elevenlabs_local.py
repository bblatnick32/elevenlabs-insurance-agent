import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType, cast

import pydantic

from insurance_agent.http import (
    CommitApiResult,
    LookupApiResult,
    ProposeApiResult,
    ProposeRequest,
)
from verify.elevenlabs_sdk import (
    CONVERSATIONAL_CONFIG,
    SIMULATION_TEST,
    TOOL_REQUEST,
    TOOL_TEST,
    ConversationalConfig,
    KnowledgeBaseLocator,
    ParseFail,
    TestsCreateRequestBody_Simulation,
    TestsCreateRequestBody_Tool,
    ToolRequestModel,
    ToolRequestModelToolConfig_Webhook,
    parse_model,
)

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type TestKind = Literal["simulation", "tool"]

Slug = NewType("Slug", str)

ARTIFACT_ROOT = "elevenlabs"
AGENT_FILE = f"{ARTIFACT_ROOT}/agent.json"
PROMPT_FILE = f"{ARTIFACT_ROOT}/prompt.md"
WEBHOOK_BASE = "https://local.invalid"
AGENT_NAME = "policy-servicing-vehicle-addition"
LLM = "gpt-5.6-sol"
TTS_MODEL = "eleven_v3_conversational"
TIMEZONE, LANGUAGE = "America/New_York", "en"
ERROR_MODE, WEBHOOK = "passthrough", "webhook"
KNOWLEDGE_TYPE, KNOWLEDGE_USAGE = "text", "prompt"
MOCKING_STRATEGY, FALLBACK_STRATEGY = "all", "raise_error"
COMMIT_TOOL = "commit_vehicle_addition"
CHAT_ROLES = frozenset({"agent", "user"})
SENTINEL = re.compile(r"^local:([a-z0-9][a-z0-9_-]*)$")
PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SIMULATE_TOKEN = "simulate_conversation"
PROMPT_TOOLS = ("conversation_config", "agent", "prompt", "tools")
PROMPT_TEXT = ("conversation_config", "agent", "prompt", "prompt")
ENVELOPE_KEYS = frozenset({"name", "conversation_config"})
POLICY_KEYS = frozenset({"mocked_tool_ids", "tool_mock_overrides"})

TEST_MANIFEST: Mapping[str, TestKind] = {
    "01-happy-path": "simulation",
    "02-date-correction": "simulation",
    "03-declines-confirmation": "tool",
    "04-cancelled-policy": "simulation",
    "05-backdated-date": "simulation",
    "06-unknown-policy": "simulation",
    "07-garbled-vin": "simulation",
    "08-pressure-to-skip": "tool",
}
SIMULATION_TEST_COUNT = sum(kind == "simulation" for kind in TEST_MANIFEST.values())
TOOL_CALL_TEST_COUNT = sum(kind == "tool" for kind in TEST_MANIFEST.values())


@dataclass(frozen=True, slots=True)
class ToolContract:
    method: Literal["GET", "POST"]
    path: str
    body_fields: frozenset[str]
    result: pydantic.TypeAdapter[object]


TOOL_CONTRACTS: Mapping[str, ToolContract] = {
    "lookup_policy": ToolContract(
        "GET",
        "/v1/policies/{policy_number}",
        frozenset(),
        pydantic.TypeAdapter(LookupApiResult),
    ),
    "propose_vehicle_addition": ToolContract(
        "POST",
        "/v1/policies/{policy_number}/vehicle-addition-proposals",
        frozenset(ProposeRequest.model_fields),
        pydantic.TypeAdapter(ProposeApiResult),
    ),
    "commit_vehicle_addition": ToolContract(
        "POST",
        "/v1/vehicle-addition-proposals/{proposal_id}/commit",
        frozenset(),
        pydantic.TypeAdapter(CommitApiResult),
    ),
}
EXPECTED_TOOL_IDS = tuple(f"local:{slug}" for slug in TOOL_CONTRACTS)


@dataclass(frozen=True, slots=True)
class ArtifactFailure:
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class ArtifactFailures:
    failures: tuple[ArtifactFailure, ...]


type Fails = list[ArtifactFailure]


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    locator: KnowledgeBaseLocator
    text: str


@dataclass(frozen=True, slots=True)
class LocalTest:
    stem: str
    kind: TestKind
    payload: TestsCreateRequestBody_Simulation | TestsCreateRequestBody_Tool


@dataclass(frozen=True, slots=True)
class LocalAgent:
    name: str
    conversation_config: ConversationalConfig
    tools: Mapping[Slug, ToolRequestModel]
    knowledge: KnowledgeSource
    tests: tuple[LocalTest, ...]
    files: frozenset[str]
    summary: str


@dataclass(frozen=True, slots=True)
class _RawTree:
    agent: dict[str, Json]
    prompt: str
    tools: Mapping[Slug, dict[str, Json]]
    knowledge: Mapping[Slug, str]
    tests: Mapping[str, dict[str, Json]]
    files: frozenset[str]


def load_local_agent(root: Path) -> LocalAgent | ArtifactFailures:
    tree = _read_tree(root)
    if isinstance(tree, ArtifactFailures):
        return tree
    denied = tuple(_denials(tree))
    if denied:
        return ArtifactFailures(denied)
    loaded = _payloads(tree)
    if isinstance(loaded, ArtifactFailures):
        return loaded
    invariants = tuple(_invariants(tree, loaded))
    return ArtifactFailures(invariants) if invariants else loaded


def _read_tree(root: Path) -> _RawTree | ArtifactFailures:
    failures: list[ArtifactFailure] = []
    base = root / ARTIFACT_ROOT
    if not base.is_dir():
        detail = "missing directory" if not base.exists() else "not a directory"
        return ArtifactFailures((_fail(ARTIFACT_ROOT, detail),))
    classified: dict[str, tuple[str, str]] = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            failures.append(_fail(relative, "symlink"))
            continue
        if not path.is_file():
            continue
        kind_stem = _classify(relative)
        if kind_stem is None:
            failures.append(_fail(relative, "unknown artifact location"))
            continue
        classified[relative] = kind_stem
    agent_raw = _require_object(root, AGENT_FILE, failures)
    prompt = _require_text(root, PROMPT_FILE, failures)
    tools: dict[Slug, dict[str, Json]] = {}
    knowledge: dict[Slug, str] = {}
    tests: dict[str, dict[str, Json]] = {}
    files = {
        path
        for path, present in ((AGENT_FILE, agent_raw), (PROMPT_FILE, prompt))
        if present is not None
    }
    for relative, (kind, stem) in classified.items():
        if kind == "knowledge":
            text = _require_text(root, relative, failures)
            if text is not None:
                knowledge[Slug(stem)] = text
                files.add(relative)
            continue
        if kind not in {"tool", "test"}:
            continue
        payload = _require_object(root, relative, failures)
        if payload is None:
            continue
        files.add(relative)
        if kind == "tool":
            tools[Slug(stem)] = payload
        else:
            tests[stem] = payload
    failures.extend(
        _fail(path, "missing")
        for path in (AGENT_FILE, PROMPT_FILE)
        if path not in classified
    )
    if failures or agent_raw is None or prompt is None:
        return ArtifactFailures(tuple(failures))
    return _RawTree(agent_raw, prompt, tools, knowledge, tests, frozenset(files))


def _classify(relative: str) -> tuple[str, str] | None:
    parts = relative.split("/")
    if parts == [ARTIFACT_ROOT, "agent.json"]:
        return "agent", "agent"
    if parts == [ARTIFACT_ROOT, "prompt.md"]:
        return "prompt", "prompt"
    if len(parts) != 3 or parts[0] != ARTIFACT_ROOT:
        return None
    folder, name = parts[1], parts[2]
    if folder == "knowledge" and name.endswith(".md"):
        return "knowledge", Path(name).stem
    if folder == "tools" and name.endswith(".json"):
        return "tool", Path(name).stem
    if folder == "tests" and name.endswith(".json"):
        return "test", Path(name).stem
    return None


def _require_text(root: Path, relative: str, failures: Fails) -> str | None:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except OSError:
        failures.append(_fail(relative, "unreadable file"))
        return None


def _require_object(
    root: Path, relative: str, failures: Fails
) -> dict[str, Json] | None:
    text = _require_text(root, relative, failures)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        failures.append(_fail(relative, f"invalid JSON: {error.msg}"))
        return None
    if not isinstance(payload, dict):
        failures.append(_fail(relative, "expected a JSON object"))
        return None
    return cast(dict[str, Json], payload)


def _denials(tree: _RawTree) -> Iterator[ArtifactFailure]:
    if _has_pointer(tree.agent, PROMPT_TOOLS):
        yield _fail(
            AGENT_FILE,
            f"{_json_pointer(PROMPT_TOOLS)}: inline prompt.tools is deprecated; "
            "reference standalone tools via tool_ids",
        )
    if _has_pointer(tree.agent, PROMPT_TEXT):
        yield _fail(
            AGENT_FILE,
            f"{_json_pointer(PROMPT_TEXT)}: owned by {PROMPT_FILE}; remove it here",
        )
    yield from _simulate_hits(AGENT_FILE, tree.agent)
    yield from _simulate_hits(PROMPT_FILE, tree.prompt)
    for slug, payload in tree.tools.items():
        yield from _simulate_hits(_art("tools", f"{slug}.json"), payload)
    for slug, text in tree.knowledge.items():
        yield from _simulate_hits(_art("knowledge", f"{slug}.md"), text)
    for stem, payload in tree.tests.items():
        relative = _art("tests", f"{stem}.json")
        for pointer, kind, text in _walk(payload):
            if kind == "key" and text == "success_condition":
                yield _fail(
                    relative,
                    f"{pointer}: singular success_condition is deprecated; "
                    "use success_conditions",
                )
            if kind == "key" and text in POLICY_KEYS:
                yield _fail(
                    relative, f"{pointer}: {text} is disallowed; tool files own mocks"
                )
        yield from _simulate_hits(relative, payload)


def _simulate_hits(relative: str, node: Json | str) -> Iterator[ArtifactFailure]:
    if isinstance(node, str):
        if SIMULATE_TOKEN in node:
            yield _fail(relative, f"contains {SIMULATE_TOKEN}")
        return
    for pointer, _kind, text in _walk(node):
        if SIMULATE_TOKEN in text:
            yield _fail(relative, f"{pointer}: contains {SIMULATE_TOKEN}")


def _payloads(tree: _RawTree) -> LocalAgent | ArtifactFailures:
    failures: list[ArtifactFailure] = []
    extra_keys = set(tree.agent) - ENVELOPE_KEYS
    missing_keys = ENVELOPE_KEYS - set(tree.agent)
    if extra_keys:
        extra = sorted(extra_keys)[0]
        message = f"envelope allows only name and conversation_config; extra {extra}"
        failures.append(_fail(AGENT_FILE, message))
    if missing_keys:
        missing = sorted(missing_keys)[0]
        failures.append(_fail(AGENT_FILE, f"envelope missing {missing}"))
    name = tree.agent.get("name")
    if not isinstance(name, str):
        failures.append(_fail(AGENT_FILE, "/name: must be a string"))
        name = ""
    config_raw = tree.agent.get("conversation_config")
    if not isinstance(config_raw, dict):
        failures.append(
            _fail(AGENT_FILE, "/conversation_config: expected a JSON object")
        )
        return ArtifactFailures(tuple(failures))
    section_raw = config_raw.get("agent")
    if not isinstance(section_raw, dict):
        return ArtifactFailures(
            (*failures, _fail(AGENT_FILE, "/conversation_config/agent: missing"))
        )
    block = section_raw.get("prompt")
    if not isinstance(block, dict):
        return ArtifactFailures(
            (*failures, _fail(AGENT_FILE, "/conversation_config/agent/prompt: missing"))
        )
    if not tree.prompt.strip():
        return ArtifactFailures((*failures, _fail(PROMPT_FILE, "prompt text is empty")))
    inlined = {
        **config_raw,
        "agent": {**section_raw, "prompt": {**block, "prompt": tree.prompt}},
    }
    config = _take(
        failures, AGENT_FILE, CONVERSATIONAL_CONFIG, inlined, ("conversation_config",)
    )
    tools: dict[Slug, ToolRequestModel] = {}
    for slug, raw in tree.tools.items():
        relative = _art("tools", f"{slug}.json")
        config_obj = raw.get("tool_config")
        if not isinstance(config_obj, dict) or config_obj.get("type") != WEBHOOK:
            failures.append(_fail(relative, "/tool_config/type: must be webhook"))
            continue
        tool = _take(failures, relative, TOOL_REQUEST, raw)
        if tool is not None:
            tools[slug] = tool
    tests = _load_tests(tree.tests, failures)
    if failures or config is None:
        return ArtifactFailures(tuple(failures))
    section = config.agent
    prompt = None if section is None else section.prompt
    if section is None or prompt is None:
        return _halt(AGENT_FILE, "/conversation_config/agent/prompt: missing")
    locators = prompt.knowledge_base or []
    if len(locators) != 1:
        return _halt(
            AGENT_FILE,
            "/conversation_config/agent/prompt/knowledge_base: exactly one locator",
        )
    locator = locators[0]
    knowledge_slug = Slug(locator.name)
    if knowledge_slug not in tree.knowledge:
        return _halt(
            AGENT_FILE,
            "/conversation_config/agent/prompt/knowledge_base/0/name: "
            f"no {_art('knowledge', f'{locator.name}.md')}",
        )
    return LocalAgent(
        name,
        config,
        tools,
        KnowledgeSource(locator, tree.knowledge[knowledge_slug]),
        tuple(sorted(tests, key=lambda item: item.stem)),
        tree.files,
        f"1 agent, {len(TOOL_CONTRACTS)} tools, 1 knowledge source, "
        f"{SIMULATION_TEST_COUNT} Simulation tests, {TOOL_CALL_TEST_COUNT} Tool Call tests",
    )


def _load_tests(
    raw_tests: Mapping[str, dict[str, Json]], failures: Fails
) -> list[LocalTest]:
    discovered = set(raw_tests)
    expected = set(TEST_MANIFEST)
    failures.extend(
        _fail(_art("tests", f"{stem}.json"), "missing")
        for stem in sorted(expected - discovered)
    )
    failures.extend(
        _fail(_art("tests", f"{stem}.json"), "unknown artifact location")
        for stem in sorted(discovered - expected)
    )
    tests: list[LocalTest] = []
    for stem, raw in sorted(raw_tests.items()):
        if stem not in TEST_MANIFEST:
            continue
        relative = _art("tests", f"{stem}.json")
        declared = raw.get("type")
        wanted = TEST_MANIFEST[stem]
        if declared != "simulation" and declared != "tool":
            failures.append(_fail(relative, "/type: must be simulation or tool"))
            continue
        if declared != wanted:
            failures.append(_fail(relative, f"/type: must be {wanted}"))
            continue
        if declared == "simulation":
            payload = _take(failures, relative, SIMULATION_TEST, raw)
            kind: TestKind = "simulation"
        else:
            payload = _take(failures, relative, TOOL_TEST, raw)
            kind = "tool"
        if payload is not None:
            tests.append(LocalTest(stem, kind, payload))
    return tests


def _take[T](
    failures: Fails,
    relative: str,
    adapter: pydantic.TypeAdapter[T],
    data: object,
    prefix: tuple[str, ...] = (),
) -> T | None:
    parsed = parse_model(adapter, data, prefix)
    if isinstance(parsed, ParseFail):
        failures.extend(
            _fail(relative, f"{_json_pointer(loc)}: {message}")
            for loc, message in parsed.issues
        )
        return None
    return parsed


def _invariants(tree: _RawTree, agent: LocalAgent) -> Iterator[ArtifactFailure]:
    section = agent.conversation_config.agent
    prompt = None if section is None else section.prompt
    if section is None or prompt is None:
        yield _fail(AGENT_FILE, "/conversation_config/agent/prompt: missing")
        return
    tts = agent.conversation_config.tts
    locator = agent.knowledge.locator
    p = "/conversation_config/agent/prompt"
    pins = (
        ("/name", agent.name, AGENT_NAME),
        ("/conversation_config/agent/language", section.language, LANGUAGE),
        (f"{p}/llm", prompt.llm, LLM),
        (f"{p}/temperature", prompt.temperature, 0),
        (f"{p}/timezone", prompt.timezone, TIMEZONE),
        (
            "/conversation_config/tts/model_id",
            None if tts is None else tts.model_id,
            TTS_MODEL,
        ),
        (f"{p}/knowledge_base/0/type", locator.type, KNOWLEDGE_TYPE),
        (f"{p}/knowledge_base/0/usage_mode", locator.usage_mode, KNOWLEDGE_USAGE),
    )
    for pointer, actual, expected in pins:
        yield from _must(AGENT_FILE, pointer, actual, expected)
    if not section.first_message:
        yield _fail(
            AGENT_FILE, "/conversation_config/agent/first_message: must be nonempty"
        )
    if locator.id != f"local:{locator.name}":
        yield _fail(AGENT_FILE, f"{p}/knowledge_base/0/id: must be local:<name>")
    if not agent.knowledge.text.strip():
        yield _fail(
            _art("knowledge", f"{locator.name}.md"), "knowledge source is empty"
        )
    for slug in sorted(set(tree.knowledge) - {Slug(locator.name)}):
        yield _fail(_art("knowledge", f"{slug}.md"), "orphan knowledge source")
    ids = tuple(prompt.tool_ids or ())
    tools = set(agent.tools)
    for index, value in enumerate(ids):
        pointer = f"{p}/tool_ids/{index}"
        slug = _slug(value)
        if slug is None:
            yield _fail(AGENT_FILE, f"{pointer}: {value!r} is not a local: sentinel")
        elif slug not in tools:
            yield _fail(AGENT_FILE, f"{pointer}: no {_art('tools', f'{slug}.json')}")
    if ids != EXPECTED_TOOL_IDS:
        yield _fail(AGENT_FILE, f"{p}/tool_ids: must be the three local tools")
    yield from _tool_failures(agent)
    yield from _test_failures(agent)


def _tool_failures(agent: LocalAgent) -> Iterator[ArtifactFailure]:
    for slug in TOOL_CONTRACTS:
        if Slug(slug) not in agent.tools:
            yield _fail(_art("tools", f"{slug}.json"), "missing")
    for slug, tool in agent.tools.items():
        relative = _art("tools", f"{slug}.json")
        if slug not in TOOL_CONTRACTS:
            yield _fail(relative, "orphan tool file")
            continue
        config = tool.tool_config
        if not isinstance(config, ToolRequestModelToolConfig_Webhook):
            yield _fail(relative, "/tool_config/type: must be webhook")
            continue
        contract = TOOL_CONTRACTS[slug]
        schema = config.api_schema
        pins = (
            ("/tool_config/name", config.name, slug),
            (
                "/tool_config/tool_error_handling_mode",
                config.tool_error_handling_mode,
                ERROR_MODE,
            ),
            ("/tool_config/api_schema/method", schema.method, contract.method),
        )
        for pointer, actual, expected in pins:
            yield from _must(relative, pointer, actual, expected)
        if config.api_schema.url != f"{WEBHOOK_BASE}{contract.path}":
            yield _fail(
                relative, "/tool_config/api_schema/url: must use the local.invalid host"
            )
        declared = set(config.api_schema.path_params_schema or {})
        if declared != set(PATH_PARAM.findall(contract.path)):
            yield _fail(
                relative,
                "/tool_config/api_schema/path_params_schema: must match path parameters",
            )
        body = config.api_schema.request_body_schema
        body_ptr = "/tool_config/api_schema/request_body_schema"
        if not contract.body_fields:
            if body is not None:
                yield _fail(relative, f"{body_ptr}: commit and lookup have no body")
        elif body is None:
            yield _fail(relative, f"{body_ptr}: missing")
        else:
            fields = set(contract.body_fields)
            required = set(body.required or [])
            properties = set(body.properties or {})
            if required != fields or properties != fields:
                yield _fail(relative, f"{body_ptr}: must be vin and effective_date")
        mocks = tool.response_mocks or []
        if not mocks:
            yield _fail(relative, "/response_mocks: must be nonempty")
            continue
        if mocks[-1].parameter_conditions:
            yield _fail(
                relative, "/response_mocks: last mock must be an unconditional fallback"
            )
        for index, mock in enumerate(mocks):
            problem = _mock_problem(mock.mock_result, contract)
            if problem is not None:
                yield _fail(relative, f"/response_mocks/{index}/mock_result: {problem}")


def _mock_problem(mock_result: str, contract: ToolContract) -> str | None:
    try:
        parsed = json.loads(mock_result)
    except json.JSONDecodeError:
        return "must be JSON"
    if not isinstance(parsed, dict):
        return "must be a JSON object"
    try:
        contract.result.validate_python(parsed)
    except pydantic.ValidationError:
        return "must match the route-specific API result"
    return None


def _test_failures(agent: LocalAgent) -> Iterator[ArtifactFailure]:
    for test in agent.tests:
        relative = _art("tests", f"{test.stem}.json")
        payload = test.payload
        if test.kind == "simulation":
            if not isinstance(payload, TestsCreateRequestBody_Simulation):
                continue
            if not payload.simulation_scenario:
                yield _fail(relative, "/simulation_scenario: must be nonempty")
            if not payload.success_conditions:
                yield _fail(relative, "/success_conditions: must be nonempty")
            if (
                payload.simulation_max_turns is None
                or payload.simulation_max_turns <= 0
            ):
                yield _fail(
                    relative, "/simulation_max_turns: must be a positive integer"
                )
            mock = payload.tool_mock_config
            if (
                mock is None
                or mock.mocking_strategy != MOCKING_STRATEGY
                or mock.fallback_strategy != FALLBACK_STRATEGY
            ):
                yield _fail(
                    relative,
                    "/tool_mock_config: must be mocking_strategy all and "
                    "fallback_strategy raise_error",
                )
            eval_m, user_m = payload.evaluation_model, payload.simulated_user_model
            yield from _must(relative, "/evaluation_model", eval_m, LLM)
            yield from _must(relative, "/simulated_user_model", user_m, LLM)
            continue
        if not isinstance(payload, TestsCreateRequestBody_Tool):
            continue
        history = payload.chat_history or []
        if not history:
            yield _fail(relative, "/chat_history: must be nonempty")
            continue
        previous = -1
        for index, turn in enumerate(history):
            if turn.role not in CHAT_ROLES:
                yield _fail(
                    relative, f"/chat_history/{index}/role: must be agent or user"
                )
            if not turn.message:
                yield _fail(
                    relative, f"/chat_history/{index}/message: must be nonempty"
                )
            if turn.time_in_call_secs <= previous:
                yield _fail(
                    relative, f"/chat_history/{index}/time_in_call_secs: must increase"
                )
            previous = turn.time_in_call_secs
        parameters = payload.tool_call_parameters
        if parameters is None or parameters.verify_absence is not True:
            yield _fail(relative, "/tool_call_parameters/verify_absence: must be true")
            continue
        referenced = parameters.referenced_tool
        if referenced is None:
            yield _fail(relative, "/tool_call_parameters/referenced_tool: missing")
            continue
        tool_type = "/tool_call_parameters/referenced_tool/type"
        yield from _must(relative, tool_type, referenced.type, WEBHOOK)
        if referenced.id != f"local:{COMMIT_TOOL}":
            yield _fail(
                relative,
                f"/tool_call_parameters/referenced_tool/id: must be local:{COMMIT_TOOL}",
            )


def _must(
    relative: str, pointer: str, actual: object, expected: object
) -> Iterator[ArtifactFailure]:
    if actual != expected:
        yield _fail(relative, f"{pointer}: must be {expected}")


def _fail(path: str, detail: str) -> ArtifactFailure:
    return ArtifactFailure(path, detail)


def _halt(path: str, detail: str) -> ArtifactFailures:
    return ArtifactFailures((_fail(path, detail),))


def _has_pointer(node: Json, pointer: tuple[str, ...]) -> bool:
    current = node
    for key in pointer:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return True


def _walk(node: Json, parts: tuple[str, ...] = ()) -> Iterator[tuple[str, str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            here = (*parts, key)
            yield _json_pointer(here), "key", key
            yield from _walk(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, (*parts, str(index)))
    elif isinstance(node, str):
        yield _json_pointer(parts), "value", node


def _json_pointer(parts: Sequence[str]) -> str:
    escaped = (part.replace("~", "~0").replace("/", "~1") for part in parts)
    return "/" + "/".join(escaped) if parts else ""


def _slug(value: str) -> Slug | None:
    matched = SENTINEL.fullmatch(value)
    return None if matched is None else Slug(matched.group(1))


def _art(*parts: str) -> str:
    return "/".join((ARTIFACT_ROOT, *parts))
