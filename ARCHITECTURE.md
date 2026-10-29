# Architecture

This project keeps the implementation deliberately small and deterministic.

## Ownership and state
- Each viewer has a durable state record under `viewer_state/<user_id>.json`.
- State includes the latest profile snapshot, event-derived facts, evidence conflicts, decision history, and action history.
- The decision loop is viewer-scoped: one active goal, one published action or explicit no-action, no multi-goal fan-out.

## Planning
- The agent evaluates goals in order from `config/goals.json`.
- It applies evidence freshness and precedence rules before choosing the first eligible goal.
- Corrected goal rules are recorded as applied-at timestamps so future decisions use the new rule without silently rewriting historical decisions.

## Memory and evidence
- Events are deduplicated by `event_id` and processed by `received_at`.
- Profile rows are selected using the latest `updated_at` row per viewer.
- Conflicts are retained in the decision record instead of silently overwritten.

## Verification
- Before publishing, the agent independently validates catalogue availability, dialect, holdout isolation, consent, quiet-hours, and other policy checks.
- Generated catalogue text is never treated as an instruction; content is treated as data only.

## Runtime behaviour
- Actions are processed in replay order with late and duplicate triggers filtered deterministically.
- A model or dependency failure results in no action or a reduced safe action, never a guessed success.
- The decision latency budget is measured per decision and remains well under 60 seconds in the supplied replay.

## Trust boundaries
- The simulator is the outcome oracle for the provided pack.
- G10 and G11 are marked unsupported; no invented semantics are accepted as evidence.
