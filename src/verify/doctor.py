import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from verify.elevenlabs_local import (
    ARTIFACT_ROOT,
    ArtifactFailures,
    LocalAgent,
    load_local_agent,
)

type CheckName = Literal[
    "python_version",
    "env_file_ignored",
    "secret_scan",
    "elevenlabs_configs",
    "credentials",
]


class Status(StrEnum):
    PASS = "PASS"
    SKIP = "SKIP"
    FAIL = "FAIL"


class SecretPattern(StrEnum):
    ELEVENLABS_SK = "elevenlabs_sk"
    ELEVENLABS_KEY_ASSIGNMENT = "elevenlabs_api_key"
    GITHUB_TOKEN = "github_token"
    AWS_ACCESS_KEY = "aws_access_key"
    GOOGLE_API_KEY = "google_api_key"
    SLACK_TOKEN = "slack_token"
    PEM_PRIVATE_KEY = "pem_private_key"


@dataclass(frozen=True, slots=True)
class SecretHit:
    path: str
    line: int
    label: SecretPattern


@dataclass(frozen=True, slots=True)
class Finding:
    status: Status
    detail: str
    secret_hit: SecretHit | None = None


@dataclass(frozen=True, slots=True)
class GitFiles:
    tracked: frozenset[str]
    committable: frozenset[str]
    env_ignored: bool
    env_tracked: bool


@dataclass(frozen=True, slots=True)
class Tree:
    root: Path
    env_names: frozenset[str]
    git: GitFiles | None


@dataclass(frozen=True, slots=True)
class Check:
    name: CheckName
    run: Callable[[Tree], Finding]


_LS_CACHED = ("git", "ls-files", "-z", "--cached")
_LS_OTHERS = ("git", "ls-files", "-z", "--others", "--exclude-standard")
_CHECK_IGNORE_ENV = ("git", "check-ignore", "-q", "--", ".env")
_GIT_UNAVAILABLE = "git metadata is unavailable"
_SECRET_PATTERNS: tuple[tuple[SecretPattern, re.Pattern[bytes]], ...] = (
    (SecretPattern.ELEVENLABS_SK, re.compile(rb"(?<![A-Za-z0-9_])sk_[A-Za-z0-9]+")),
    (
        SecretPattern.ELEVENLABS_KEY_ASSIGNMENT,
        re.compile(rb"ELEVENLABS_API_KEY\s*=\s*\S+"),
    ),
    (
        SecretPattern.GITHUB_TOKEN,
        re.compile(rb"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+"),
    ),
    (SecretPattern.AWS_ACCESS_KEY, re.compile(rb"(?<![A-Za-z0-9])AKIA[A-Z0-9]+")),
    (SecretPattern.GOOGLE_API_KEY, re.compile(rb"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]+")),
    (SecretPattern.SLACK_TOKEN, re.compile(rb"(?:xox[abprs]|xapp)-[A-Za-z0-9-]+")),
    (
        SecretPattern.PEM_PRIVATE_KEY,
        re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    ),
)


def _run_git(root: Path, argv: tuple[str, ...]) -> tuple[int, bytes] | None:
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            shell=False,
            check=False,
            text=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return completed.returncode, completed.stdout


def _nul_paths(payload: bytes) -> frozenset[str]:
    return frozenset(
        part.decode("utf-8", "surrogateescape") for part in payload.split(b"\0") if part
    )


def _probe_git(root: Path) -> GitFiles | None:
    cached = _run_git(root, _LS_CACHED)
    others = _run_git(root, _LS_OTHERS)
    ignored = _run_git(root, _CHECK_IGNORE_ENV)
    if cached is None or others is None or ignored is None:
        return None
    cached_code, cached_out = cached
    others_code, others_out = others
    ignored_code, _ignored_out = ignored
    if cached_code != 0 or others_code != 0 or ignored_code not in {0, 1}:
        return None
    tracked = _nul_paths(cached_out)
    committable = tracked | _nul_paths(others_out)
    return GitFiles(
        tracked=tracked,
        committable=committable,
        env_ignored=ignored_code == 0,
        env_tracked=".env" in tracked,
    )


def _display_path(relative: str) -> str:
    encoded = relative.encode("utf-8", "surrogateescape")
    if any(pattern.search(encoded) is not None for _, pattern in _SECRET_PATTERNS):
        return "<redacted-path>"
    return relative


def _read_scan_target(root: Path, relative: str) -> bytes | Finding:
    display = _display_path(relative)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return Finding(Status.FAIL, f"unsafe path {display}")
    path = root / candidate
    try:
        if path.is_symlink():
            return Finding(Status.FAIL, f"symlink {display}")
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return Finding(Status.FAIL, f"unsafe path {display}")
        if not path.is_file():
            return Finding(Status.FAIL, f"unreadable file {display}")
        return path.read_bytes()
    except OSError:
        return Finding(Status.FAIL, f"unreadable file {display}")


def _secret_hit(data: bytes, display: str) -> SecretHit | None:
    found: tuple[SecretPattern, int] | None = None
    for label, pattern in _SECRET_PATTERNS:
        match = pattern.search(data)
        if match is None:
            continue
        start = match.start()
        if found is None or start < found[1]:
            found = (label, start)
    if found is None:
        return None
    label, start = found
    return SecretHit(path=display, line=data.count(b"\n", 0, start) + 1, label=label)


def _python_version(_tree: Tree) -> Finding:
    version = sys.version_info
    rendered = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 14):
        return Finding(Status.PASS, f"Python {rendered} satisfies >=3.14")
    return Finding(Status.FAIL, f"Python {rendered} does not satisfy >=3.14")


def _env_file_ignored(tree: Tree) -> Finding:
    git = tree.git
    if git is None:
        return Finding(Status.FAIL, _GIT_UNAVAILABLE)
    if git.env_ignored and not git.env_tracked:
        return Finding(Status.PASS, ".env is ignored and untracked")
    if git.env_tracked:
        return Finding(Status.FAIL, ".env is tracked")
    return Finding(Status.FAIL, ".env is not ignored")


def _secret_scan(tree: Tree) -> Finding:
    git = tree.git
    if git is None:
        return Finding(Status.FAIL, _GIT_UNAVAILABLE)
    if not git.committable:
        return Finding(Status.FAIL, "no committable files to scan")
    for relative in sorted(git.committable):
        if relative == ".env":
            continue
        payload = _read_scan_target(tree.root, relative)
        if isinstance(payload, Finding):
            return payload
        hit = _secret_hit(payload, _display_path(relative))
        if hit is not None:
            return Finding(
                Status.FAIL,
                f"{hit.label} at {hit.path}:{hit.line}",
                secret_hit=hit,
            )
    return Finding(
        Status.PASS,
        f"no secret patterns in {len(git.committable)} committable files",
    )


def _elevenlabs_paths(paths: frozenset[str]) -> frozenset[str]:
    prefix = f"{ARTIFACT_ROOT}/"
    return frozenset(
        path.replace("\\", "/")
        for path in paths
        if path.replace("\\", "/").startswith(prefix)
    )


def _elevenlabs_configs(tree: Tree) -> Finding:
    git = tree.git
    if git is None:
        return Finding(Status.FAIL, _GIT_UNAVAILABLE)
    committable = _elevenlabs_paths(git.committable)
    outcome = load_local_agent(tree.root)
    match outcome:
        case ArtifactFailures(failures=failures):
            head = failures[0]
            more = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
            return Finding(Status.FAIL, f"{head.path}: {head.detail}{more}")
        case LocalAgent() as agent:
            if agent.files != committable:
                names = " ".join(sorted(agent.files ^ committable))
                return Finding(
                    Status.FAIL,
                    f"{ARTIFACT_ROOT}/ tree and Git committable set disagree on {names}",
                )
            return Finding(
                Status.PASS,
                f"{agent.summary} validate against elevenlabs {version('elevenlabs')}",
            )


def _credentials(tree: Tree) -> Finding:
    if "ELEVENLABS_API_KEY" in tree.env_names:
        return Finding(
            Status.SKIP,
            "ELEVENLABS_API_KEY is present; no credentialed checks exist yet",
        )
    return Finding(
        Status.SKIP,
        "ELEVENLABS_API_KEY is not in the environment; credentialed checks not run",
    )


CHECKS: tuple[Check, ...] = (
    Check("python_version", _python_version),
    Check("env_file_ignored", _env_file_ignored),
    Check("secret_scan", _secret_scan),
    Check("elevenlabs_configs", _elevenlabs_configs),
    Check("credentials", _credentials),
)


def _run_check(check: Check, tree: Tree) -> Finding:
    try:
        return check.run(tree)
    except Exception as error:
        return Finding(Status.FAIL, type(error).__name__)


def run_doctor(root: Path, env_names: frozenset[str]) -> int:
    tree = Tree(root=root, env_names=env_names, git=_probe_git(root))
    width = max(len(check.name) for check in CHECKS)
    passed = 0
    skipped = 0
    failed = 0
    skipped_names: list[CheckName] = []
    for check in CHECKS:
        finding = _run_check(check, tree)
        print(f"{finding.status} {check.name:<{width}} {finding.detail}")
        match finding.status:
            case Status.PASS:
                passed += 1
            case Status.SKIP:
                skipped += 1
                skipped_names.append(check.name)
            case Status.FAIL:
                failed += 1
    print(f"doctor: {passed} pass, {skipped} skip, {failed} fail")
    if skipped_names:
        print("not verified: " + " ".join(skipped_names))
    return 1 if failed else 0
