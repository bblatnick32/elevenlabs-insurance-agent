# Look up a policy

Lookup is the first servicing step. The caller gives a policy number. The agent asks the API whether that policy exists and may be changed. No vehicle is added.

## Sub-features

- `lookup-active` finds `POL-1001` or `POL-3003` and returns `kind` `policy_found`.
- `lookup-cancelled` finds `POL-2002` and returns `kind` `rejected` with `code` `policy_cancelled`.
- `lookup-unknown` rejects a number that is not in the fixture store and asks the caller again.

## How to get to it (user POV)

- On a live call, answer the greeting `Thanks for calling policy servicing. What is your policy number?` with the policy number.
- The agent must call `lookup_policy` before it proposes or commits.
- An agent verifying a change runs the HTTP lookup the tool uses.

## Driving it with verify

Preconditions:

- `uv run verify doctor` ends with `pass`.
- Conversation proof also requires doctor to print `ELEVENLABS_API_KEY present`.

- **Active policy HTTP.** Run `uv run verify api suite`. Exit 0. `artifacts/api-suite/<run_id>/summary.json` has `"result": "pass"`. Cases `pol_1001_lookup` and `healthz` are `pass`. `http-exchanges.json` includes `GET /v1/policies/POL-1001` with `"kind": "policy_found"`, `"vehicles": []`, and `"earliest_effective_date": "2026-09-17"`.
- **Cancelled policy HTTP.** In the same summary, case `pol_2002_lookup` is `pass`. The matching exchange is `GET /v1/policies/POL-2002` with `"kind": "rejected"` and `"code": "policy_cancelled"`. Case `pol_2002_no_mutation` is `pass`.
- **Unknown policy HTTP.** Case `unknown_policy_lookup` is `pass`. The exchange is `GET /v1/policies/POL-9999` with `"code": "policy_not_found"` and `"recovery": "ask_caller_again"`.
- **Runner mapping.** Run `uv run python -m unittest discover -s tests`. Exit 0. `test_normal_conversation_has_no_behavioral_verdict` prints `tool  > lookup_policy GET /v1/policies/POL-1001 200 policy_found` and does not print `PASS` or `FAIL`.
- **Cancelled policy speech.** Run `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"`. Exit 0. Test `04 cancelled policy escalates without mutating` is `passed`. Its remote transcript shows spoken escalation, and its tool calls omit `propose_vehicle_addition` and `commit_vehicle_addition`.
- **Unknown policy speech.** In the same invocation, test `06 unknown policy is re-asked once then escalated` is `passed`. The agent re-asks once, then escalates, and never offers `POL-1001`.
- **Proof.** Keep the API `summary.json` and `http-exchanges.json`. For speech, keep the native invocation ID and named test statuses; inspect the remote transcripts when reporting tool absence or exact language.

## Gotchas

- `uv run agent` exit 0 after the caller hangs up is not proof of lookup.
- A cancelled lookup is HTTP 200 with `kind` `rejected`. Do not treat 200 as success.
- After `policy_cancelled`, do not propose or commit. `04-cancelled-policy` fails if those tools appear.
- The API suite clock is `2026-09-17`. `earliest_effective_date` on an active lookup is that New York date, not today on your machine.
- Do not substitute the API suite for native tests `04` or `06`. Those judge spoken recovery.
