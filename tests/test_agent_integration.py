import io
import unittest
from contextlib import redirect_stdout
from functools import partial

from fastapi import FastAPI

from agent.conversation import (
    AgentId,
    ConversationPort,
    SessionRecord,
    SessionRequest,
)
from agent.local_api import LocalApi
from agent.run import run_agent
from insurance_backend.http import NEW_YORK, ROUTES, RandomIds, SystemClock, create_app
from insurance_backend.server import serving
from insurance_backend.store import Store


class ScriptedConversation(ConversationPort):
    def __init__(
        self,
        record: SessionRecord,
        call: tuple[str, dict[str, object]] | None = None,
    ) -> None:
        self._record = record
        self._call = call

    def session(self, request: SessionRequest) -> SessionRecord:
        if self._call is not None:
            name, parameters = self._call
            request.echo(request.handlers[name](parameters))
        return self._record


def app() -> FastAPI:
    return create_app(
        store=Store.load_default(),
        clock=SystemClock(),
        ids=RandomIds(),
        tz=NEW_YORK,
    )


def run(conversation: ConversationPort) -> int:
    with serving(app()) as port, LocalApi(f"http://127.0.0.1:{port}") as local:
        handlers = {
            name: partial(local.call, name, route) for name, route in ROUTES.items()
        }
        return run_agent(
            AgentId("agent_test"),
            conversation,
            handlers,
            f"http://127.0.0.1:{port}",
        )


class AgentIntegrationTests(unittest.TestCase):
    def test_normal_conversation_has_no_behavioral_verdict(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(
                ScriptedConversation(
                    SessionRecord("conv_test", "ended"),
                    (
                        "lookup_policy",
                        {"policy_number": "POL-1001", "tool_call_id": "call_1"},
                    ),
                )
            )

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertIn(
            "tool  > lookup_policy GET /v1/policies/POL-1001 200 policy_found", rendered
        )
        self.assertIn("conversation conv_test ended", rendered)
        self.assertNotIn("PASS", rendered)
        self.assertNotIn("FAIL", rendered)

    def test_unexpected_tool_argument_is_rejected_before_http(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(
                ScriptedConversation(
                    SessionRecord("conv_test", "ended"),
                    (
                        "commit_vehicle_addition",
                        {
                            "proposal_id": "vap_unknown",
                            "caller_confirmed": True,
                            "tool_call_id": "call_1",
                        },
                    ),
                )
            )

        self.assertEqual(code, 0)
        self.assertIn(
            "tool  > commit_vehicle_addition - - - unexpected_parameter",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
