---
name: verify-insurance-agent
description: Drive and prove the ElevenLabs policy-servicing insurance agent on its HTTP API, runner, and native conversation suites. Use after changing policy rules, tools, prompts, or the live agent runner.
---

# Verify the insurance agent

This skill is for the next agent. Drive the real harnesses. Do not write a throwaway script. Do not treat compile, lint, or `uv run agent` exit 0 as proof.

## Surfaces

Primary surface is the local policy-servicing HTTP API. The voice agent reaches it through three client tools.

- `GET /v1/policies/{policy_number}` (`lookup_policy`)
- `POST /v1/policies/{policy_number}/vehicle-addition-proposals` (`propose_vehicle_addition`)
- `POST /v1/vehicle-addition-proposals/{proposal_id}/commit` (`commit_vehicle_addition`)

Other surfaces:

- `uv run agent` is one live microphone conversation. A human speaks. Conversation content never sets the exit code.
- `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"` runs the eight persistent Simulation and Tool Call tests attached to the agent. Their test-scoped mocks target the persistent client tools. The command does not start `insurance_backend`.
- `uv run python -m unittest discover -s tests` proves the runner maps tool calls onto HTTP and rejects extra parameters. It does not judge speech.

There is no web UI.

## Launch

Install once per checkout:

```bash
uv sync --locked
```

There is no long-lived server. Each harness starts what it needs and tears it down.

| Drive | What starts | Ready when | Teardown |
|---|---|---|---|
| `uv run verify api suite` | FastAPI on `127.0.0.1` port 0, fixture store, clock `2026-09-17T20:45:12Z` | stdout prints `pass artifacts/api-suite/<run_id>` or `fail …` | `finally` stops uvicorn |
| `uv run python -m unittest discover -s tests` | one ephemeral API per test | unittest exits 0 | `serving()` context |
| `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"` | one native test invocation against the persistent agent and tests | CLI exits 0 and every attached test is `passed` | no resources are created |
| `uv run agent` | ephemeral API plus ElevenLabs Conversation | stdout shows `local API http://127.0.0.1:<port>` | Ctrl+C or remote close |

Seed data is `fixtures/policies.json`: `POL-1001` active, `POL-2002` cancelled, `POL-3003` active, all with empty vehicle lists. Live `uv run agent` uses a real clock. The API suite pins the clock.

Auth:

- API suite and unittest need no ElevenLabs key.
- Agent suite requires `ELEVENLABS_API_KEY` in `.env` or the environment. Python loads `.env` with `override=False`.
- A private live agent also needs that key. A public agent can run without it.

## Doctor

Run this first whenever anything looks off:

```bash
uv run verify doctor
```

Read-only. Starts no server. Creates no remote objects. Last stdout line must be `pass`.

Expected stdout on a healthy checkout:

```text
fixtures ok
contracts ok
elevenlabs_project ok
ELEVENLABS_API_KEY present
pass
```

`ELEVENLABS_API_KEY absent` is still `pass` if fixtures, contracts, client tools, tests, mocks, and agent attachments are clean. Do not run the native ElevenLabs tests while the key is absent.

If doctor fails, fix the printed `path: detail` lines. Do not drive.

Live-microphone extras are outside doctor. `uv run agent` needs PortAudio and PyAudio on Darwin. Missing PyAudio prints `PyAudio is not installed` and exits 1.

## Drive

Read `features/README.md`, then the feature file for the change. Drive every entry point that file lists. A proof that takes one convenient path is incomplete when the map lists others.

Pick the harness from the change:

| Changed | Command | Forbidden substitute |
|---|---|---|
| `src/insurance_backend/`, `contracts/`, `fixtures/` | `uv run verify api suite` | `uv run agent` exit 0; native test mocks |
| `src/agent/`, tool parameter mapping | `uv run python -m unittest discover -s tests` | conversation PASS/FAIL (the runner has none) |
| prompt, `test_configs/`, `tool_configs/`, confirmation rules in speech | `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"` | API suite; local runner exit 0 |
| live microphone session | a human call plus printed `tool  >` lines | any suite |

The API suite has no per-case filter. One command exercises every HTTP feature. After it exits, read `artifacts/api-suite/<run_id>/summary.json` and require the case names the feature file lists.

The native command runs all eight tests attached in `agent_configs/policy-servicing-vehicle-addition-agent.json`. Require each named test to pass in the CLI result; detailed transcripts and evaluator rationale remain in the ElevenLabs test invocation.

Stable handles are route paths, `kind` values, case names, and test config stems. There are no ARIA roles.

Do not send `caller_confirmed` on commit. The API returns 422. The runner rejects the parameter before HTTP.

Do not drive a live `uv run agent` session you did not start. It owns the microphone.

## Evidence

Proof is the action plus the resulting state.

API suite:

- Command, exit code, and the path printed on stdout.
- `artifacts/api-suite/<run_id>/summary.json` must have `"result": "pass"`.
- Named cases in that file must be `"verdict": "pass"`.
- `artifacts/api-suite/<run_id>/http-exchanges.json` must show the request and the `kind` body.
- Mutation proof is a later lookup or store snapshot case in the same run (`pol_1001_one_vehicle`, `pol_2002_no_mutation`, `pol_3003_no_mutation`, `commit_recheck_no_mutation`).

Native agent tests:

- Keep the CLI command, exit code, invocation ID, and per-test statuses.
- Every feature-mapped test must be `passed`; inspect its remote transcript and evaluator rationale when a result is ambiguous.
- Simulation tests use `mocking_strategy: all` and `fallback_strategy: raise_error`. A tool call whose parameters match no test override fails closed instead of contacting a live handler.

Unittest:

- Exit code 0.
- `test_normal_conversation_has_no_behavioral_verdict` prints a `tool  > lookup_policy` line and no `PASS`/`FAIL`.
- `test_unexpected_tool_argument_is_rejected_before_http` prints `unexpected_parameter`.

Live call:

- Printed `tool  >` lines with method, path, status, and `kind`.
- Success speech is allowed only after `kind` `applied`.
- Exit 0 after a decline or a completed change is not a verdict.

Mocks are valid only for native ElevenLabs tests. They live in `test_configs/` as `tool_mock_overrides` keyed by the persistent client-tool IDs. They do not run during a live microphone conversation.

`--promote` copies a passing API run into `artifacts/example-run/api-suite/`. Use it only when updating the checked-in example. It is not required for a proof.

`artifacts/*` is gitignored except `artifacts/example-run/`. Leave proof on disk. Do not commit a run unless asked.

## Cleanup

Do not kill by process name.

- API suite and unittest stop the uvicorn they started. No extra cleanup.
- Native tests reuse the persistent agent, tools, and test IDs. They require no resource cleanup.
- If you started `uv run agent`, stop that process with Ctrl+C. Do not kill other terminals' agent processes.
- Never delete `artifacts/`. Proof must remain after teardown.

## Helpers

The harness is the repo `verify` console script in `src/verify/`. Do not add a second control CLI.

```bash
uv run verify doctor
uv run verify api suite
uv run verify api suite --promote
uv run python -m unittest discover -s tests
elevenlabs agents test policy-servicing-vehicle-addition-agent \
  --intent "verify policy servicing conversation behavior"
```

`--help` is the command surface. Suites print the evidence directory on stdout.
