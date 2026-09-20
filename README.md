# ElevenLabs Insurance Policy Servicing Agent

This repository contains a local insurance policy-servicing API, an ElevenLabs voice agent, and deterministic verification commands. The voice agent adds one vehicle to a synthetic auto insurance policy.

Do not use this project with real policyholder data.

## Ownership

Each part has one job.

- The official ElevenLabs CLI owns the persistent remote agent and its three client tools.
- `src/agent/` runs one local microphone conversation. It does not create, update, judge, or record remote conversations.
- `src/verify/` runs the repository preflight and deterministic API suite.
- `src/insurance_backend/` owns policy-servicing rules, persistence, and HTTP delivery.

The persistent voice agent uses client tools. Each handler calls the in-process API on `127.0.0.1`. Eight persistent ElevenLabs tests mock those client tools directly and are attached to the agent.

## Prerequisites

Install Python 3.14, `uv`, the ElevenLabs CLI, and PortAudio.

```bash
brew install uv
brew install elevenlabs/tap/elevenlabs
brew install portaudio
uv sync --locked
```

Set `ELEVENLABS_API_KEY` in `.env` for a private agent and for native ElevenLabs tests. A public agent can run without an API key.

```dotenv
ELEVENLABS_API_KEY=
```

The Python commands load the repository `.env` without overriding exported values. The ElevenLabs CLI can use `elevenlabs auth login` or an exported API key.

## Run the agent

Run one microphone conversation.

```bash
uv run agent
```

The command resolves the agent ID in this order.

1. `--agent-id`
2. `AGENT_ID` environment variable
3. The sole entry in `agents.json`

Examples:

```bash
uv run agent --agent-id agent_123
AGENT_ID=agent_123 uv run agent
```

The command starts a fresh fixture backend on an ephemeral loopback port. It registers the three local client-tool handlers, opens an ElevenLabs `Conversation`, and prints agent, user, and tool events.

Press Ctrl+C to hang up. A normal end, a remote close, and Ctrl+C exit with status 0. Setup, connection, or session failures exit with status 1. Conversation content never determines the exit status. Asking a question, declining a change, or completing a change are all valid outcomes.

The command writes no transcript, verdict, manifest, policy snapshot, or evidence directory.

## Manage the persistent agent

The repository root is an ElevenLabs CLI project.

```text
agents.json
tools.json
tests.json
agent_configs/
tool_configs/
test_configs/
```

The three registry files contain the current workspace IDs. The eight tests in `test_configs/` are attached to the persistent agent through `platform_settings.testing.attached_tests`.

Validate changes without deploying:

```bash
elevenlabs tools push --dry-run --intent "validate client tool configuration"
elevenlabs tests push --dry-run --intent "validate agent test configuration"
elevenlabs agents push --dry-run --intent "validate the policy servicing agent"
```

Deploy reviewed client-tool, test, and agent changes:

```bash
elevenlabs tools push --intent "update client tool configuration"
elevenlabs tests push --intent "update policy servicing agent tests"
elevenlabs agents push --intent "update the policy servicing agent"
```

The committed agent config is intentionally smaller than a raw `agents pull` response. Review every dry-run before updating the persistent agent. The CLI updates registry IDs and the agent `version_id`; commit those changes with the reviewed configuration.

Update the existing knowledge document separately:

```bash
elevenlabs agents knowledge-base documents update \
  --documentation-id OcA0zjm2Rv1gY8aimy9D \
  --name policy-servicing-rules \
  --content "$(<knowledge/policy-servicing-rules.md)" \
  --intent "update policy servicing rules"
```

Python does not wrap or replace these CLI commands.

### Create objects in another workspace

The checked-in IDs belong to the current ElevenLabs workspace. To create equivalent objects in another workspace:

1. Remove each `id` from `tools.json` and push the tools.
2. Replace the tool IDs in the agent config and every test mock or tool reference.
3. Remove each `id` from `tests.json` and push the tests.
4. Replace the attached test IDs in the agent config.
5. Create the knowledge document and replace its ID in the agent config.
6. Remove `id`, `branch_id`, and `version_id` from `agents.json`.
7. Dry-run, review, and push the agent.

Create the knowledge document with:

```bash
elevenlabs agents knowledge-base documents create_from_text \
  --name policy-servicing-rules \
  --text "$(<knowledge/policy-servicing-rules.md)" \
  --intent "create policy servicing rules"
```

The CLI writes the newly created agent and tool IDs back to the registries.

## Verify the project

Run the repository preflight:

```bash
uv run verify doctor
```

`verify doctor` validates the policy fixtures, API contracts, client-tool schemas, official test registry, test mocks, and agent attachments. It also reports whether `ELEVENLABS_API_KEY` is present; an absent key does not fail the preflight because the local checks do not require it.

Run the local checks:

```bash
uv run python -m unittest discover -s tests
uv run ruff format --check
uv run ruff check
uv run basedpyright
uv run verify api suite
```

Run the credentialed native agent tests:

```bash
elevenlabs agents test policy-servicing-vehicle-addition-agent \
  --intent "verify policy servicing conversation behavior"
```

The command runs the eight tests attached to the persistent agent. Simulation tests use test-specific response mocks for the existing client tools with `fallback_strategy` set to `raise_error`, so they do not start `insurance_backend` or invoke live client handlers. Native tests can consume ElevenLabs credits and require `ELEVENLABS_API_KEY`.

## Call flow

The agent follows this sequence.

1. Ask for a policy number and call `lookup_policy`.
2. Collect the caller's 17-character VIN and effective date.
3. Call `propose_vehicle_addition`.
4. Speak the returned `readback` verbatim.
5. Call `commit_vehicle_addition` only after a clear, unqualified yes.
6. Report success only after the API returns `kind` value `applied`.

The API never accepts a caller-confirmation boolean from the model. The proposal binds the server-authored readback to the exact change. Commit rechecks the policy status and effective date. Repeated commit requests return the same applied result.

## HTTP API

The local API exposes four routes.

- `GET /healthz`
- `GET /v1/policies/{policy_number}`
- `POST /v1/policies/{policy_number}/vehicle-addition-proposals`
- `POST /v1/vehicle-addition-proposals/{proposal_id}/commit`

Business responses form a discriminated JSON union keyed by `kind`.

- `policy_found`
- `confirmation_ready`
- `applied`
- `rejected`

Business rejections return HTTP 200 so the agent receives their structured recovery instructions. Malformed HTTP requests return FastAPI validation errors.

## Configuration files

The persistent agent uses these CLI-owned files:

```text
agents.json
tools.json
tests.json
agent_configs/policy-servicing-vehicle-addition-agent.json
tool_configs/lookup_policy.json
tool_configs/propose_vehicle_addition.json
tool_configs/commit_vehicle_addition.json
test_configs/
knowledge/policy-servicing-rules.md
```

The official test configs reference the persistent client-tool IDs. Simulation mocks are scoped to their test and fail closed when no parameter condition matches. Live conversations still execute those tools locally through `ClientTools`.

## Limits

This project excludes authentication, a database, real insurer integrations, telephony, and a production escalation system. Native Simulation tests do not prove caller identity, legal consent, audio quality, or telephony recognition of a VIN.
