import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from verify.agent_run import (
    REPEAT_COUNT,
    TERMINAL,
    ApiFailure,
    Completed,
    Crashed,
    DeadlineExpired,
    Deletion,
    DeletionOutcome,
    Halt,
    Interrupted,
    Invocation,
    Ledger,
    Manifest,
    ProvenCall,
    RemoteCallFailed,
    RemoteFailure,
    RemoteWorkspace,
    RunReport,
    ScenarioReport,
    SuiteResult,
    bind_agent_config,
    bind_test,
    build_manifest,
    deletion_plan,
    is_settled,
    proof_counts,
    scenario_verdict,
    unbound_sentinels,
)
from verify.artifacts import Json, git_commit, repo_root, to_json, utc_stamp, write_json
from verify.elevenlabs_local import (
    TOOL_CONTRACTS,
    WEBHOOK_BASE,
    ArtifactFailures,
    LocalAgent,
    Slug,
    load_local_agent,
)

KEY_NAME = "ELEVENLABS_API_KEY"
DEADLINE_SECONDS = 1200.0
INTERVAL_SECONDS = 5.0
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PROOF_ORDER = (
    "mock_match",
    "error_result",
    "webhook_contacted",
    "unmatched",
    "unknown_tool",
    "no_result",
    "not_applicable",
)


@dataclass(frozen=True, slots=True)
class Pacing:
    deadline_seconds: float = DEADLINE_SECONDS
    interval_seconds: float = INTERVAL_SECONDS
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


DEFAULT_PACE = Pacing()


def run_agent_suite(
    *,
    env_names: frozenset[str],
    connect: Callable[[], RemoteWorkspace],
    root: Path | None = None,
    repeat_count: int = REPEAT_COUNT,
    pace: Pacing = DEFAULT_PACE,
) -> int:
    if KEY_NAME not in env_names:
        print(f"{KEY_NAME} is not set in the environment", file=sys.stderr)
        return 1
    resolved = repo_root() if root is None else root
    loaded = load_local_agent(resolved)
    match loaded:
        case ArtifactFailures(failures=failures):
            for failure in failures:
                print(f"{failure.path}: {failure.detail}", file=sys.stderr)
            return 1
        case LocalAgent() as agent:
            pass
    stamp = utc_stamp()
    started_at = datetime.now(UTC)
    base = resolved / "artifacts" / "agent-suite"
    ledger = Ledger()
    remote = connect()
    halt: Halt = Crashed("unknown")
    try:
        halt = _perform(agent, remote, ledger, repeat_count, pace, base, stamp)
    except RemoteCallFailed as error:
        halt = RemoteFailure(error.call, error.failure)
    except KeyboardInterrupt:
        halt = Interrupted()
    except Exception as error:
        halt = Crashed(type(error).__name__)
        raise
    finally:
        result = _finalize(
            resolved, agent, remote, ledger, halt, started_at, repeat_count, base, stamp
        )
    return 0 if result == "pass" else 1


def evidence_dir(base: Path, invocation_id: str | None, stamp: str) -> Path:
    if invocation_id is not None and SAFE_NAME.fullmatch(invocation_id):
        return base / invocation_id
    return base / f"{stamp}-no-invocation"


def write_evidence(
    directory: Path, manifest: Manifest, invocation: Invocation | None
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "manifest.json", manifest_json(manifest))
    if invocation is not None:
        write_json(directory / "raw-invocation.json", invocation.raw)
    for scenario in manifest.scenarios:
        for report in scenario.runs:
            run_dir = directory / scenario.stem / report.run.test_run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(run_dir / "transcript.json", report.run.transcript)
            write_json(run_dir / "tool-calls.json", _tool_calls_json(report.calls))
            write_json(run_dir / "evals.json", _evals_json(report))


def manifest_json(manifest: Manifest) -> Json:
    test_ids = {
        scenario.stem: str(scenario.test_id)
        for scenario in manifest.scenarios
        if scenario.test_id is not None
    }
    verdicts = {
        scenario.stem: scenario_verdict(scenario) for scenario in manifest.scenarios
    }
    return to_json(
        {
            "invocation_id": manifest.invocation_id,
            "started_at": manifest.started_at.isoformat(),
            "finished_at": manifest.finished_at.isoformat(),
            "result": manifest.result,
            "stopped": _halt_json(manifest.stopped),
            "git_commit": manifest.git_commit,
            "sdk_version": manifest.sdk_version,
            "agent_id": None if manifest.agent_id is None else str(manifest.agent_id),
            "repeat_count": manifest.repeat_count,
            "test_ids": test_ids,
            "verdicts": verdicts,
            "scenarios": [_scenario_json(scenario) for scenario in manifest.scenarios],
            "cleanup": [_deletion_json(item) for item in manifest.cleanup],
            "webhook_base": WEBHOOK_BASE,
        }
    )


def _perform(
    agent: LocalAgent,
    remote: RemoteWorkspace,
    ledger: Ledger,
    repeat_count: int,
    pace: Pacing,
    base: Path,
    stamp: str,
) -> Completed | DeadlineExpired:
    for slug in TOOL_CONTRACTS:
        tool = agent.tools[Slug(slug)]
        _require_bound(tool)
        ledger.tools[Slug(slug)] = remote.create_tool(Slug(slug), tool)
    document_id = remote.create_document(
        agent.knowledge.locator.name, agent.knowledge.text
    )
    ledger.document = document_id
    config = bind_agent_config(agent.conversation_config, ledger.tools, document_id)
    _require_bound(config)
    agent_id = remote.create_agent(agent.name, config)
    ledger.agent = agent_id
    for test in agent.tests:
        body = bind_test(test, ledger.tools)
        _require_bound(body)
        ledger.tests[test.stem] = remote.create_test(test.stem, body)
    test_ids = [ledger.tests[test.stem] for test in agent.tests]
    invocation = remote.run_tests(agent_id, test_ids, repeat_count)
    ledger.invocation = invocation
    print(f"invocation {invocation.id} repeat_count {repeat_count}")
    if SAFE_NAME.fullmatch(invocation.id) is None:
        directory = evidence_dir(base, invocation.id, stamp)
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "raw-invocation.json", invocation.raw)
        raise RemoteCallFailed(
            "agents.run_tests", ApiFailure("InvalidInvocationId", None, invocation.id)
        )
    directory = evidence_dir(base, invocation.id, stamp)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "raw-invocation.json", invocation.raw)
    return _poll(remote, ledger, repeat_count, pace, directory)


def _poll(
    remote: RemoteWorkspace,
    ledger: Ledger,
    repeat_count: int,
    pace: Pacing,
    directory: Path,
) -> Completed | DeadlineExpired:
    start = pace.now()
    last_k: int | None = None
    while True:
        current = ledger.invocation
        if current is not None and is_settled(current, repeat_count):
            return Completed()
        elapsed = pace.now() - start
        if elapsed >= pace.deadline_seconds:
            return DeadlineExpired(elapsed)
        pace.sleep(pace.interval_seconds)
        if current is None:
            continue
        ledger.invocation = remote.invocation(current.id)
        refreshed = ledger.invocation
        k = sum(1 for run in refreshed.runs if run.status in TERMINAL)
        if k == last_k:
            continue
        waited = pace.now() - start
        print(f"waiting {k}/{len(refreshed.runs)} runs terminal ({waited:.0f}s)")
        write_json(directory / "raw-invocation.json", refreshed.raw)
        last_k = k


def _cleanup(remote: RemoteWorkspace, ledger: Ledger) -> None:
    remaining = deletion_plan(ledger)
    for index, ref in enumerate(remaining):
        try:
            outcome: DeletionOutcome = remote.delete(ref)
        except RemoteCallFailed as error:
            outcome = error.failure
        except KeyboardInterrupt:
            ledger.deletions.append(Deletion(ref, "skipped"))
            for later in remaining[index + 1 :]:
                ledger.deletions.append(Deletion(later, "skipped"))
            return
        ledger.deletions.append(Deletion(ref, outcome))


def _finalize(
    root: Path,
    agent: LocalAgent,
    remote: RemoteWorkspace,
    ledger: Ledger,
    halt: Halt,
    started_at: datetime,
    repeat_count: int,
    base: Path,
    stamp: str,
) -> SuiteResult:
    _cleanup(remote, ledger)
    finished_at = datetime.now(UTC)
    manifest = build_manifest(
        agent,
        ledger,
        halt,
        started_at=started_at,
        finished_at=finished_at,
        git_commit=git_commit(root),
        sdk_version=version("elevenlabs"),
        requested_repeat=repeat_count,
    )
    invocation = ledger.invocation
    invocation_id = None if invocation is None else invocation.id
    directory = evidence_dir(base, invocation_id, stamp)
    write_evidence(directory, manifest, invocation)
    _report(manifest, directory)
    return manifest.result


def _report(manifest: Manifest, run_dir: Path) -> None:
    for scenario in manifest.scenarios:
        for report in scenario.runs:
            condition = report.run.condition or "-"
            print(
                f"{report.verdict:<10} {scenario.stem:<24} "
                f"{report.run.test_run_id} {condition}"
            )
    match manifest.stopped:
        case Completed():
            pass
        case DeadlineExpired(after_seconds=after):
            unfinished = sum(
                1
                for scenario in manifest.scenarios
                for report in scenario.runs
                if report.verdict == "unfinished"
            )
            print(f"deadline after {after:.0f}s: {unfinished} run unfinished")
        case Interrupted():
            print("interrupted")
        case RemoteFailure(call=call, failure=failure):
            print(f"fail {call}: {failure.error_type} {failure.status_code}")
        case Crashed(error_type=error_type):
            print(f"crashed {error_type}")
    totals = dict.fromkeys(PROOF_ORDER, 0)
    for scenario in manifest.scenarios:
        for report in scenario.runs:
            for proven in report.calls:
                totals[proven.proof] = totals.get(proven.proof, 0) + 1
    rendered = ", ".join(f"{totals[label]} {label}" for label in PROOF_ORDER)
    print(f"tool results: {rendered}")
    deleted = absent = failed = skipped = 0
    for item in manifest.cleanup:
        match item.outcome:
            case "deleted":
                deleted += 1
            case "absent":
                absent += 1
            case "skipped":
                skipped += 1
            case ApiFailure():
                failed += 1
    print(
        f"cleanup: {deleted} deleted, {absent} absent, {failed} failed, {skipped} skipped"
    )
    print(f"{manifest.result} {run_dir}")


def _require_bound(body: object) -> None:
    leftovers = unbound_sentinels(body)
    if leftovers:
        raise ValueError(f"unbound sentinels: {', '.join(leftovers)}")


def _halt_json(halt: Halt) -> Json:
    match halt:
        case Completed():
            return {"kind": "completed"}
        case DeadlineExpired(after_seconds=after):
            return {"kind": "deadline", "after_seconds": after}
        case Interrupted():
            return {"kind": "interrupted"}
        case RemoteFailure(call=call, failure=failure):
            return {
                "kind": "remote_failure",
                "call": call,
                "failure": _failure_json(failure),
            }
        case Crashed(error_type=error_type):
            return {"kind": "crashed", "error_type": error_type}


def _deletion_json(item: Deletion) -> Json:
    return to_json(
        {
            "kind": item.ref.kind,
            "id": str(item.ref.id),
            "label": item.ref.label,
            "outcome": _outcome_json(item.outcome),
        }
    )


def _outcome_json(outcome: DeletionOutcome) -> Json:
    match outcome:
        case "deleted" | "absent" | "skipped":
            return outcome
        case ApiFailure() as failure:
            return _failure_json(failure)


def _failure_json(failure: ApiFailure) -> Json:
    return {
        "error_type": failure.error_type,
        "status_code": failure.status_code,
        "body": failure.body,
    }


def _scenario_json(scenario: ScenarioReport) -> Json:
    runs = [
        to_json(
            {
                "test_run_id": report.run.test_run_id,
                "verdict": report.verdict,
                "status": report.run.status,
                "condition": report.run.condition,
                "path": f"{scenario.stem}/{report.run.test_run_id}",
                "mock_proof_counts": proof_counts(report.calls),
            }
        )
        for report in scenario.runs
    ]
    return {
        "stem": scenario.stem,
        "kind": scenario.kind,
        "test_id": None if scenario.test_id is None else str(scenario.test_id),
        "runs": runs,
    }


def _tool_calls_json(calls: tuple[ProvenCall, ...]) -> Json:
    rows: list[Json] = []
    for proven in calls:
        result = proven.call.result
        rows.append(
            {
                "request_id": proven.call.request_id,
                "tool_name": proven.call.tool_name,
                "params_as_json": proven.call.params_json,
                "tool_has_been_called": proven.call.called,
                "result_value": None if result is None else result.value,
                "is_error": None if result is None else result.is_error,
                "raw_error_message": None
                if result is None
                else result.raw_error_message,
                "mock_proof": proven.proof,
            }
        )
    return rows


def _evals_json(report: RunReport) -> Json:
    return to_json(
        {
            "verdict": report.verdict,
            "status": report.run.status,
            "condition_result": report.run.condition,
            "rationale_summary": report.run.rationale_summary,
            "rationale_messages": list(report.run.rationale_messages),
            "mock_proof_counts": proof_counts(report.calls),
        }
    )
