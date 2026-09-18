import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from verify.agent_suite import run_agent_suite
from verify.api_suite import run_api_suite
from verify.doctor import run_doctor
from verify.elevenlabs_remote import open_remote


@dataclass(frozen=True, slots=True)
class DoctorCommand:
    root: Path


@dataclass(frozen=True, slots=True)
class ApiSuiteCommand:
    promote: bool


@dataclass(frozen=True, slots=True)
class AgentSuiteCommand:
    pass


class _Args(argparse.Namespace):
    command: str | None = None
    root: Path | None = None
    api_command: str | None = None
    agent_command: str | None = None
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

    agent = commands.add_parser("agent")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("suite")
    return parser


def _parse() -> DoctorCommand | ApiSuiteCommand | AgentSuiteCommand:
    parser = _build_parser()
    args = _Args()
    parser.parse_args(namespace=args)
    if args.command == "doctor":
        root = Path.cwd() if args.root is None else args.root
        return DoctorCommand(root=root)
    if args.command == "api" and args.api_command == "suite":
        return ApiSuiteCommand(promote=args.promote)
    if args.command == "agent" and args.agent_command == "suite":
        return AgentSuiteCommand()
    parser.error("unsupported command")


def main() -> None:
    parsed = _parse()
    match parsed:
        case DoctorCommand(root=root):
            raise SystemExit(run_doctor(root, frozenset(os.environ)))
        case ApiSuiteCommand(promote=promote):
            raise SystemExit(run_api_suite(promote=promote))
        case AgentSuiteCommand():
            raise SystemExit(
                run_agent_suite(env_names=frozenset(os.environ), connect=open_remote)
            )


if __name__ == "__main__":
    main()
