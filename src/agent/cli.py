import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface

from agent.conversation import AgentId
from agent.elevenlabs import open_conversation
from agent.local_api import LocalApi
from agent.run import run_agent
from insurance_backend.http import NEW_YORK, ROUTES, RandomIds, SystemClock, create_app
from insurance_backend.server import serving
from insurance_backend.store import Store

_AGENT_ID = re.compile(r"^agent_[a-z0-9]+$")


@dataclass(frozen=True, slots=True)
class Resolved:
    agent_id: AgentId
    source: str


@dataclass(frozen=True, slots=True)
class Unresolved:
    detail: str


type AgentResolution = Resolved | Unresolved


class _Args(argparse.Namespace):
    agent_id: str | None = None


def _parse() -> _Args:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("--agent-id", metavar="AGENT_ID")
    args = _Args()
    parser.parse_args(namespace=args)
    return args


def resolve_agent_id(
    registry_text: str | None,
    *,
    flag: str | None,
    env: Mapping[str, str],
) -> AgentResolution:
    if flag is not None:
        return _resolution(flag, "--agent-id")
    env_id = env.get("AGENT_ID")
    if env_id:
        return _resolution(env_id, "AGENT_ID")
    return _from_registry(registry_text)


def _from_registry(registry_text: str | None) -> AgentResolution:
    if registry_text is None:
        return Unresolved(
            "agents.json is missing; run `elevenlabs agents add` or pass --agent-id"
        )
    try:
        payload_object: object = json.loads(registry_text)
    except json.JSONDecodeError as error:
        return Unresolved(f"agents.json: invalid JSON: {error.msg}")
    if not isinstance(payload_object, dict):
        return Unresolved("agents.json: expected one agents list")
    payload = cast(dict[str, object], payload_object)
    if set(payload) != {"agents"}:
        return Unresolved("agents.json: expected one agents list")
    entries_object = payload.get("agents")
    if not isinstance(entries_object, list):
        return Unresolved("agents.json: /agents must be a list")
    entries = cast(list[object], entries_object)
    if len(entries) != 1:
        return Unresolved(
            f"agents.json registers {len(entries)} agents; pass --agent-id"
        )
    entry_object = entries[0]
    if not isinstance(entry_object, dict):
        return Unresolved("agents.json: /agents/0 must be an object")
    entry = cast(dict[str, object], entry_object)
    agent_id = entry.get("id")
    config = entry.get("config")
    if not isinstance(agent_id, str):
        return Unresolved(
            "agents.json has no agent id; run `elevenlabs agents push` or pass --agent-id"
        )
    if not isinstance(config, str) or not _safe_config_path(config):
        return Unresolved("agents.json: /agents/0/config is unsafe")
    return _resolution(agent_id, f"agents.json {config}")


def _resolution(value: str, source: str) -> AgentResolution:
    if _AGENT_ID.fullmatch(value) is None:
        return Unresolved(f"{source}: invalid agent id")
    return Resolved(AgentId(value), source)


def _registry_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None


def _safe_config_path(value: str) -> bool:
    path = Path(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parent == Path("agent_configs")
        and path.suffix == ".json"
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
    args = _parse()
    match resolve_agent_id(
        _registry_text(root / "agents.json"), flag=args.agent_id, env=os.environ
    ):
        case Unresolved(detail=detail):
            print(detail, file=sys.stderr)
            raise SystemExit(1)
        case Resolved(agent_id=agent_id, source=source):
            print(f"agent {agent_id} ({source})")
    try:
        audio = DefaultAudioInterface()
    except ImportError:
        print("PyAudio is not installed", file=sys.stderr)
        raise SystemExit(1) from None
    api_key = os.getenv("ELEVENLABS_API_KEY")
    app = create_app(
        store=Store.load_default(),
        clock=SystemClock(),
        ids=RandomIds(),
        tz=NEW_YORK,
    )
    conversation = open_conversation(
        ElevenLabs(api_key=api_key), audio, requires_auth=bool(api_key)
    )
    with serving(app) as port, LocalApi(f"http://127.0.0.1:{port}") as local:
        base_url = f"http://127.0.0.1:{port}"
        handlers = {
            name: partial(local.call, name, route) for name, route in ROUTES.items()
        }
        code = run_agent(agent_id, conversation, handlers, base_url)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
