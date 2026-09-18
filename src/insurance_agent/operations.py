from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from insurance_agent.domain import (
    Applied,
    AppliedProposal,
    Change,
    CommitResult,
    ConfirmationReady,
    LookupResult,
    PendingProposal,
    Policy,
    PolicyFound,
    PolicyStatus,
    Proposal,
    ProposeResult,
    RejectionCode,
    SupersededProposal,
    Vehicle,
    calendar_date,
    format_readback,
    not_amendable,
    parse_effective_date,
    parse_vin,
    rejected,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdSource(Protocol):
    def next_proposal_id(self) -> str: ...

    def next_confirmation_number(self) -> str: ...


class ServicingStore(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def get_policy(self, policy_number: str) -> Policy | None: ...

    def set_policy(self, policy: Policy) -> None: ...

    def get_proposal(self, proposal_id: str) -> Proposal | None: ...

    def set_proposal(self, proposal_id: str, proposal: Proposal) -> None: ...

    def pending_for_policy(self, policy_number: str) -> PendingProposal | None: ...


def lookup(
    store: ServicingStore, clock: Clock, tz: ZoneInfo, policy_number: str
) -> LookupResult:
    with store.transaction():
        policy = store.get_policy(policy_number)
        if policy is None:
            return rejected(RejectionCode.POLICY_NOT_FOUND, policy_number=policy_number)
        match policy.status:
            case PolicyStatus.ACTIVE:
                return PolicyFound(
                    policy_number=policy.policy_number,
                    vehicles=policy.vehicles,
                    earliest_effective_date=calendar_date(clock.now(), tz),
                )
            case PolicyStatus.CANCELLED:
                return rejected(
                    RejectionCode.POLICY_CANCELLED, policy_number=policy.policy_number
                )


def propose(
    store: ServicingStore,
    clock: Clock,
    ids: IdSource,
    tz: ZoneInfo,
    policy_number: str,
    vin_raw: str,
    date_raw: str,
) -> ProposeResult:
    with store.transaction():
        vin = parse_vin(vin_raw)
        if isinstance(vin, RejectionCode):
            return rejected(vin)
        effective = parse_effective_date(date_raw)
        if isinstance(effective, RejectionCode):
            return rejected(effective)
        today = calendar_date(clock.now(), tz)
        if effective < today:
            return rejected(
                RejectionCode.EFFECTIVE_DATE_IN_PAST,
                requested=effective,
                api_date=today,
            )
        policy = store.get_policy(policy_number)
        if policy is None:
            return rejected(RejectionCode.POLICY_NOT_FOUND, policy_number=policy_number)
        blocked = not_amendable(policy)
        if blocked is not None:
            return blocked
        if any(vehicle.vin == vin for vehicle in policy.vehicles):
            return rejected(
                RejectionCode.VEHICLE_ALREADY_ON_POLICY,
                vin=vin,
                policy_number=policy.policy_number,
            )
        prior = store.pending_for_policy(policy.policy_number)
        if prior is not None:
            store.set_proposal(
                prior.proposal_id,
                SupersededProposal(
                    proposal_id=prior.proposal_id,
                    change=prior.change,
                    readback=prior.readback,
                ),
            )
        change = Change(
            policy_number=policy.policy_number,
            vin=vin,
            effective_date=effective,
        )
        proposal_id = ids.next_proposal_id()
        pending = PendingProposal(
            proposal_id=proposal_id,
            change=change,
            readback=format_readback(change),
        )
        store.set_proposal(proposal_id, pending)
        return ConfirmationReady(
            proposal_id=proposal_id,
            change=change,
            readback=pending.readback,
        )


def commit(
    store: ServicingStore,
    clock: Clock,
    ids: IdSource,
    tz: ZoneInfo,
    proposal_id: str,
) -> CommitResult:
    with store.transaction():
        proposal = store.get_proposal(proposal_id)
        if proposal is None:
            return rejected(RejectionCode.PROPOSAL_NOT_FOUND, proposal_id=proposal_id)
        match proposal:
            case AppliedProposal(result=result):
                return result
            case SupersededProposal():
                return rejected(
                    RejectionCode.PROPOSAL_SUPERSEDED, proposal_id=proposal_id
                )
            case PendingProposal() as pending:
                return _apply_pending(store, clock, ids, tz, pending)


def _apply_pending(
    store: ServicingStore,
    clock: Clock,
    ids: IdSource,
    tz: ZoneInfo,
    pending: PendingProposal,
) -> CommitResult:
    change = pending.change
    policy = store.get_policy(change.policy_number)
    if policy is None:
        return rejected(
            RejectionCode.POLICY_NOT_FOUND, policy_number=change.policy_number
        )
    blocked = not_amendable(policy)
    if blocked is not None:
        return blocked
    today = calendar_date(clock.now(), tz)
    if change.effective_date < today:
        return rejected(
            RejectionCode.EFFECTIVE_DATE_IN_PAST,
            requested=change.effective_date,
            api_date=today,
        )
    if any(vehicle.vin == change.vin for vehicle in policy.vehicles):
        return rejected(
            RejectionCode.VEHICLE_ALREADY_ON_POLICY,
            vin=change.vin,
            policy_number=policy.policy_number,
        )
    updated = Policy(
        policy_number=policy.policy_number,
        status=policy.status,
        vehicles=(
            *policy.vehicles,
            Vehicle(vin=change.vin, effective_date=change.effective_date),
        ),
    )
    applied = Applied(
        proposal_id=pending.proposal_id,
        confirmation_number=ids.next_confirmation_number(),
        change=change,
        applied_at=clock.now(),
    )
    store.set_policy(updated)
    store.set_proposal(pending.proposal_id, AppliedProposal(result=applied))
    return applied
