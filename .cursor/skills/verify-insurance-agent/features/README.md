# Insurance agent verification map

This directory is the maintained source for verifying user-facing policy-servicing behavior. Read this index, then use the matching feature file as the recipe.

## Baseline preconditions

- Run `uv sync --locked` once per checkout.
- Run `uv run verify doctor` and require a final stdout line of `pass`.
- API and unittest drives need no ElevenLabs key.
- Conversation drives need `ELEVENLABS_API_KEY` present in doctor output.
- The API harness starts a fresh fixture store. Native tests reuse the persistent agent and attached test IDs with test-scoped client-tool mocks.
- Do not reuse a live `uv run agent` session you did not start.

## Driving conventions

- Start every recipe from doctor `pass` unless the feature says otherwise.
- Treat every command as literal.
- The API suite and attached native tests are atomic. Run the whole command, then assert the named cases or tests.
- Prefer route paths, `kind` values, case names, and test config stems over source function names.
- Restore nothing by hand. API runs reload `fixtures/policies.json`; native tests do not mutate the local store.
- Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the user action and the resulting `kind` or native-test verdict, not only exit 0.
- HTTP proof includes the request, status, `kind` body, and a later lookup or no-mutation case when the feature mutates or refuses mutation.
- Conversation proof includes the CLI invocation ID, the named test status, and its remote transcript or evaluator rationale when needed.
- `uv run agent` exit 0 is not a behavioral verdict.
- Report an unreachable conversation path when doctor shows `ELEVENLABS_API_KEY absent`. Do not claim it passed through the API suite.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with verify` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Look up a policy](./lookup-policy.md) covers active, cancelled, and unknown policy numbers.
- [Propose a vehicle addition](./propose-vehicle-addition.md) covers VIN and date collection, server readback, and date correction.
- [Commit a confirmed addition](./commit-vehicle-addition.md) covers apply, replay, and commit-time rechecks.
- [Refuse unconfirmed commit](./refuse-unconfirmed-commit.md) covers hesitation and pressure to skip confirmation.
- [Reject bad servicing input](./reject-bad-input.md) covers unknown policy, garbled VIN, and past dates.
