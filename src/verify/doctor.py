import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from insurance_backend.http import parse_api_result
from insurance_backend.store import Store
from verify.artifacts import repo_root
from verify.project_validation import project_failures

KEY_NAME = "ELEVENLABS_API_KEY"
CONTRACTS = (
    "confirmation-ready.json",
    "applied.json",
    "rejected-policy-cancelled.json",
    "rejected-effective-date-in-past.json",
)


def run_doctor(root: Path | None = None, env: Mapping[str, str] | None = None) -> int:
    resolved = repo_root() if root is None else root
    names = os.environ if env is None else env
    failed = False
    if not _fixtures_ok(resolved):
        failed = True
    if not _contracts_ok(resolved):
        failed = True
    project = project_failures(resolved)
    if project:
        for failure in project:
            print(f"{failure.path}: {failure.detail}", file=sys.stderr)
        failed = True
    else:
        print("elevenlabs_project ok")
    present = KEY_NAME in names and bool(names[KEY_NAME])
    print(f"{KEY_NAME} {'present' if present else 'absent'}")
    print("fail" if failed else "pass")
    return 1 if failed else 0


def _fixtures_ok(root: Path) -> bool:
    path = root / "fixtures" / "policies.json"
    try:
        Store.from_fixture_file(path)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"fixtures/policies.json: {error}", file=sys.stderr)
        return False
    print("fixtures ok")
    return True


def _contracts_ok(root: Path) -> bool:
    ok = True
    for name in CONTRACTS:
        relative = f"contracts/{name}"
        path = root / relative
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
            parse_api_result(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            print(f"{relative}: {error}", file=sys.stderr)
            ok = False
    if ok:
        print("contracts ok")
    return ok
