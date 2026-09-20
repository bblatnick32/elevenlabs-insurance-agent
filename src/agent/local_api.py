import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self
from urllib.parse import quote

import httpx

from agent.conversation import (
    CallFailed,
    HttpExchange,
    ToolCall,
)
from insurance_backend.http import Route


@dataclass(frozen=True, slots=True)
class Planned:
    path: str
    body: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class Unplannable:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]


class LocalApi:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=10.0)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def call(
        self, name: str, route: Route, parameters: Mapping[str, object]
    ) -> ToolCall:
        planned = plan_call(route, parameters)
        if isinstance(planned, Unplannable):
            reason = "missing_parameter" if planned.missing else "unexpected_parameter"
            names = planned.missing or planned.unexpected
            return ToolCall(name, CallFailed(reason, ", ".join(names), 0.0))
        return self._request(name, route, planned)

    def _request(self, name: str, route: Route, plan: Planned) -> ToolCall:
        t0 = time.monotonic()
        try:
            response = self._client.request(route.method, plan.path, json=plan.body)
        except httpx.HTTPError as error:
            return ToolCall(
                name,
                CallFailed("connection", type(error).__name__, time.monotonic() - t0),
            )
        return ToolCall(
            name,
            HttpExchange(
                route.method,
                str(response.url),
                response.status_code,
                response.text,
                time.monotonic() - t0,
            ),
        )


def plan_call(route: Route, parameters: Mapping[str, object]) -> Planned | Unplannable:
    arguments = {
        key: value for key, value in parameters.items() if key != "tool_call_id"
    }
    expected_order = (*route.path_params, *sorted(route.body_fields))
    expected = set(expected_order)
    missing = tuple(name for name in expected_order if name not in arguments)
    unexpected = tuple(sorted(name for name in arguments if name not in expected))
    if missing or unexpected:
        return Unplannable(missing, unexpected)
    path = route.path
    for name in route.path_params:
        path = path.replace("{" + name + "}", quote(str(arguments[name]), safe=""))
    body = (
        None
        if route.method == "GET"
        else {key: arguments[key] for key in route.body_fields}
    )
    return Planned(path, body)
