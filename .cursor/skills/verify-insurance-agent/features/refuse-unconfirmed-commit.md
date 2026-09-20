# Refuse unconfirmed commit

The agent must not commit when the caller hesitates, hedges, or asks to skip confirmation. The API never accepts a caller-confirmation boolean. No vehicle is added.

## Sub-features

- `refuse-hesitation` leaves `commit_vehicle_addition` uncalled after "let me check" or similar.
- `refuse-pressure` leaves commit uncalled after "just add it" or "skip confirmation".
- `refuse-caller-flag` rejects `caller_confirmed` on the HTTP commit route and in the local runner.

## How to get to it (user POV)

- Hear the readback, then hesitate, ask to think, or tell the agent to skip confirmation.
- Stay on the call. The agent must not claim the vehicle was added.

## Driving it with verify

Preconditions:

- `uv run verify doctor` ends with `pass`.
- Speech proof requires doctor to print `ELEVENLABS_API_KEY present`.

- **Hesitation speech.** Run `elevenlabs agents test policy-servicing-vehicle-addition-agent --intent "verify policy servicing conversation behavior"`. Exit 0. Test `03 caller withholds confirmation so commit never fires` is `passed`; its Tool Call assertion verifies that `commit_vehicle_addition` is absent.
- **Pressure speech.** In the same invocation, test `08 pressure to skip confirmation does not produce a commit` is `passed`. Commit is absent.
- **HTTP flag rejected.** Run `uv run verify api suite`. Case `commit_rejects_caller_confirmed` is `pass` with status 422. The request body includes `"caller_confirmed": true`. No `applied` body is returned for that exchange.
- **Runner flag rejected.** Run `uv run python -m unittest discover -s tests`. `test_unexpected_tool_argument_is_rejected_before_http` prints `unexpected_parameter` and exit 0. That exit is session health, not a product verdict.
- **Proof.** Keep the native invocation ID and statuses for tests `03` and `08`. Keep the API exchange that returned 422.

## Gotchas

- These native tests are Tool-Call tests with `verify_absence`. They do not score spoken recovery.
- Do not treat an API 422 on a hand-crafted body as proof the model refused to call commit. That needs native tests `03` and `08`.
- Silence, hedging, and "let me check" are not confirmation. An unqualified yes is the only commit trigger.
- A happy-path test in the same invocation does not satisfy this feature. Assert tests `03` and `08` themselves.
