import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer

from insurance_backend.domain import (
    Applied,
    Change,
    ConfirmationReady,
    PolicyFound,
    Recovery,
    Rejected,
    RejectionCode,
)
from insurance_backend.servicing import (
    Clock,
    IdSource,
    ServicingStore,
    commit,
    lookup,
    propose,
)

NEW_YORK = ZoneInfo("America/New_York")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class RandomIds:
    def next_proposal_id(self) -> str:
        return "vap_" + secrets.token_hex(13)

    def next_confirmation_number(self) -> str:
        return "CHG-" + secrets.token_hex(6).upper()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VehicleModel(StrictModel):
    vin: str
    effective_date: date


class ChangeModel(StrictModel):
    type: Literal["vehicle_addition"]
    policy_number: str
    vin: str
    effective_date: date


class PolicyFoundResult(StrictModel):
    kind: Literal["policy_found"]
    policy_number: str
    vehicles: list[VehicleModel]
    earliest_effective_date: date


class ConfirmationReadyResult(StrictModel):
    kind: Literal["confirmation_ready"]
    proposal_id: str
    change: ChangeModel
    readback: str


class AppliedResult(StrictModel):
    kind: Literal["applied"]
    proposal_id: str
    confirmation_number: str
    change: ChangeModel
    applied_at: datetime

    @field_serializer("applied_at")
    def serialize_applied_at(self, value: datetime) -> str:
        instant = value.astimezone(UTC).replace(microsecond=0)
        return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


class RejectedResult(StrictModel):
    kind: Literal["rejected"]
    code: RejectionCode
    recovery: Recovery
    message: str


class ProposeRequest(StrictModel):
    vin: str
    effective_date: str


class CommitRequest(StrictModel):
    pass


LookupApiResult = Annotated[
    PolicyFoundResult | RejectedResult,
    Field(discriminator="kind"),
]
ProposeApiResult = Annotated[
    ConfirmationReadyResult | RejectedResult,
    Field(discriminator="kind"),
]
CommitApiResult = Annotated[
    AppliedResult | RejectedResult,
    Field(discriminator="kind"),
]
ApiResult = Annotated[
    PolicyFoundResult | ConfirmationReadyResult | AppliedResult | RejectedResult,
    Field(discriminator="kind"),
]
_API_RESULT_ADAPTER: TypeAdapter[ApiResult] = TypeAdapter(ApiResult)
_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class Route:
    method: Literal["GET", "POST"]
    path: str
    body: type[StrictModel] | None
    result: TypeAdapter[object]

    @property
    def path_params(self) -> tuple[str, ...]:
        return tuple(_PATH_PARAM.findall(self.path))

    @property
    def body_fields(self) -> frozenset[str]:
        return frozenset() if self.body is None else frozenset(self.body.model_fields)


ROUTES: Mapping[str, Route] = {
    "lookup_policy": Route(
        "GET",
        "/v1/policies/{policy_number}",
        None,
        TypeAdapter(LookupApiResult),
    ),
    "propose_vehicle_addition": Route(
        "POST",
        "/v1/policies/{policy_number}/vehicle-addition-proposals",
        ProposeRequest,
        TypeAdapter(ProposeApiResult),
    ),
    "commit_vehicle_addition": Route(
        "POST",
        "/v1/vehicle-addition-proposals/{proposal_id}/commit",
        CommitRequest,
        TypeAdapter(CommitApiResult),
    ),
}


def parse_api_result(data: object) -> ApiResult:
    return _API_RESULT_ADAPTER.validate_python(data)


def to_api(result: PolicyFound | ConfirmationReady | Applied | Rejected) -> ApiResult:
    match result:
        case PolicyFound():
            return PolicyFoundResult(
                kind="policy_found",
                policy_number=result.policy_number,
                vehicles=[
                    VehicleModel(vin=vehicle.vin, effective_date=vehicle.effective_date)
                    for vehicle in result.vehicles
                ],
                earliest_effective_date=result.earliest_effective_date,
            )
        case ConfirmationReady():
            return ConfirmationReadyResult(
                kind="confirmation_ready",
                proposal_id=result.proposal_id,
                change=_change_model(result.change),
                readback=result.readback,
            )
        case Applied():
            return AppliedResult(
                kind="applied",
                proposal_id=result.proposal_id,
                confirmation_number=result.confirmation_number,
                change=_change_model(result.change),
                applied_at=result.applied_at,
            )
        case Rejected():
            return RejectedResult(
                kind="rejected",
                code=result.code,
                recovery=result.recovery,
                message=result.message,
            )


def _change_model(change: Change) -> ChangeModel:
    return ChangeModel(
        type="vehicle_addition",
        policy_number=change.policy_number,
        vin=change.vin,
        effective_date=change.effective_date,
    )


def create_app(
    *, store: ServicingStore, clock: Clock, ids: IdSource, tz: ZoneInfo
) -> FastAPI:
    application = FastAPI()

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get(ROUTES["lookup_policy"].path, response_model=LookupApiResult)
    def get_policy(policy_number: str) -> JSONResponse:
        return _ok(lookup(store, clock, tz, policy_number))

    @application.post(
        ROUTES["propose_vehicle_addition"].path,
        response_model=ProposeApiResult,
    )
    def propose_vehicle_addition(
        policy_number: str, body: ProposeRequest
    ) -> JSONResponse:
        return _ok(
            propose(store, clock, ids, tz, policy_number, body.vin, body.effective_date)
        )

    @application.post(
        ROUTES["commit_vehicle_addition"].path,
        response_model=CommitApiResult,
    )
    # Commit accepts an empty optional JSON body so extra fields 422 before commit runs.
    def commit_proposal(
        proposal_id: str,
        body: Annotated[CommitRequest | None, Body()] = None,
    ) -> JSONResponse:
        return _ok(commit(store, clock, ids, tz, proposal_id))

    return application


def _ok(result: PolicyFound | ConfirmationReady | Applied | Rejected) -> JSONResponse:
    return JSONResponse(content=to_api(result).model_dump(mode="json"), status_code=200)
