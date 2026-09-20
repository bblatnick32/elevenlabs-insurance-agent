# Commit a confirmed addition

Commit applies the latest pending proposal after the caller gives an unqualified yes. This is the only step that adds a vehicle. Success speech is allowed only after `kind` `applied`.

## Sub-features

- `commit-apply` adds the VIN on the bound date and returns `confirmation_number`.
- `commit-replay` returns the same `applied` body when the same proposal is committed again.
- `commit-superseded` rejects the older proposal after a correction.
- `commit-recheck` rejects commit when the policy is no longer amendable or the date has become past.
- `commit-no-caller-flag` rejects a model-supplied confirmation boolean.

## How to get to it (user POV)

- Hear the exact readback from propose.
- Say an unqualified yes.
- The agent calls `commit_vehicle_addition` with only `proposal_id`.
- Hear the `confirmation_number` only after the tool returns `applied`.

## Driving it with verify

Preconditions:

- `uv run verify doctor` ends with `pass`.
- Conversation proof also requires doctor to print `ELEVENLABS_API_KEY present`.

- **Apply HTTP.** Run `uv run verify api suite`. Exit 0. Case `pol_1001_commit` is `pass`. The exchange is `POST /v1/vehicle-addition-proposals/vap_01K5EPJ7R9QY6M8B4T2D3F1H0C/commit` with no `caller_confirmed` field. Response matches `contracts/applied.json`, including `confirmation_number` `CHG-2026-0917-0031` and `applied_at` `2026-09-17T20:45:12Z`.
- **Replay HTTP.** Case `pol_1001_commit_replay` is `pass` with the same `applied` body.
- **Vehicle persisted.** Case `pol_1001_one_vehicle` is `pass`. `GET /v1/policies/POL-1001` lists one vehicle, VIN `1HGCM82633A004352`, date `2026-09-18`.
- **Superseded commit.** Case `pol_1001_commit_superseded` is `pass` with `"code": "proposal_superseded"`.
- **No confirmation boolean.** Case `commit_rejects_caller_confirmed` is `pass` with status 422.
- **Status recheck.** Cases `commit_recheck_cancelled` and `commit_recheck_no_mutation` are `pass`. After cancel, the policy still has only the first applied vehicle.
- **Clock recheck.** Cases `pol_3003_commit_after_clock` and `pol_3003_no_mutation` are `pass`.
- **Runner guard.** Run `uv run python -m unittest discover -s tests`. `test_unexpected_tool_argument_is_rejected_before_http` prints `tool  > commit_vehicle_addition - - - unexpected_parameter`.
- **Happy commit speech.** Run `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"`. Test `01 happy path applies exactly once` is `passed`. Its remote tool calls include one `commit_vehicle_addition` after a clear yes, and the transcript speaks `CHG-2026-0917-0031`.
- **Corrected commit speech.** Test `02 caller corrects the date during readback` is `passed`. Commit uses only the latest proposal id.
- **Proof.** Keep API `http-exchanges.json` showing commit then lookup. Keep the native invocation ID and named statuses; inspect remote tool calls for tests `01` and `02`.

## Gotchas

- HTTP 200 with `kind` `rejected` means no vehicle was added.
- Native happy-path mocks return `CHG-2026-0917-0031`. That does not prove `insurance_backend` mutated a store.
- The live runner writes no transcript or verdict. A human call can support this feature only through printed `tool  > … applied` lines.
- Duplicate VIN after apply is `vehicle_already_on_policy`. That case is `pol_1001_duplicate_vin` in the same API run.
- Do not prove this feature with native tests `03` or `08`. Those require commit to be absent.
