import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from insurance_backend.http import ROUTES, Route
from verify.artifacts import Json

AGENT_FILE = "agent_configs/policy-servicing-vehicle-addition-agent.json"
TOOLS_FILE = "tools.json"
TESTS_FILE = "tests.json"
TOOL_CONFIG_ROOT = Path("tool_configs")
TEST_CONFIG_ROOT = Path("test_configs")
COMMIT_TOOL = "commit_vehicle_addition"
TEST_MANIFEST: Mapping[str, str] = {
    "01-happy-path": "simulation",
    "02-date-correction": "simulation",
    "03-declines-confirmation": "tool",
    "04-cancelled-policy": "simulation",
    "05-backdated-date": "simulation",
    "06-unknown-policy": "simulation",
    "07-garbled-vin": "simulation",
    "08-pressure-to-skip": "tool",
}
CLIENT_TOOL_BEHAVIOR: Mapping[str, Json] = {
    "assignments": [],
    "disable_interruptions": False,
    "dynamic_variables": {"dynamic_variable_placeholders": {}},
    "execution_mode": "immediate",
    "expects_response": True,
    "force_pre_tool_speech": False,
    "interruption_mode": "allow",
    "pre_tool_speech": "auto",
    "response_timeout_secs": 20,
    "tool_call_sound": None,
    "tool_call_sound_behavior": "auto",
    "tool_error_handling_mode": "passthrough",
    "type": "client",
}


@dataclass(frozen=True, slots=True)
class ProjectFailure:
    path: str
    detail: str


def project_failures(root: Path) -> tuple[ProjectFailure, ...]:
    failures: list[ProjectFailure] = []
    agent = _object(root, AGENT_FILE, failures)
    tool_entries = _registry(root, TOOLS_FILE, "tools", failures)
    test_entries = _registry(root, TESTS_FILE, "tests", failures)
    tool_ids = _tool_failures(root, tool_entries, failures)
    test_ids = _test_failures(root, test_entries, tool_ids, failures)
    if agent is not None:
        _agent_links(agent, tool_ids, test_ids, failures)
    return tuple(failures)


def _tool_failures(
    root: Path,
    entries: Sequence[dict[str, Json]],
    failures: list[ProjectFailure],
) -> dict[str, str]:
    tool_ids: dict[str, str] = {}
    for index, entry in enumerate(entries):
        pointer = f"/tools/{index}"
        if entry.get("type") != "client":
            failures.append(
                ProjectFailure(TOOLS_FILE, f"{pointer}/type: must be client")
            )
        tool_id = entry.get("id")
        if not isinstance(tool_id, str) or not tool_id.startswith("tool_"):
            failures.append(
                ProjectFailure(TOOLS_FILE, f"{pointer}/id: invalid tool id")
            )
            continue
        relative = _config_path(
            entry.get("config"), TOOL_CONFIG_ROOT, TOOLS_FILE, pointer, failures
        )
        if relative is None:
            continue
        raw = _object(root, relative, failures)
        if raw is None:
            continue
        slug = raw.get("name")
        if not isinstance(slug, str) or slug not in ROUTES:
            failures.append(
                ProjectFailure(relative, "/name: must name one of the three tools")
            )
            continue
        if slug in tool_ids:
            failures.append(ProjectFailure(relative, f"/name: duplicate tool {slug}"))
            continue
        tool_ids[slug] = tool_id
        if Path(relative).stem != slug:
            failures.append(
                ProjectFailure(
                    relative, f"/name: must match file stem {Path(relative).stem}"
                )
            )
        _client_tool_failures(relative, raw, ROUTES[slug], failures)
    failures.extend(
        ProjectFailure(TOOLS_FILE, f"missing client tool {slug}")
        for slug in sorted(set(ROUTES) - set(tool_ids))
    )
    return tool_ids


def _client_tool_failures(
    relative: str,
    raw: dict[str, Json],
    route: Route,
    failures: list[ProjectFailure],
) -> None:
    for field, expected in CLIENT_TOOL_BEHAVIOR.items():
        if raw.get(field) != expected:
            failures.append(ProjectFailure(relative, f"/{field}: must be {expected}"))
    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        failures.append(ProjectFailure(relative, "/parameters: expected an object"))
        return
    expected_names = set(route.path_params) | set(route.body_fields)
    required_raw = parameters.get("required")
    properties = parameters.get("properties")
    required: set[str] = (
        {item for item in cast(list[Json], required_raw) if isinstance(item, str)}
        if isinstance(required_raw, list)
        else set()
    )
    property_names: set[str] = (
        set(cast(dict[str, Json], properties))
        if isinstance(properties, dict)
        else set()
    )
    if required != expected_names or property_names != expected_names:
        failures.append(
            ProjectFailure(
                relative,
                f"/parameters: properties and required must be exactly {sorted(expected_names)}",
            )
        )
        return
    for name in sorted(expected_names):
        value = properties.get(name) if isinstance(properties, dict) else None
        if not isinstance(value, dict):
            failures.append(
                ProjectFailure(
                    relative, f"/parameters/properties/{name}: expected object"
                )
            )
            continue
        if value.get("type") != "string":
            failures.append(
                ProjectFailure(
                    relative, f"/parameters/properties/{name}/type: must be string"
                )
            )
        description = value.get("description")
        if not isinstance(description, str) or not description.strip():
            failures.append(
                ProjectFailure(
                    relative,
                    f"/parameters/properties/{name}/description: must be nonempty",
                )
            )


def _test_failures(
    root: Path,
    entries: Sequence[dict[str, Json]],
    tool_ids: Mapping[str, str],
    failures: list[ProjectFailure],
) -> dict[str, str]:
    test_ids: dict[str, str] = {}
    tool_names_by_id = {tool_id: slug for slug, tool_id in tool_ids.items()}
    for index, entry in enumerate(entries):
        pointer = f"/tests/{index}"
        relative = _config_path(
            entry.get("config"), TEST_CONFIG_ROOT, TESTS_FILE, pointer, failures
        )
        if relative is None:
            continue
        stem = Path(relative).stem
        expected_kind = TEST_MANIFEST.get(stem)
        if expected_kind is None:
            failures.append(ProjectFailure(relative, "unknown test config"))
            continue
        if entry.get("type") != expected_kind:
            failures.append(
                ProjectFailure(TESTS_FILE, f"{pointer}/type: must be {expected_kind}")
            )
        test_id = entry.get("id")
        if not isinstance(test_id, str) or not test_id.startswith("test_"):
            failures.append(
                ProjectFailure(TESTS_FILE, f"{pointer}/id: invalid test id")
            )
            continue
        if stem in test_ids:
            failures.append(ProjectFailure(TESTS_FILE, f"{pointer}: duplicate {stem}"))
            continue
        test_ids[stem] = test_id
        raw = _object(root, relative, failures)
        if raw is None:
            continue
        if raw.get("type") != expected_kind:
            failures.append(ProjectFailure(relative, f"/type: must be {expected_kind}"))
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            failures.append(ProjectFailure(relative, "/name: must be nonempty"))
        if _contains_local_sentinel(raw):
            failures.append(
                ProjectFailure(relative, "contains obsolete local: sentinel")
            )
        if expected_kind == "simulation":
            _simulation_failures(relative, raw, tool_names_by_id, failures)
        else:
            _tool_test_failures(relative, raw, tool_ids, failures)
    failures.extend(
        ProjectFailure(TESTS_FILE, f"missing test {stem}")
        for stem in sorted(set(TEST_MANIFEST) - set(test_ids))
    )
    return test_ids


def _simulation_failures(
    relative: str,
    raw: dict[str, Json],
    tool_names_by_id: Mapping[str, str],
    failures: list[ProjectFailure],
) -> None:
    scenario = raw.get("simulation_scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        failures.append(
            ProjectFailure(relative, "/simulation_scenario: must be nonempty")
        )
    conditions = raw.get("success_conditions")
    if (
        not isinstance(conditions, list)
        or not conditions
        or any(not isinstance(item, str) or not item.strip() for item in conditions)
    ):
        failures.append(
            ProjectFailure(relative, "/success_conditions: must be nonempty")
        )
    max_turns = raw.get("simulation_max_turns")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
        failures.append(
            ProjectFailure(relative, "/simulation_max_turns: must be positive")
        )
    mock_config = raw.get("tool_mock_config")
    if not isinstance(mock_config, dict) or mock_config != {
        "mocking_strategy": "all",
        "fallback_strategy": "raise_error",
    }:
        failures.append(
            ProjectFailure(
                relative,
                "/tool_mock_config: must mock all tools and raise on no match",
            )
        )
    overrides = raw.get("tool_mock_overrides")
    if not isinstance(overrides, dict) or not overrides:
        failures.append(
            ProjectFailure(relative, "/tool_mock_overrides: must be nonempty")
        )
        return
    for tool_id, mocks in overrides.items():
        slug = tool_names_by_id.get(tool_id)
        if slug is None:
            failures.append(
                ProjectFailure(
                    relative, f"/tool_mock_overrides/{tool_id}: unknown tool id"
                )
            )
            continue
        if not isinstance(mocks, list) or not mocks:
            failures.append(
                ProjectFailure(
                    relative, f"/tool_mock_overrides/{tool_id}: must be nonempty"
                )
            )
            continue
        for index, mock in enumerate(mocks):
            _mock_failures(relative, tool_id, index, mock, ROUTES[slug], failures)


def _mock_failures(
    relative: str,
    tool_id: str,
    index: int,
    mock: Json,
    route: Route,
    failures: list[ProjectFailure],
) -> None:
    pointer = f"/tool_mock_overrides/{tool_id}/{index}"
    if not isinstance(mock, dict):
        failures.append(ProjectFailure(relative, f"{pointer}: expected object"))
        return
    result = mock.get("mock_result")
    if not isinstance(result, str):
        failures.append(
            ProjectFailure(relative, f"{pointer}/mock_result: must be string")
        )
    else:
        try:
            route.result.validate_python(json.loads(result))
        except json.JSONDecodeError, ValidationError:
            failures.append(
                ProjectFailure(
                    relative, f"{pointer}/mock_result: must match the API result"
                )
            )
    conditions = mock.get("parameter_conditions", [])
    if not isinstance(conditions, list):
        failures.append(
            ProjectFailure(relative, f"{pointer}/parameter_conditions: must be a list")
        )
        return
    expected_names = set(route.path_params) | set(route.body_fields)
    for condition_index, condition in enumerate(conditions):
        condition_pointer = f"{pointer}/parameter_conditions/{condition_index}"
        if not isinstance(condition, dict):
            failures.append(
                ProjectFailure(relative, f"{condition_pointer}: expected object")
            )
            continue
        if condition.get("path") not in expected_names:
            failures.append(
                ProjectFailure(relative, f"{condition_pointer}/path: unknown parameter")
            )
        strategy = condition.get("eval")
        if not isinstance(strategy, dict) or strategy.get("type") not in {
            "anything",
            "exact",
            "llm",
            "regex",
        }:
            failures.append(
                ProjectFailure(relative, f"{condition_pointer}/eval: invalid strategy")
            )


def _tool_test_failures(
    relative: str,
    raw: dict[str, Json],
    tool_ids: Mapping[str, str],
    failures: list[ProjectFailure],
) -> None:
    history = raw.get("chat_history")
    if not isinstance(history, list) or not history:
        failures.append(ProjectFailure(relative, "/chat_history: must be nonempty"))
    else:
        previous = -1
        for index, turn in enumerate(history):
            pointer = f"/chat_history/{index}"
            if not isinstance(turn, dict):
                failures.append(ProjectFailure(relative, f"{pointer}: expected object"))
                continue
            if turn.get("role") not in {"agent", "user"}:
                failures.append(ProjectFailure(relative, f"{pointer}/role: invalid"))
            message = turn.get("message")
            if not isinstance(message, str) or not message.strip():
                failures.append(ProjectFailure(relative, f"{pointer}/message: empty"))
            seconds = turn.get("time_in_call_secs")
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, int | float)
                or seconds <= previous
            ):
                failures.append(
                    ProjectFailure(
                        relative, f"{pointer}/time_in_call_secs: must increase"
                    )
                )
            else:
                previous = int(seconds)
            _history_tools(relative, pointer, turn, failures)
    parameters = raw.get("tool_call_parameters")
    referenced = (
        parameters.get("referenced_tool") if isinstance(parameters, dict) else None
    )
    commit_id = tool_ids.get(COMMIT_TOOL)
    if not isinstance(parameters, dict) or parameters.get("verify_absence") is not True:
        failures.append(
            ProjectFailure(
                relative, "/tool_call_parameters/verify_absence: must be true"
            )
        )
    if (
        not isinstance(referenced, dict)
        or referenced.get("id") != commit_id
        or referenced.get("type") != "client"
    ):
        failures.append(
            ProjectFailure(
                relative,
                "/tool_call_parameters/referenced_tool: must reference the client commit tool",
            )
        )


def _history_tools(
    relative: str,
    pointer: str,
    turn: dict[str, Json],
    failures: list[ProjectFailure],
) -> None:
    for field in ("tool_calls", "tool_results"):
        items = turn.get(field, [])
        if not isinstance(items, list):
            failures.append(
                ProjectFailure(relative, f"{pointer}/{field}: must be a list")
            )
            continue
        for index, item in enumerate(items):
            item_pointer = f"{pointer}/{field}/{index}"
            if not isinstance(item, dict):
                failures.append(
                    ProjectFailure(relative, f"{item_pointer}: expected object")
                )
                continue
            if item.get("type") != "client":
                failures.append(
                    ProjectFailure(relative, f"{item_pointer}/type: must be client")
                )
            if item.get("tool_name") not in ROUTES:
                failures.append(
                    ProjectFailure(relative, f"{item_pointer}/tool_name: unknown tool")
                )


def _agent_links(
    agent: dict[str, Json],
    tool_ids: Mapping[str, str],
    test_ids: Mapping[str, str],
    failures: list[ProjectFailure],
) -> None:
    config = agent.get("conversation_config")
    section = config.get("agent") if isinstance(config, dict) else None
    prompt = section.get("prompt") if isinstance(section, dict) else None
    actual_tools = prompt.get("tool_ids") if isinstance(prompt, dict) else None
    expected_tools = [tool_ids[slug] for slug in ROUTES if slug in tool_ids]
    if actual_tools != expected_tools:
        failures.append(
            ProjectFailure(
                AGENT_FILE,
                "/conversation_config/agent/prompt/tool_ids: must match tools.json order",
            )
        )
    platform = agent.get("platform_settings")
    testing = platform.get("testing") if isinstance(platform, dict) else None
    attached = testing.get("attached_tests") if isinstance(testing, dict) else None
    actual_tests = (
        [
            item.get("test_id")
            for item in attached
            if isinstance(item, dict) and isinstance(item.get("test_id"), str)
        ]
        if isinstance(attached, list)
        else []
    )
    expected_tests = [test_ids[stem] for stem in TEST_MANIFEST if stem in test_ids]
    if actual_tests != expected_tests:
        failures.append(
            ProjectFailure(
                AGENT_FILE,
                "/platform_settings/testing/attached_tests: must match tests.json order",
            )
        )


def _registry(
    root: Path,
    relative: str,
    key: str,
    failures: list[ProjectFailure],
) -> list[dict[str, Json]]:
    payload = _object(root, relative, failures)
    if payload is None:
        return []
    entries = payload.get(key)
    if set(payload) != {key} or not isinstance(entries, list):
        failures.append(ProjectFailure(relative, f"/{key}: expected one list"))
        return []
    result: list[dict[str, Json]] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            result.append(entry)
        else:
            failures.append(
                ProjectFailure(relative, f"/{key}/{index}: expected an object")
            )
    return result


def _config_path(
    value: Json | None,
    expected_parent: Path,
    registry: str,
    pointer: str,
    failures: list[ProjectFailure],
) -> str | None:
    if not isinstance(value, str):
        failures.append(ProjectFailure(registry, f"{pointer}/config: expected a path"))
        return None
    path = Path(value)
    if (
        path.parent != expected_parent
        or path.suffix != ".json"
        or path.is_absolute()
        or ".." in path.parts
    ):
        failures.append(
            ProjectFailure(registry, f"{pointer}/config: unsafe config path")
        )
        return None
    return value


def _object(
    root: Path, relative: str, failures: list[ProjectFailure]
) -> dict[str, Json] | None:
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file():
            failures.append(ProjectFailure(relative, "unreadable file"))
            return None
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        detail = (
            "unreadable file"
            if isinstance(error, OSError)
            else f"invalid JSON: {error.msg}"
        )
        failures.append(ProjectFailure(relative, detail))
        return None
    if not isinstance(payload, dict):
        failures.append(ProjectFailure(relative, "expected a JSON object"))
        return None
    return cast(dict[str, Json], payload)


def _contains_local_sentinel(value: Json) -> bool:
    if isinstance(value, str):
        return value.startswith("local:")
    if isinstance(value, list):
        return any(_contains_local_sentinel(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_local_sentinel(item) for item in value.values())
    return False
