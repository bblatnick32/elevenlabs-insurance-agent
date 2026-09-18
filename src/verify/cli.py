import argparse

from verify.api_suite import run_api_suite


def main() -> None:
    parser = argparse.ArgumentParser(prog="verify")
    parser.add_argument("args", nargs="+")
    parser.add_argument("--promote", action="store_true")
    parsed = parser.parse_args()
    if parsed.args == ["api", "suite"]:
        raise SystemExit(run_api_suite(promote=parsed.promote))
    parser.error("supported command: verify api suite")


if __name__ == "__main__":
    main()
