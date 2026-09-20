import argparse
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from verify.api_suite import run_api_suite
from verify.doctor import run_doctor


@dataclass(frozen=True, slots=True)
class ApiSuiteCommand:
    promote: bool


@dataclass(frozen=True, slots=True)
class DoctorCommand:
    pass


class _Args(argparse.Namespace):
    command: str | None = None
    api_command: str | None = None
    promote: bool = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")

    api = commands.add_parser("api")
    api_commands = api.add_subparsers(dest="api_command", required=True)
    suite = api_commands.add_parser("suite")
    suite.add_argument("--promote", action="store_true")
    return parser


def _parse() -> ApiSuiteCommand | DoctorCommand:
    parser = _build_parser()
    args = _Args()
    parser.parse_args(namespace=args)
    if args.command == "doctor":
        return DoctorCommand()
    if args.command == "api" and args.api_command == "suite":
        return ApiSuiteCommand(promote=args.promote)
    parser.error("unsupported command")


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    parsed = _parse()
    match parsed:
        case DoctorCommand():
            raise SystemExit(run_doctor())
        case ApiSuiteCommand(promote=promote):
            raise SystemExit(run_api_suite(promote=promote))


if __name__ == "__main__":
    main()
