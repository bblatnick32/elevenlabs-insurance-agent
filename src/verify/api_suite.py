import json
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from pydantic import ValidationError

from insurance_backend.http import create_app, parse_api_result
from insurance_backend.server import start_server, stop_server
from insurance_backend.store import Store
from verify.artifacts import Json, git_commit, repo_root, utc_stamp, write_json

FIXED_INSTANT = datetime(2026, 9, 17, 20, 45, 12, tzinfo=UTC)
NEW_YORK = ZoneInfo("America/New_York")
CONTRACT_VIN = "1HGCM82633A004352"
SECOND_VIN = "2HGCM82633A004352"
FIRST_PROPOSAL_ID = "vap_01K5EPJ7R9QY6M8B4T2D3F1H0B"
CONTRACT_PROPOSAL_ID = "vap_01K5EPJ7R9QY6M8B4T2D3F1H0C"
POL3003_PROPOSAL_ID = "vap_01K5EPJ7R9QY6M8B4T2D3F1H0D"
STATUS_RECHECK_ID = "vap_01K5EPJ7R9QY6M8B4T2D3F1H0E"
CONTRACT_CONFIRMATION = "CHG-2026-0917-0031"


class MutableClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def advance_days(self, days: int) -> None:
        self._instant = self._instant + timedelta(days=days)


class ScriptedIds:
    def __init__(
        self, proposal_ids: list[str], confirmation_numbers: list[str]
    ) -> None:
        self._proposal_ids = list(proposal_ids)
        self._confirmation_numbers = list(confirmation_numbers)

    def next_proposal_id(self) -> str:
        return self._proposal_ids.pop(0)

    def next_confirmation_number(self) -> str:
        return self._confirmation_numbers.pop(0)


class Recorder:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client
        self.exchanges: list[dict[str, object]] = []
        self.verdicts: list[dict[str, object]] = []
        self.failed = False

    def call(
        self,
        method: str,
        path: str,
        request_json: dict[str, object] | None = None,
    ) -> tuple[int, Json]:
        if self.client is None:
            raise RuntimeError("HTTP client is not available")
        if method == "GET":
            response = self.client.get(path)
        elif method == "POST" and request_json is None:
            response = self.client.post(path)
        elif method == "POST":
            response = self.client.post(path, json=request_json)
        else:
            raise ValueError(method)
        body: Json = response.json()
        exchange: dict[str, object] = {
            "method": method,
            "path": path,
            "status": response.status_code,
            "response": body,
        }
        if request_json is not None:
            exchange["request"] = request_json
        self.exchanges.append(exchange)
        return response.status_code, body

    def expect(self, name: str, actual: object, expected: object) -> None:
        ok = actual == expected
        row: dict[str, object] = {"name": name, "verdict": "pass" if ok else "fail"}
        if not ok:
            row["actual"] = actual
            row["expected"] = expected
            self.failed = True
        self.verdicts.append(row)


def _deref(openapi: dict[str, Json], node: Json) -> dict[str, Json]:
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    current: Json = openapi
    for part in ref.split("/")[1:]:
        if not isinstance(current, dict):
            return {}
        current = current.get(part)
    return _deref(openapi, current)


def _union_node(openapi: dict[str, Json], node: Json) -> dict[str, Json]:
    resolved = _deref(openapi, node)
    if resolved.get("oneOf") or resolved.get("anyOf"):
        return resolved
    combined = resolved.get("allOf")
    if not isinstance(combined, list):
        return resolved
    for item in combined:
        nested = _union_node(openapi, item)
        if nested.get("oneOf") or nested.get("anyOf"):
            if "discriminator" not in nested and "discriminator" in resolved:
                return {**nested, "discriminator": resolved["discriminator"]}
            return nested
    return resolved


def discriminated_schema(
    openapi: dict[str, Json], path: str, method: str
) -> dict[str, Json]:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return {"variants": 0, "discriminator": None}
    item = paths.get(path)
    if not isinstance(item, dict):
        return {"variants": 0, "discriminator": None}
    operation = item.get(method)
    if not isinstance(operation, dict):
        return {"variants": 0, "discriminator": None}
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return {"variants": 0, "discriminator": None}
    ok = responses.get("200")
    if not isinstance(ok, dict):
        return {"variants": 0, "discriminator": None}
    content = ok.get("content")
    if not isinstance(content, dict):
        return {"variants": 0, "discriminator": None}
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return {"variants": 0, "discriminator": None}
    node = _union_node(openapi, json_content.get("schema"))
    variants = node.get("oneOf") or node.get("anyOf") or []
    discriminator = node.get("discriminator")
    property_name = None
    if isinstance(discriminator, dict):
        property_name = discriminator.get("propertyName")
    return {
        "variants": len(variants) if isinstance(variants, list) else 0,
        "discriminator": property_name,
    }


def run_api_suite(*, promote: bool) -> int:
    root = repo_root()
    run_id = utc_stamp()
    run_dir = root / "artifacts" / "api-suite" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    contracts = root / "contracts"
    clock = MutableClock(FIXED_INSTANT)
    store = Store.from_fixture_file(root / "fixtures" / "policies.json")
    ids = ScriptedIds(
        [
            FIRST_PROPOSAL_ID,
            CONTRACT_PROPOSAL_ID,
            POL3003_PROPOSAL_ID,
            STATUS_RECHECK_ID,
        ],
        [CONTRACT_CONFIRMATION],
    )
    app = create_app(store=store, clock=clock, ids=ids, tz=NEW_YORK)
    ports: list[int] = []
    recorder = Recorder()
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    try:
        server, thread, port = start_server(app)
        ports.append(port)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:
            recorder.client = client
            _run_cases(recorder, store, clock, contracts)
    except (
        AttributeError,
        httpx.HTTPError,
        IndexError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        recorder.expect("server_lifecycle", str(error), "started")
    finally:
        if server is not None and thread is not None:
            stop_server(server, thread)
    summary = {
        "run_id": run_id,
        "result": "fail" if recorder.failed else "pass",
        "clock": "2026-09-17T20:45:12Z",
        "timezone": "America/New_York",
        "ports": ports,
        "cases": recorder.verdicts,
        "exchange_count": len(recorder.exchanges),
        "git_commit": git_commit(root),
    }
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "http-exchanges.json", recorder.exchanges)
    print(f"{summary['result']} {run_dir}")
    if recorder.failed:
        return 1
    if promote:
        example = root / "artifacts" / "example-run" / "api-suite"
        example.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run_dir / "summary.json", example / "summary.json")
        shutil.copy2(run_dir / "http-exchanges.json", example / "http-exchanges.json")
        print(f"promoted {example}")
    return 0


def _run_cases(
    rec: Recorder, store: Store, clock: MutableClock, contracts: Path
) -> None:
    confirmation_ready = json.loads((contracts / "confirmation-ready.json").read_text())
    applied = json.loads((contracts / "applied.json").read_text())
    cancelled = json.loads((contracts / "rejected-policy-cancelled.json").read_text())
    past = json.loads((contracts / "rejected-effective-date-in-past.json").read_text())

    rec.expect(
        "contract_confirmation_ready",
        parse_api_result(confirmation_ready).model_dump(mode="json"),
        confirmation_ready,
    )
    rec.expect(
        "contract_applied",
        parse_api_result(applied).model_dump(mode="json"),
        applied,
    )
    rec.expect(
        "contract_rejected_policy_cancelled",
        parse_api_result(cancelled).model_dump(mode="json"),
        cancelled,
    )
    rec.expect(
        "contract_rejected_effective_date_in_past",
        parse_api_result(past).model_dump(mode="json"),
        past,
    )
    try:
        parse_api_result({**applied, "ok": True})
    except ValidationError:
        extra_ok = "rejected"
    else:
        extra_ok = "accepted"
    rec.expect("parse_rejects_extra_ok", extra_ok, "rejected")

    rec.expect("healthz", rec.call("GET", "/healthz"), (200, {"status": "ok"}))
    openapi_status, openapi_body = rec.call("GET", "/openapi.json")
    rec.expect("openapi_available", openapi_status, 200)
    if isinstance(openapi_body, dict):
        rec.expect(
            "openapi_lookup_schema",
            discriminated_schema(openapi_body, "/v1/policies/{policy_number}", "get"),
            {"variants": 2, "discriminator": "kind"},
        )
        rec.expect(
            "openapi_propose_schema",
            discriminated_schema(
                openapi_body,
                "/v1/policies/{policy_number}/vehicle-addition-proposals",
                "post",
            ),
            {"variants": 2, "discriminator": "kind"},
        )
        rec.expect(
            "openapi_commit_schema",
            discriminated_schema(
                openapi_body,
                "/v1/vehicle-addition-proposals/{proposal_id}/commit",
                "post",
            ),
            {"variants": 2, "discriminator": "kind"},
        )
    else:
        rec.expect("openapi_lookup_schema", type(openapi_body).__name__, "dict")

    rec.expect(
        "unknown_policy_lookup",
        rec.call("GET", "/v1/policies/POL-9999"),
        (
            200,
            {
                "kind": "rejected",
                "code": "policy_not_found",
                "recovery": "ask_caller_again",
                "message": (
                    "Policy POL-9999 was not found. "
                    "Ask the caller for the policy number again."
                ),
            },
        ),
    )
    rec.expect(
        "unknown_policy_propose",
        rec.call(
            "POST",
            "/v1/policies/POL-9999/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-18"},
        ),
        (
            200,
            {
                "kind": "rejected",
                "code": "policy_not_found",
                "recovery": "ask_caller_again",
                "message": (
                    "Policy POL-9999 was not found. "
                    "Ask the caller for the policy number again."
                ),
            },
        ),
    )
    rec.expect(
        "invalid_vin",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": "1HGCM 82633A004352", "effective_date": "2026-09-18"},
        ),
        (
            200,
            {
                "kind": "rejected",
                "code": "invalid_vin",
                "recovery": "ask_caller_again",
                "message": (
                    "The VIN is not a valid 17-character VIN. "
                    "Ask the caller for the VIN again."
                ),
            },
        ),
    )
    rec.expect(
        "invalid_effective_date",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026/09/17"},
        ),
        (
            200,
            {
                "kind": "rejected",
                "code": "invalid_effective_date",
                "recovery": "ask_caller_again",
                "message": (
                    "The effective date is not a valid YYYY-MM-DD date. "
                    "Ask the caller for the date again."
                ),
            },
        ),
    )
    rec.expect(
        "unknown_proposal",
        rec.call("POST", "/v1/vehicle-addition-proposals/vap_unknown/commit"),
        (
            200,
            {
                "kind": "rejected",
                "code": "proposal_not_found",
                "recovery": "escalate",
                "message": "Proposal vap_unknown was not found. No change was applied.",
            },
        ),
    )

    rec.expect(
        "pol_1001_lookup",
        rec.call("GET", "/v1/policies/POL-1001"),
        (
            200,
            {
                "kind": "policy_found",
                "policy_number": "POL-1001",
                "vehicles": list[Json](),
                "earliest_effective_date": "2026-09-17",
            },
        ),
    )
    before_unexpected_propose = sorted(store.snapshot().proposals)
    unexpected_propose_status, _unexpected_propose_body = rec.call(
        "POST",
        "/v1/policies/POL-1001/vehicle-addition-proposals",
        {"vin": CONTRACT_VIN, "effective_date": "2026-09-19", "ok": True},
    )
    rec.expect("propose_rejects_unexpected_field", unexpected_propose_status, 422)
    rec.expect(
        "propose_unexpected_field_no_proposal",
        sorted(store.snapshot().proposals),
        before_unexpected_propose,
    )
    first_ready = {
        "kind": "confirmation_ready",
        "proposal_id": FIRST_PROPOSAL_ID,
        "change": {
            "type": "vehicle_addition",
            "policy_number": "POL-1001",
            "vin": CONTRACT_VIN,
            "effective_date": "2026-09-19",
        },
        "readback": (
            "I will add the vehicle with V I N 1 H G C M 8 2 6 3 3 A 0 0 4 3 5 2 "
            "to policy P O L dash 1 0 0 1, effective September 19, 2026. "
            "Do you confirm this exact change?"
        ),
    }
    rec.expect(
        "pol_1001_propose",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-19"},
        ),
        (200, first_ready),
    )
    rec.expect(
        "pol_1001_correct",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": "1hgcm82633a004352", "effective_date": "2026-09-18"},
        ),
        (200, confirmation_ready),
    )
    confirmed_status, _confirmed_body = rec.call(
        "POST",
        f"/v1/vehicle-addition-proposals/{CONTRACT_PROPOSAL_ID}/commit",
        {"caller_confirmed": True},
    )
    rec.expect("commit_rejects_caller_confirmed", confirmed_status, 422)
    rec.expect(
        "pol_1001_commit_superseded",
        rec.call("POST", f"/v1/vehicle-addition-proposals/{FIRST_PROPOSAL_ID}/commit"),
        (
            200,
            {
                "kind": "rejected",
                "code": "proposal_superseded",
                "recovery": "escalate",
                "message": (
                    f"Proposal {FIRST_PROPOSAL_ID} was superseded. No change was applied."
                ),
            },
        ),
    )
    rec.expect(
        "pol_1001_commit",
        rec.call(
            "POST", f"/v1/vehicle-addition-proposals/{CONTRACT_PROPOSAL_ID}/commit"
        ),
        (200, applied),
    )
    rec.expect(
        "pol_1001_commit_replay",
        rec.call(
            "POST", f"/v1/vehicle-addition-proposals/{CONTRACT_PROPOSAL_ID}/commit"
        ),
        (200, applied),
    )
    rec.expect(
        "pol_1001_one_vehicle",
        rec.call("GET", "/v1/policies/POL-1001"),
        (
            200,
            {
                "kind": "policy_found",
                "policy_number": "POL-1001",
                "vehicles": [
                    {"vin": CONTRACT_VIN, "effective_date": "2026-09-18"},
                ],
                "earliest_effective_date": "2026-09-17",
            },
        ),
    )
    rec.expect(
        "pol_1001_duplicate_vin",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-18"},
        ),
        (
            200,
            {
                "kind": "rejected",
                "code": "vehicle_already_on_policy",
                "recovery": "escalate",
                "message": (
                    f"Vehicle {CONTRACT_VIN} is already on policy POL-1001. "
                    "No change was applied."
                ),
            },
        ),
    )

    before_cancelled = store.snapshot()
    rec.expect(
        "pol_2002_lookup",
        rec.call("GET", "/v1/policies/POL-2002"),
        (200, cancelled),
    )
    rec.expect(
        "pol_2002_propose",
        rec.call(
            "POST",
            "/v1/policies/POL-2002/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-18"},
        ),
        (200, cancelled),
    )
    after_cancelled = store.snapshot()
    rec.expect(
        "pol_2002_no_mutation",
        {
            "vehicles": list(after_cancelled.policies["POL-2002"].vehicles),
            "proposal_ids": sorted(after_cancelled.proposals),
        },
        {
            "vehicles": list(before_cancelled.policies["POL-2002"].vehicles),
            "proposal_ids": sorted(before_cancelled.proposals),
        },
    )

    rec.expect(
        "pol_3003_past_date",
        rec.call(
            "POST",
            "/v1/policies/POL-3003/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-16"},
        ),
        (200, past),
    )
    rec.expect(
        "pol_3003_same_day",
        rec.call(
            "POST",
            "/v1/policies/POL-3003/vehicle-addition-proposals",
            {"vin": CONTRACT_VIN, "effective_date": "2026-09-17"},
        ),
        (
            200,
            {
                "kind": "confirmation_ready",
                "proposal_id": POL3003_PROPOSAL_ID,
                "change": {
                    "type": "vehicle_addition",
                    "policy_number": "POL-3003",
                    "vin": CONTRACT_VIN,
                    "effective_date": "2026-09-17",
                },
                "readback": (
                    "I will add the vehicle with V I N 1 H G C M 8 2 6 3 3 A 0 0 4 3 5 2 "
                    "to policy P O L dash 3 0 0 3, effective September 17, 2026. "
                    "Do you confirm this exact change?"
                ),
            },
        ),
    )
    clock.advance_days(1)
    rec.expect(
        "pol_3003_commit_after_clock",
        rec.call(
            "POST", f"/v1/vehicle-addition-proposals/{POL3003_PROPOSAL_ID}/commit"
        ),
        (
            200,
            {
                "kind": "rejected",
                "code": "effective_date_in_past",
                "recovery": "ask_caller_again",
                "message": (
                    "Effective date 2026-09-17 is before 2026-09-18. "
                    "Ask the caller for an effective date of 2026-09-18 or later."
                ),
            },
        ),
    )
    rec.expect(
        "pol_3003_no_mutation",
        rec.call("GET", "/v1/policies/POL-3003"),
        (
            200,
            {
                "kind": "policy_found",
                "policy_number": "POL-3003",
                "vehicles": list[Json](),
                "earliest_effective_date": "2026-09-18",
            },
        ),
    )

    rec.expect(
        "commit_recheck_propose",
        rec.call(
            "POST",
            "/v1/policies/POL-1001/vehicle-addition-proposals",
            {"vin": SECOND_VIN, "effective_date": "2026-09-18"},
        ),
        (
            200,
            {
                "kind": "confirmation_ready",
                "proposal_id": STATUS_RECHECK_ID,
                "change": {
                    "type": "vehicle_addition",
                    "policy_number": "POL-1001",
                    "vin": SECOND_VIN,
                    "effective_date": "2026-09-18",
                },
                "readback": (
                    "I will add the vehicle with V I N 2 H G C M 8 2 6 3 3 A 0 0 4 3 5 2 "
                    "to policy P O L dash 1 0 0 1, effective September 18, 2026. "
                    "Do you confirm this exact change?"
                ),
            },
        ),
    )
    with store.transaction():
        store.replace_status("POL-1001", "cancelled")
    rec.expect(
        "commit_recheck_cancelled",
        rec.call("POST", f"/v1/vehicle-addition-proposals/{STATUS_RECHECK_ID}/commit"),
        (
            200,
            {
                "kind": "rejected",
                "code": "policy_cancelled",
                "recovery": "escalate",
                "message": "Policy POL-1001 is cancelled. No change was applied.",
            },
        ),
    )
    rec.expect(
        "commit_recheck_no_mutation",
        [
            {
                "vin": vehicle.vin,
                "effective_date": vehicle.effective_date.isoformat(),
            }
            for vehicle in store.snapshot().policies["POL-1001"].vehicles
        ],
        [{"vin": CONTRACT_VIN, "effective_date": "2026-09-18"}],
    )
