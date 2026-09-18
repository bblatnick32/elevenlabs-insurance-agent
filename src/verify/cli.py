import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from verify.api_suite import run_api_suite
from verify.doctor import run_doctor


@dataclass(frozen=True, slots=True)
class DoctorCommand:
    root: Path


@dataclass(frozen=True, slots=True)
class ApiSuiteCommand:
    promote: bool


class _Args(argparse.Namespace):
    command: str | None = None
    root: Path | None = None
    api_command: str | None = None
    promote: bool = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--root", type=Path, metavar="PATH")

    api = commands.add_parser("api")
    api_commands = api.add_subparsers(dest="api_command", required=True)
    suite = api_commands.add_parser("suite")
    suite.add_argument("--promote", action="store_true")
    return parser


def _parse() -> DoctorCommand | ApiSuiteCommand:
    parser = _build_parser()
    args = _Args()
    parser.parse_args(namespace=args)
    if args.command == "doctor":
        root = Path.cwd() if args.root is None else args.root
        return DoctorCommand(root=root)
    if args.command == "api" and args.api_command == "suite":
        return ApiSuiteCommand(promote=args.promote)
    parser.error("unsupported command")


def main() -> None:
    parsed = _parse()
    match parsed:
        case DoctorCommand(root=root):
            raise SystemExit(run_doctor(root, frozenset(os.environ)))
        case ApiSuiteCommand(promote=promote):
            raise SystemExit(run_api_suite(promote=promote))


if __name__ == "__main__":
    main()
