import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from insurance_backend.domain import (
    PendingProposal,
    Policy,
    PolicyStatus,
    Proposal,
    RejectionCode,
    Vehicle,
    parse_effective_date,
    parse_vin,
)


@dataclass(frozen=True)
class StoreSnapshot:
    policies: dict[str, Policy]
    proposals: dict[str, Proposal]


class Store:
    def __init__(self, policies: dict[str, Policy]) -> None:
        self._lock = threading.Lock()
        self._policies = dict(policies)
        self._proposals: dict[str, Proposal] = {}

    @classmethod
    def from_fixture_file(cls, path: Path) -> Store:
        payload = json.loads(path.read_text())
        policies: dict[str, Policy] = {}
        for raw in payload["policies"]:
            vehicles: list[Vehicle] = []
            for item in raw["vehicles"]:
                match parse_vin(item["vin"]):
                    case RejectionCode():
                        raise ValueError(f"invalid fixture VIN {item['vin']!r}")
                    case vin:
                        pass
                match parse_effective_date(item["effective_date"]):
                    case RejectionCode():
                        raise ValueError(
                            f"invalid fixture effective date {item['effective_date']!r}"
                        )
                    case effective:
                        pass
                vehicles.append(Vehicle(vin=vin, effective_date=effective))
            policy = Policy(
                policy_number=raw["policy_number"],
                status=PolicyStatus(raw["status"]),
                vehicles=tuple(vehicles),
            )
            policies[policy.policy_number] = policy
        expected = {"POL-1001", "POL-2002", "POL-3003"}
        if set(policies) != expected:
            raise ValueError(f"fixture must contain {sorted(expected)}")
        return cls(policies)

    @classmethod
    def load_default(cls) -> Store:
        return cls.from_fixture_file(default_fixture_path())

    @contextmanager
    def transaction(self) -> Generator[None]:
        with self._lock:
            yield

    def get_policy(self, policy_number: str) -> Policy | None:
        return self._policies.get(policy_number)

    def set_policy(self, policy: Policy) -> None:
        self._policies[policy.policy_number] = policy

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        return self._proposals.get(proposal_id)

    def set_proposal(self, proposal_id: str, proposal: Proposal) -> None:
        self._proposals[proposal_id] = proposal

    def pending_for_policy(self, policy_number: str) -> PendingProposal | None:
        found: PendingProposal | None = None
        for proposal in self._proposals.values():
            if (
                isinstance(proposal, PendingProposal)
                and proposal.change.policy_number == policy_number
            ):
                if found is not None:
                    raise RuntimeError(f"two pending proposals for {policy_number}")
                found = proposal
        return found

    def replace_status(self, policy_number: str, status: str) -> None:
        policy = self._policies[policy_number]
        self._policies[policy_number] = Policy(
            policy_number=policy.policy_number,
            status=PolicyStatus(status),
            vehicles=policy.vehicles,
        )

    def snapshot(self) -> StoreSnapshot:
        return StoreSnapshot(
            policies=dict(self._policies), proposals=dict(self._proposals)
        )


def default_fixture_path() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "policies.json"
