import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal, NewType
from zoneinfo import ZoneInfo

Vin = NewType("Vin", str)

# ISO 3779 excludes I, O, and Q so they are not confused with 1 and 0.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
# date.fromisoformat also accepts YYYYMMDD.
_EFFECTIVE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class PolicyStatus(Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class RejectionCode(Enum):
    POLICY_NOT_FOUND = "policy_not_found"
    POLICY_CANCELLED = "policy_cancelled"
    INVALID_VIN = "invalid_vin"
    INVALID_EFFECTIVE_DATE = "invalid_effective_date"
    EFFECTIVE_DATE_IN_PAST = "effective_date_in_past"
    PROPOSAL_NOT_FOUND = "proposal_not_found"
    PROPOSAL_SUPERSEDED = "proposal_superseded"
    VEHICLE_ALREADY_ON_POLICY = "vehicle_already_on_policy"


class Recovery(Enum):
    ASK_CALLER_AGAIN = "ask_caller_again"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Vehicle:
    vin: Vin
    effective_date: date


@dataclass(frozen=True)
class Policy:
    policy_number: str
    status: PolicyStatus
    vehicles: tuple[Vehicle, ...]


@dataclass(frozen=True)
class Change:
    policy_number: str
    vin: Vin
    effective_date: date
    type: Literal["vehicle_addition"] = "vehicle_addition"


@dataclass(frozen=True)
class PolicyFound:
    policy_number: str
    vehicles: tuple[Vehicle, ...]
    earliest_effective_date: date


@dataclass(frozen=True)
class ConfirmationReady:
    proposal_id: str
    change: Change
    readback: str


@dataclass(frozen=True)
class Applied:
    proposal_id: str
    confirmation_number: str
    change: Change
    applied_at: datetime


@dataclass(frozen=True)
class Rejected:
    code: RejectionCode
    message: str

    @property
    def recovery(self) -> Recovery:
        return recovery_for(self.code)


@dataclass(frozen=True)
class PendingProposal:
    proposal_id: str
    change: Change
    readback: str


@dataclass(frozen=True)
class SupersededProposal:
    proposal_id: str
    change: Change
    readback: str


@dataclass(frozen=True)
class AppliedProposal:
    result: Applied


Proposal = PendingProposal | SupersededProposal | AppliedProposal
LookupResult = PolicyFound | Rejected
ProposeResult = ConfirmationReady | Rejected
CommitResult = Applied | Rejected


def recovery_for(code: RejectionCode) -> Recovery:
    match code:
        case (
            RejectionCode.POLICY_NOT_FOUND
            | RejectionCode.INVALID_VIN
            | RejectionCode.INVALID_EFFECTIVE_DATE
            | RejectionCode.EFFECTIVE_DATE_IN_PAST
        ):
            return Recovery.ASK_CALLER_AGAIN
        case (
            RejectionCode.POLICY_CANCELLED
            | RejectionCode.PROPOSAL_NOT_FOUND
            | RejectionCode.PROPOSAL_SUPERSEDED
            | RejectionCode.VEHICLE_ALREADY_ON_POLICY
        ):
            return Recovery.ESCALATE


def rejected(
    code: RejectionCode,
    *,
    policy_number: str = "",
    proposal_id: str = "",
    vin: str = "",
    requested: date | None = None,
    api_date: date | None = None,
) -> Rejected:
    return Rejected(
        code=code,
        message=message_for(
            code,
            policy_number=policy_number,
            proposal_id=proposal_id,
            vin=vin,
            requested=requested,
            api_date=api_date,
        ),
    )


def not_amendable(policy: Policy) -> Rejected | None:
    match policy.status:
        case PolicyStatus.ACTIVE:
            return None
        case PolicyStatus.CANCELLED:
            return rejected(
                RejectionCode.POLICY_CANCELLED, policy_number=policy.policy_number
            )


def message_for(
    code: RejectionCode,
    *,
    policy_number: str = "",
    proposal_id: str = "",
    vin: str = "",
    requested: date | None = None,
    api_date: date | None = None,
) -> str:
    match code:
        case RejectionCode.POLICY_NOT_FOUND:
            return (
                f"Policy {policy_number} was not found. "
                "Ask the caller for the policy number again."
            )
        case RejectionCode.POLICY_CANCELLED:
            return f"Policy {policy_number} is cancelled. No change was applied."
        case RejectionCode.INVALID_VIN:
            return (
                "The VIN is not a valid 17-character VIN. "
                "Ask the caller for the VIN again."
            )
        case RejectionCode.INVALID_EFFECTIVE_DATE:
            return (
                "The effective date is not a valid YYYY-MM-DD date. "
                "Ask the caller for the date again."
            )
        case RejectionCode.EFFECTIVE_DATE_IN_PAST:
            if requested is None or api_date is None:
                raise ValueError("past-date rejection needs requested and api dates")
            return (
                f"Effective date {requested.isoformat()} is before {api_date.isoformat()}. "
                f"Ask the caller for an effective date of {api_date.isoformat()} or later."
            )
        case RejectionCode.PROPOSAL_NOT_FOUND:
            return f"Proposal {proposal_id} was not found. No change was applied."
        case RejectionCode.PROPOSAL_SUPERSEDED:
            return f"Proposal {proposal_id} was superseded. No change was applied."
        case RejectionCode.VEHICLE_ALREADY_ON_POLICY:
            return (
                f"Vehicle {vin} is already on policy {policy_number}. "
                "No change was applied."
            )


def parse_vin(raw: str) -> Vin | RejectionCode:
    folded = raw.upper()
    if _VIN_RE.fullmatch(folded) is None:
        return RejectionCode.INVALID_VIN
    return Vin(folded)


def parse_effective_date(raw: str) -> date | RejectionCode:
    matched = _EFFECTIVE_DATE_RE.fullmatch(raw)
    if matched is None:
        return RejectionCode.INVALID_EFFECTIVE_DATE
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    except ValueError:
        return RejectionCode.INVALID_EFFECTIVE_DATE


def calendar_date(instant: datetime, tz: ZoneInfo) -> date:
    return instant.astimezone(tz).date()


def format_readback(change: Change) -> str:
    vin_spoken = " ".join(change.vin)
    policy_spoken = " ".join(
        "dash" if char == "-" else char for char in change.policy_number
    )
    spoken_date = (
        f"{_MONTHS[change.effective_date.month]} "
        f"{change.effective_date.day}, {change.effective_date.year}"
    )
    return (
        f"I will add the vehicle with V I N {vin_spoken} to policy {policy_spoken}, "
        f"effective {spoken_date}. Do you confirm this exact change?"
    )
