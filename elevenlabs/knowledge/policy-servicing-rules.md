# Policy servicing rules

## Result kinds

The API returns exactly one `kind` on each tool result.

- `policy_found` is a successful lookup. It is not a change.
- `confirmation_ready` is a successful proposal. It is not a change. It includes `proposal_id`, the exact `change`, and a server-authored `readback`.
- `applied` is the only result that means a change happened.
- `rejected` means no change happened. Read `recovery` for the next step.

## Recovery

- `ask_caller_again` means ask the caller for that field once more. Do not invent a replacement. If the caller cannot give a different value and the tool rejects the field a second time, do not ask a third time. Escalate.
- `escalate` means stop changing the policy, state that no change was applied, and say that you are escalating the case to a policy servicing representative. That spoken handoff is the escalation. Do not claim a live transfer.

The API derives `recovery` from `code`. Do not pick a different recovery.

`policy_not_found`, `invalid_vin`, `invalid_effective_date`, and `effective_date_in_past` use `ask_caller_again`.
`policy_cancelled`, `proposal_not_found`, `proposal_superseded`, and `vehicle_already_on_policy` use `escalate`.

## Cancelled policy

A cancelled policy lookup returns `rejected` with `code` `policy_cancelled`. Do not propose or commit.

## Date boundary

An effective date is `YYYY-MM-DD`. The API rejects any date earlier than its calendar date in America/New_York. Relay the earliest date the rejection names. Do not choose a date for the caller.

## VIN shape

A VIN is exactly 17 characters. The letters I, O, and Q never appear. Do not pad, repair, or complete a shorter or garbled VIN.
