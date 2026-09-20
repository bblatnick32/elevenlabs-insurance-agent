# Propose a vehicle addition

Propose binds a VIN and effective date to a policy and returns a server-authored readback. It does not add the vehicle. The caller must hear that readback before anyone may commit.

## Sub-features

- `propose-ready` accepts a valid VIN and a date on or after the API calendar date and returns `kind` `confirmation_ready`.
- `propose-readback` returns the exact spoken confirmation string. The agent must say it verbatim.
- `propose-correct` supersedes an earlier pending proposal when the caller changes the date or VIN.
- `propose-extra-field` rejects unknown JSON fields with HTTP 422 and writes no proposal.

## How to get to it (user POV)

- After a successful lookup, give the 17-character VIN and a `YYYY-MM-DD` effective date.
- The agent calls `propose_vehicle_addition` and reads the returned `readback` aloud.
- To change the date or VIN, give the new values. The agent proposes again. Only the latest proposal may be committed.

## Driving it with verify

Preconditions:

- `uv run verify doctor` ends with `pass`.
- Conversation proof also requires doctor to print `ELEVENLABS_API_KEY present`.

- **Happy propose HTTP.** Run `uv run verify api suite`. Exit 0. Case `pol_1001_propose` is `pass`. The exchange is `POST /v1/policies/POL-1001/vehicle-addition-proposals` with `vin` `1HGCM82633A004352` and `effective_date` `2026-09-19`. Response `kind` is `confirmation_ready` and `proposal_id` is `vap_01K5EPJ7R9QY6M8B4T2D3F1H0B`.
- **Corrected propose HTTP.** Case `pol_1001_correct` is `pass`. The second propose uses VIN `1hgcm82633a004352` and date `2026-09-18`. Response matches `contracts/confirmation-ready.json`, including the readback `I will add the vehicle with V I N 1 H G C M 8 2 6 3 3 A 0 0 4 3 5 2 to policy P O L dash 1 0 0 1, effective September 18, 2026. Do you confirm this exact change?` and `proposal_id` `vap_01K5EPJ7R9QY6M8B4T2D3F1H0C`.
- **No extra fields.** Case `propose_rejects_unexpected_field` is `pass` with status 422. Case `propose_unexpected_field_no_proposal` is `pass`.
- **Happy propose speech.** Run `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"`. Test `01 happy path applies exactly once` is `passed`. Its remote transcript contains the server readback verbatim before any commit.
- **Date correction speech.** Test `02 caller corrects the date during readback` is `passed`. The agent proposes a second time with `2026-09-21` and commits only the new `proposal_id`.
- **Proof.** API `http-exchanges.json` shows both propose bodies and both `confirmation_ready` responses. Keep the native invocation ID and named test statuses; inspect the remote transcripts for `01` and `02` to confirm the readback. A later lookup in the same API run still has an empty vehicle list until commit cases run.

## Gotchas

- `confirmation_ready` is not a change. Do not treat propose as success for the caller.
- The agent must not invent, pad, or repair a VIN. That belongs to [Reject bad servicing input](./reject-bad-input.md).
- The first API propose uses date `2026-09-19` so the second propose can supersede it. Assert the latest `proposal_id`.
- Native tests mock tool results. They do not prove the FastAPI store wrote a `PendingProposal`.
- Do not commit in this feature's speech proof unless you are also proving [Commit a confirmed addition](./commit-vehicle-addition.md).
