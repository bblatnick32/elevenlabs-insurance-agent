import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NewType, Protocol
from urllib.parse import urlparse

import pydantic

from verify.artifacts import Json, to_json
from verify.elevenlabs_local import (
    COMMIT_TOOL,
    SENTINEL,
    TOOL_CONTRACTS,
    WEBHOOK_BASE,
    LocalAgent,
    LocalTest,
    Slug,
    TestKind,
)
from verify.elevenlabs_sdk import (
    ConversationalConfig,
    TestsCreateRequestBody_Simulation,
    TestsCreateRequestBody_Tool,
    ToolRequestModel,
)

RemoteId = NewType("RemoteId", str)
type TestBody = TestsCreateRequestBody_Simulation | TestsCreateRequestBody_Tool
type RemoteKind = Literal["tool", "document", "agent", "test"]
type RunVerdict = Literal["pass", "fail", "unfinished"]
type SuiteResult = Literal["pass", "fail"]
type MockProof = Literal[
    "mock_match",
    "error_result",
    "webhook_contacted",
    "unmatched",
    "unknown_tool",
    "no_result",
    "not_applicable",
]
type DeletionOutcome = Literal["deleted", "absent", "skipped"] | ApiFailure
type ScenarioVerdict = Literal["pass", "fail", "unfinished", "not_run"]

REPEAT_COUNT = 1
TERMINAL = frozenset({"passed", "failed"})
FAILING_PROOFS = frozenset({"unmatched", "unknown_tool", "webhook_contacted"})


@dataclass(frozen=True, slots=True)
class RemoteRef:
    kind: RemoteKind
    id: RemoteId
    label: str


@dataclass(frozen=True, slots=True)
class ApiFailure:
    error_type: str
    status_code: int | None
    body: Json


@dataclass(frozen=True, slots=True)
class Deletion:
    ref: RemoteRef
    outcome: DeletionOutcome


@dataclass(frozen=True, slots=True)
class ToolResult:
    value: str
    is_error: bool
    raw_error_message: str | None


@dataclass(frozen=True, slots=True)
class ToolCall:
    request_id: str
    tool_name: str
    params_json: str
    called: bool
    result: ToolResult | None


@dataclass(frozen=True, slots=True)
class TestRun:
    test_id: RemoteId
    test_run_id: str
    status: str
    condition: str | None
    rationale_summary: str | None
    rationale_messages: tuple[str, ...]
    tool_calls: tuple[ToolCall, ...]
    transcript: Json


@dataclass(frozen=True, slots=True)
class Invocation:
    id: str
    repeat_count: int | None
    bucketing: str | None
    runs: tuple[TestRun, ...]
    raw: Json


@dataclass(slots=True)
class Ledger:
    tools: dict[Slug, RemoteId] = field(default_factory=dict)
    document: RemoteId | None = None
    agent: RemoteId | None = None
    tests: dict[str, RemoteId] = field(default_factory=dict)
    invocation: Invocation | None = None
    deletions: list[Deletion] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Completed:
    pass


@dataclass(frozen=True, slots=True)
class DeadlineExpired:
    after_seconds: float


@dataclass(frozen=True, slots=True)
class Interrupted:
    pass


@dataclass(frozen=True, slots=True)
class RemoteFailure:
    call: str
    failure: ApiFailure


@dataclass(frozen=True, slots=True)
class Crashed:
    error_type: str


type Halt = Completed | DeadlineExpired | Interrupted | RemoteFailure | Crashed


class RemoteCallFailed(Exception):
    call: str
    failure: ApiFailure

    def __init__(self, call: str, failure: ApiFailure) -> None:
        self.call = call
        self.failure = failure
        super().__init__(f"{call}: {failure.error_type} {failure.status_code}")


class RemoteWorkspace(Protocol):
    def create_tool(self, slug: Slug, tool: ToolRequestModel) -> RemoteId: ...
    def create_document(self, name: str, text: str) -> RemoteId: ...
    def create_agent(self, name: str, config: ConversationalConfig) -> RemoteId: ...
    def create_test(self, stem: str, body: TestBody) -> RemoteId: ...
    def run_tests(
        self, agent_id: RemoteId, test_ids: Sequence[RemoteId], repeat_count: int
    ) -> Invocation: ...
    def invocation(self, invocation_id: str) -> Invocation: ...
    def delete(self, ref: RemoteRef) -> Literal["deleted", "absent"]: ...


def bind_agent_config(
    config: ConversationalConfig,
    tool_ids: Mapping[Slug, RemoteId],
    document_id: RemoteId,
) -> ConversationalConfig:
    section = config.agent
    prompt = None if section is None else section.prompt
    locators = None if prompt is None else prompt.knowledge_base
    if section is None or prompt is None or not locators:
        raise ValueError("conversation_config.agent.prompt.knowledge_base is required")
    locator = locators[0]
    # knowledge_base[0] is LocalAgent.knowledge.locator; in-place writes would alias the loaded agent.
    new_prompt = prompt.model_copy(
        update={
            "tool_ids": [tool_ids[Slug(slug)] for slug in TOOL_CONTRACTS],
            "knowledge_base": [locator.model_copy(update={"id": document_id})],
        }
    )
    return config.model_copy(
        update={"agent": section.model_copy(update={"prompt": new_prompt})}
    )


def bind_test(test: LocalTest, tool_ids: Mapping[Slug, RemoteId]) -> TestBody:
    match test.kind:
        case "simulation":
            return test.payload
        case "tool":
            payload = test.payload
            if not isinstance(payload, TestsCreateRequestBody_Tool):
                raise ValueError(f"{test.stem} payload is not a tool body")
            params = payload.tool_call_parameters
            referenced = None if params is None else params.referenced_tool
            if params is None or referenced is None:
                raise ValueError(f"{test.stem} is missing referenced_tool")
            return payload.model_copy(
                update={
                    "tool_call_parameters": params.model_copy(
                        update={
                            "referenced_tool": referenced.model_copy(
                                update={"id": tool_ids[Slug(COMMIT_TOOL)]}
                            )
                        }
                    )
                }
            )


def unbound_sentinels(body: object) -> tuple[str, ...]:
    if not isinstance(body, pydantic.BaseModel):
        return ()
    return tuple(
        _sentinel_pointers(to_json(body.model_dump(mode="json", by_alias=True)), ())
    )


def deletion_plan(ledger: Ledger) -> tuple[RemoteRef, ...]:
    done = {(item.ref.kind, item.ref.id) for item in ledger.deletions}
    refs: list[RemoteRef] = []
    for stem, test_id in reversed(list(ledger.tests.items())):
        refs.append(RemoteRef("test", test_id, stem))
    if ledger.agent is not None:
        refs.append(RemoteRef("agent", ledger.agent, "agent"))
    if ledger.document is not None:
        refs.append(RemoteRef("document", ledger.document, "document"))
    for slug in reversed(list(TOOL_CONTRACTS)):
        tool_id = ledger.tools.get(Slug(slug))
        if tool_id is not None:
            refs.append(RemoteRef("tool", tool_id, slug))
    return tuple(ref for ref in refs if (ref.kind, ref.id) not in done)


def is_settled(invocation: Invocation, requested_repeat: int) -> bool:
    if not invocation.runs:
        return False
    if any(run.status not in TERMINAL for run in invocation.runs):
        return False
    repeats = (
        invocation.repeat_count
        if invocation.repeat_count is not None
        else requested_repeat
    )
    if repeats > 1:
        return invocation.bucketing in {"completed", "failed"}
    return True


def run_verdict(run: TestRun) -> RunVerdict:
    if run.status == "passed":
        return "pass"
    if run.status == "failed":
        return "fail"
    return "unfinished"


def mock_proof(
    kind: TestKind, call: ToolCall, tools: Mapping[Slug, ToolRequestModel]
) -> MockProof:
    match kind:
        case "tool":
            return "not_applicable"
        case "simulation":
            pass
    slug = Slug(call.tool_name)
    if slug not in tools:
        return "unknown_tool"
    result = call.result
    if result is None:
        return "no_result"
    tool = tools[slug]
    if any(
        _same_payload(result.value, mock.mock_result)
        for mock in (tool.response_mocks or [])
    ):
        return "mock_match"
    host = urlparse(WEBHOOK_BASE).hostname
    text = result.value or result.raw_error_message or ""
    if result.is_error and host is not None and host in text:
        return "webhook_contacted"
    if result.is_error:
        return "error_result"
    return "unmatched"


@dataclass(frozen=True, slots=True)
class ProvenCall:
    call: ToolCall
    proof: MockProof


@dataclass(frozen=True, slots=True)
class RunReport:
    run: TestRun
    calls: tuple[ProvenCall, ...]

    @property
    def verdict(self) -> RunVerdict:
        return run_verdict(self.run)


@dataclass(frozen=True, slots=True)
class ScenarioReport:
    stem: str
    kind: TestKind
    test_id: RemoteId | None
    runs: tuple[RunReport, ...]


@dataclass(frozen=True, slots=True)
class Manifest:
    invocation_id: str | None
    started_at: datetime
    finished_at: datetime
    result: SuiteResult
    stopped: Halt
    git_commit: str | None
    sdk_version: str
    agent_id: RemoteId | None
    repeat_count: int
    scenarios: tuple[ScenarioReport, ...]
    cleanup: tuple[Deletion, ...]


def build_manifest(
    agent: LocalAgent,
    ledger: Ledger,
    halt: Halt,
    *,
    started_at: datetime,
    finished_at: datetime,
    git_commit: str | None,
    sdk_version: str,
    requested_repeat: int,
) -> Manifest:
    by_id = {test_id: stem for stem, test_id in ledger.tests.items()}
    grouped: dict[str, list[TestRun]] = {test.stem: [] for test in agent.tests}
    invocation = ledger.invocation
    if invocation is not None:
        for run in invocation.runs:
            stem = by_id.get(run.test_id)
            if stem is None:
                continue
            grouped[stem].append(run)
    scenarios = tuple(
        _scenario_report(test, ledger.tests.get(test.stem), grouped[test.stem], agent)
        for test in agent.tests
    )
    repeat_count = requested_repeat
    if invocation is not None and invocation.repeat_count is not None:
        repeat_count = invocation.repeat_count
    result = suite_result(halt, scenarios, ledger.deletions, requested_repeat)
    if len(scenarios) != len(agent.tests):
        result = "fail"
    invocation_id = None if invocation is None else invocation.id
    return Manifest(
        invocation_id,
        started_at,
        finished_at,
        result,
        halt,
        git_commit,
        sdk_version,
        ledger.agent,
        repeat_count,
        scenarios,
        tuple(ledger.deletions),
    )


def suite_result(
    halt: Halt,
    scenarios: Sequence[ScenarioReport],
    cleanup: Sequence[Deletion],
    repeat_count: int,
) -> SuiteResult:
    match halt:
        case Completed():
            if not scenarios:
                return "fail"
            for scenario in scenarios:
                if len(scenario.runs) != repeat_count:
                    return "fail"
                for report in scenario.runs:
                    if report.verdict != "pass":
                        return "fail"
                    for proven in report.calls:
                        if proven.proof in FAILING_PROOFS:
                            return "fail"
            for deletion in cleanup:
                if not _clean_deletion(deletion):
                    return "fail"
            return "pass"
        case DeadlineExpired() | Interrupted() | RemoteFailure() | Crashed():
            return "fail"


def scenario_verdict(scenario: ScenarioReport) -> ScenarioVerdict:
    if not scenario.runs:
        return "not_run"
    if any(report.verdict == "fail" for report in scenario.runs):
        return "fail"
    if any(report.verdict == "unfinished" for report in scenario.runs):
        return "unfinished"
    return "pass"


def proof_counts(calls: Sequence[ProvenCall]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for proven in calls:
        counts[proven.proof] = counts.get(proven.proof, 0) + 1
    return counts


def _scenario_report(
    test: LocalTest,
    test_id: RemoteId | None,
    runs: Sequence[TestRun],
    agent: LocalAgent,
) -> ScenarioReport:
    reports = tuple(
        RunReport(
            run,
            tuple(
                ProvenCall(call, mock_proof(test.kind, call, agent.tools))
                for call in run.tool_calls
            ),
        )
        for run in runs
    )
    return ScenarioReport(test.stem, test.kind, test_id, reports)


def _clean_deletion(deletion: Deletion) -> bool:
    match deletion.outcome:
        case "deleted" | "absent":
            return True
        case "skipped":
            return False
        case ApiFailure():
            return False


def _same_payload(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        parsed_left = json.loads(left)
        parsed_right = json.loads(right)
    except json.JSONDecodeError:
        return False
    return parsed_left == parsed_right


def _sentinel_pointers(node: Json, parts: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_sentinel_pointers(value, (*parts, str(key))))
        return found
    if isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_sentinel_pointers(value, (*parts, str(index))))
        return found
    if isinstance(node, str) and SENTINEL.fullmatch(node):
        escaped = (part.replace("~", "~0").replace("/", "~1") for part in parts)
        found.append("/" + "/".join(escaped) if parts else "")
    return found
