# Reject bad servicing input

The agent relays structured rejections and does not invent a policy number, VIN, or date. Ask-again codes get one retry. Escalate codes stop the change.

## Sub-features

- `reject-unknown-policy` returns `policy_not_found` and `ask_caller_again`.
- `reject-garbled-vin` returns `invalid_vin` and never pads or repairs the VIN.
- `reject-bad-date-shape` returns `invalid_effective_date` for a non-`YYYY-MM-DD` value.
- `reject-past-date` returns `effective_date_in_past` and names the earliest allowed date.
- `reject-unknown-proposal` returns `proposal_not_found` on commit of a missing id.

## How to get to it (user POV)

- Give a policy number that is not on file.
- Give a VIN that is not 17 valid characters, including spaces or letters I, O, or Q.
- Give a date that is not `YYYY-MM-DD`, or a date before the servicing calendar date.
- Ask the agent to commit a proposal that was never created.

## Driving it with verify

Preconditions:

- `uv run verify doctor` ends with `pass`.
- Speech proof requires doctor to print `ELEVENLABS_API_KEY present`.

- **Unknown policy HTTP.** Run `uv run verify api suite`. Cases `unknown_policy_lookup` and `unknown_policy_propose` are `pass` with `"code": "policy_not_found"`.
- **Garbled VIN HTTP.** Case `invalid_vin` is `pass`. The propose body VIN is `1HGCM 82633A004352`. Response `"recovery"` is `ask_caller_again`.
- **Bad date shape HTTP.** Case `invalid_effective_date` is `pass`. The body date is `2026/09/17`.
- **Past date HTTP.** Case `pol_3003_past_date` is `pass` and matches `contracts/rejected-effective-date-in-past.json`. Case `pol_3003_no_mutation` is `pass` after the later clock-advance commit failure.
- **Unknown proposal HTTP.** Case `unknown_proposal` is `pass` with `"code": "proposal_not_found"` and `"recovery": "escalate"`.
- **Unknown policy speech.** Run `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"`. Test `06 unknown policy is re-asked once then escalated` is `passed`. The agent re-asks once, then escalates, and never offers `POL-1001`.
- **Garbled VIN speech.** Test `07 garbled VIN is re-asked, never repaired` is `passed`. The agent does not repair the VIN and does not commit.
- **Past date speech.** Test `05 backdated date is relayed, never substituted` is `passed`. The agent relays the past-date rejection and does not substitute a new date.
- **Proof.** Keep the API exchanges for each rejection `code`. Keep the native invocation ID and statuses for tests `05`, `06`, and `07`; inspect their remote transcripts when needed.

## Gotchas

- Business rejections are HTTP 200. Assert `kind` and `code`, not status 4xx.
- `ask_caller_again` allows one retry of that field. A second rejection of the same field is escalate. Do not ask a third time.
- The API suite date boundary is New York on `2026-09-17`. A date of `2026-09-16` is past in that run even if it is in the future on your machine.
- Native tests mock the rejection body. They do not prove `parse_vin` or the store clock. The API suite proves those.
- Do not report `05`, `06`, or `07` as verified by HTTP-only cases.
