# ElevenLabs policy servicing agent

This repository is at an API checkpoint. The HTTP API and the real-HTTP API verifier are implemented. Local ElevenLabs agent artifacts and native test definitions are checked in and validated offline. No remote agent, tool, knowledge document, or test exists.

A caller adds one vehicle to a fixture auto policy through an ElevenAgents voice agent and a Python HTTP API.

This project excludes UI, a database, authentication, real policyholder data, a real insurer, and a production escalation system. Never use this project with real policyholder data.

## Call flow

The agent follows this sequence.

1. Ask for a policy number and call `lookup_policy`.
2. Collect the caller's 17-character VIN and requested effective date. Never fill missing values.
3. Call `propose_vehicle_addition`.
4. The API returns `kind` value `confirmation_ready`, a proposal ID, the exact change, and a server-authored `readback`.
5. Speak `readback` verbatim.
6. Call `commit_vehicle_addition(proposal_id)` only after a clear, unqualified yes.
7. Report success only after `kind` value `applied`. On a rejection with `recovery` value `escalate`, state that no change was applied and hand off. In this reference, handoff will be a tested conversation outcome. It does not claim a live transfer.

## Design decision

The protocol is two-step proposal and commit.

The API binds the readback to the committed change. It rechecks policy status and the effective date at commit. It supersedes older pending proposals after corrections. Retries are idempotent.

The API cannot prove that the human said yes. ElevenLabs native tests will evaluate that behavior from the transcript.

The protocol does not accept `caller_confirmed: true`. The LLM would be asserting its own evidence.

ElevenLabs MCP stays out of this project. Approval targets a different operator, adds a server and a workspace opt-in, is not CLI-managed, and does not prove voice-caller consent.

A zero-parameter commit through a sanitized ElevenLabs dynamic variable is a later prototype candidate. Do not freeze it into the contract until a real ElevenLabs run verifies assignment, sanitization, and model behavior.

## Named data shape

The organizing structure is a discriminated JSON result union keyed by `kind`. Optional fields and parallel booleans are not the contract.

The API variants are `policy_found`, `confirmation_ready`, `applied`, and `rejected`.

`rejected.recovery` is either `ask_caller_again` or `escalate`. The API derives that value from the error code.

There is no `caller_confirmed`, `success`, `ok`, or nullable cross-variant field.

The four prototype files are [`contracts/confirmation-ready.json`](contracts/confirmation-ready.json), [`contracts/applied.json`](contracts/applied.json), [`contracts/rejected-policy-cancelled.json`](contracts/rejected-policy-cancelled.json), and [`contracts/rejected-effective-date-in-past.json`](contracts/rejected-effective-date-in-past.json).

`confirmation_ready` performs no policy mutation. `applied` means exactly one mutation. `rejected` means no mutation.

A cancelled lookup returns `rejected` instead of `policy_found`. The agent never has to interpret an amendability status.

## HTTP API

The API exposes four routes.

- `GET /healthz` returns `{"status": "ok"}`.
- `GET /v1/policies/{policy_number}` returns `policy_found` or `rejected`.
- `POST /v1/policies/{policy_number}/vehicle-addition-proposals` accepts `vin` and `effective_date`, then returns `confirmation_ready` or `rejected`.
- `POST /v1/vehicle-addition-proposals/{proposal_id}/commit` accepts no change or confirmation fields, then returns `applied` or `rejected`.

Parsed business results, including `rejected`, return HTTP 200 so a webhook tool always sees the JSON union. Malformed request envelopes may return FastAPI 422.

## Safety ownership and evidence

API-enforced rules are proved by `uv run verify api suite`.

- Cancelled policies do not mutate.
- Dates earlier than the API clock do not mutate.
- Commit applies only the newest live proposal's exact change.
- Repeated commit returns the same result and does not add a second vehicle.
- Every rule is rechecked at commit.

Transcript-evaluated rules are later measured by `uv run verify agent suite`.

- Readback occurs before commit.
- An explicit yes occurs before commit.
- No policy number, VIN, date, or success is invented.
- Rejected or missing data produces a correction request or escalation.

Text Simulation tests do not prove caller identity, legal consent, audio quality, or telephony ASR of a 17-character VIN. If independent consent proof is required, stop and add an authenticated confirmation channel outside this project.

## Planned current ElevenAgents stack

The planned stack is the following.

- Use the official Python SDK and `uv`.
- Reference standalone webhook tools through `conversation_config.agent.prompt.tool_ids`. Do not use deprecated inline `prompt.tools`.
- Set `tool_error_handling_mode` to `passthrough` on each webhook tool.
- Run native Simulation and Tool Call tests through `agents.run_tests`. Do not use deprecated `simulate_conversation` endpoints or deprecated singular `success_condition`.
- Pin the LLM to `gpt-5.6-sol` and TTS to `eleven_v3_conversational`. Credentialed doctor checks must confirm that both are still available before a run.
- Keep the agent prompt and the injected API clock on the same IANA timezone. The reference timezone is `America/New_York`.
- Keep microphone audio and `Conversation` out of the verification commands so headless reviewers do not need PyAudio.

These official pages were read for this checkpoint.

- [Tools](https://elevenlabs.io/docs/eleven-agents/customization/tools)
- [Webhook tools](https://elevenlabs.io/docs/eleven-agents/customization/tools/webhook-tools)
- [Agent testing](https://elevenlabs.io/docs/eleven-agents/customization/agent-testing)
- [Python SDK](https://elevenlabs.io/docs/eleven-agents/libraries/python)
- [Expressive mode](https://elevenlabs.io/docs/eleven-agents/customization/voice/expressive-mode)
- [Models](https://elevenlabs.io/docs/eleven-agents/customization/llm)
- [Model Context Protocol](https://elevenlabs.io/docs/eleven-agents/customization/tools/mcp)

## Reviewer commands

```
uv run verify doctor
uv run verify api suite
uv run verify agent suite
```

`doctor` runs five offline checks in this order.

1. `python_version`. PASS when the running interpreter is Python 3.14 or newer.
2. `env_file_ignored`. PASS when Git reports that `.env` is ignored and untracked. The check never opens `.env`. If Git cannot answer, the check fails.
3. `secret_scan`. Scans Git-tracked files and untracked, unignored files for prefix and structure secret patterns. It does not scan ignored files. A match prints the repo-relative path, the line number, and a pattern label. It never prints matched bytes. If a path matches a secret pattern, the path prints as `<redacted-path>`.
4. `elevenlabs_configs`. Discovers files under `elevenlabs/`, inlines `prompt.md`, and validates the agent, tools, knowledge locator, and tests against the installed ElevenLabs SDK models. Unknown keys, wrong project pins, deprecated `prompt.tools`, singular `success_condition`, and any `simulate_conversation` token fail. The check PASSes when the validated file set matches the Git committable `elevenlabs/` subset. Missing, malformed, extra, untracked, or invalid artifacts FAIL.
5. `credentials`. SKIPs when `ELEVENLABS_API_KEY` is absent. It also SKIPs when that name is present. It never reads the value and never makes a request.

Run `uv run verify doctor`. Pass `--root PATH` to point at another Git work tree.

Doctor does not load `.env`. Git must be available. Exit 0 means the offline checks did not fail. `elevenlabs_configs` proves the local artifacts validate against installed SDK models. It does not prove remote agent state, a live webhook path, or that native tests ran. Exit 1 means at least one check failed. Exit 2 is an argparse error.

If any check SKIPs, doctor prints `not verified:` and the skipped check names.

`api suite` requires no ElevenLabs credentials. Run `uv run verify api suite`. The command starts Uvicorn on an ephemeral `127.0.0.1` port and calls it with HTTPX over a real TCP socket. Do not use FastAPI TestClient, ASGITransport, or direct domain calls as the finish evidence. The suite uses the fixed instant `2026-09-17T20:45:12Z` and timezone `America/New_York`. It writes evidence to `artifacts/api-suite/<run-id>/summary.json` and `http-exchanges.json` before it returns a failing exit code. After every check passes, `uv run verify api suite --promote` copies that run to `artifacts/example-run/api-suite/`.

The API suite uses exactly three synthetic policies. POL-1001 is active for the successful exact-once mutation. POL-2002 is cancelled. POL-3003 is active and receives the backdated request. The suite proves three business rules. Only active policies change. Effective dates are at least the API date. Commit applies the exact newest proposal once.

`agent suite` is not implemented. The planned command requires `ELEVENLABS_API_KEY`. It will use native ElevenLabs tests with webhook mocks and fallback `raise_error`. A public webhook is not required. The suite will write evidence before returning a failing exit code.

Passing the API and mocked agent suites does not prove the live path from ElevenLabs to the webhook. A later manual live run needs a publicly reachable webhook URL.

## Eight scenarios

Exactly six Simulation tests and two Tool Call tests. Files live under `elevenlabs/tests/`.

1. `01-happy-path`. Simulation. Exact readback, explicit yes, one commit, and success only after `applied`.
2. `02-date-correction`. Simulation. Supersede the old proposal, read the new one, and commit only the second proposal.
3. `03-declines-confirmation`. Tool Call test with `verify_absence` on commit.
4. `04-cancelled-policy`. Simulation. No proposal or commit. State that no change was applied and escalate.
5. `05-backdated-date`. Simulation. Relay the rejection, never substitute a date, and ask for a caller-supplied valid date.
6. `06-unknown-policy`. Simulation. Re-ask once, then escalate without inventing a policy.
7. `07-garbled-vin`. Simulation. Re-ask, never pad or repair it, then escalate if the caller cannot provide it.
8. `08-pressure-to-skip`. Tool Call test with `verify_absence` on commit.

## Local ElevenLabs artifacts

The checked-in graph is one `elevenlabs/` directory.

```
elevenlabs/
  agent.json
  prompt.md
  knowledge/
    policy-servicing-rules.md
  tools/
    lookup_policy.json
    propose_vehicle_addition.json
    commit_vehicle_addition.json
  tests/
    01-happy-path.json
    02-date-correction.json
    03-declines-confirmation.json
    04-cancelled-policy.json
    05-backdated-date.json
    06-unknown-policy.json
    07-garbled-vin.json
    08-pressure-to-skip.json
```

JSON files use installed SDK request field names. Local files use `local:<slug>` IDs and webhook URLs on `https://local.invalid`. `agent.prompt.prompt` is absent from `agent.json`. `elevenlabs/prompt.md` is its sole owner and is inlined only in memory before SDK validation.

Tool `response_mocks` own happy-path, correction, rejection, and fallback answers. Tests do not set `mocked_tool_ids` or `tool_mock_overrides`.

## Artifacts

A passing API suite writes `artifacts/api-suite/<run-id>/summary.json` and `http-exchanges.json`.

A passing agent suite will write `artifacts/agent-suite/<invocation-id>/manifest.json` and `raw-invocation.json`. Each scenario and each test run will also write `transcript.json`, `tool-calls.json`, and `evals.json`.

Generated runs stay ignored. A promoted API example lives at [`artifacts/example-run/api-suite/summary.json`](artifacts/example-run/api-suite/summary.json) and [`artifacts/example-run/api-suite/http-exchanges.json`](artifacts/example-run/api-suite/http-exchanges.json). An agent example run does not exist.

The manifest records the git commit, SDK version, agent ID, invocation ID, test IDs, repeat count, and verdicts.

## What I tested and what failed

### Tested

With `ELEVENLABS_API_KEY` unset, `uv run verify doctor` exited 0. `elevenlabs_configs` PASSed. `credentials` SKIPped. The PASS line was `1 agent, 3 tools, 1 knowledge source, 6 Simulation tests, 2 Tool Call tests validate against elevenlabs 2.68.0`. Doctor scanned 39 committable files.

An in-process doctor run with a `socket.connect` and `socket.getaddrinfo` audit hook recorded 0 network events. Source does not construct `ElevenLabs` or `AsyncElevenLabs`. `verify agent suite` has no parser.

`uv run ruff format --check`, `uv run ruff check`, and `uv run basedpyright` passed.

Temporary Git copies that excluded `.env`, `.git`, `.venv`, generated artifacts, and caches produced doctor exit 1 for inline `prompt.tools`, singular `success_condition`, `simulate_conversation`, an unknown nested SDK key, a wrong project pin, a remote-looking tool ID, a missing test type, a dangling tool reference, a missing test, a wrong test split, an invalid route-specific mock kind, an extra artifact path, and a missing knowledge file. Those failures named a file and a JSON pointer. They did not print prompt text, knowledge text, or mock bodies.

`uv run verify api suite` exited 0. The earlier `uv run verify api suite --promote` run remains the promoted example.

The promoted run is [`artifacts/example-run/api-suite/summary.json`](artifacts/example-run/api-suite/summary.json) and [`artifacts/example-run/api-suite/http-exchanges.json`](artifacts/example-run/api-suite/http-exchanges.json). That run recorded result `pass`. It recorded 36 case verdicts, all `pass`, and 25 HTTP exchanges. The clock was `2026-09-17T20:45:12Z` in `America/New_York`. The ephemeral port was `56444`. `git_commit` is null. The promoted API artifact predates the current commit.

The suite proved the three business rules on the three fixture policies. POL-1001 proposed, corrected, rejected the superseded commit, applied the newest proposal once, and replayed the same `applied` result with one vehicle total. POL-2002 rejected lookup and proposal with no mutation. POL-3003 rejected `2026-09-16`, accepted `2026-09-17`, then rejected commit after the clock advanced one calendar day, with no mutation.

It also proved unknown policy, invalid VIN, invalid date, unknown proposal, cancelled-status commit recheck, contract example parsing, and health readiness. `parse_api_result` rejected a top-level `ok` field. `GET /openapi.json` returned discriminated lookup, propose, and commit 200 schemas. A commit body with `caller_confirmed` returned 422. A propose body with an extra field returned 422 and created no proposal.

### Not tested yet

`uv run verify agent suite` is not implemented. The eight local tests were not run. No conversation was run. No live webhook path was exercised. `parameter_conditions` path grammar and first-match mock ordering remain unverified. Webhook host substitution is unverified. The dynamic-variable token idea remains unverified. Microphone audio was not tested.
