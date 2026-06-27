# Project Agent Rules

## Runtime

This project runs in Codex native. Codex is both planner and executor.

- Do not call stale `mcp__codex__codex` or `mcp__codex__codex_reply` guidance.
- Search, read, edit, and test directly in this workspace.
- Use Chinese for user-facing summaries unless the user asks otherwise.

## Multi-Agent Protocol

Treat `/Users/xida/codexProject/codexwork/Codex多Agent协作机制与项目落地手册.md` as the source protocol for this project.

Core agents:

- `prod-diagnosis`: online diagnosis and production verification.
- `product`: intake, triage, PRD, product model, dataflow design, acceptance criteria.
- `dev`: implementation, automated checks, project structure index.
- `qa`: independent validation and regression evidence.
- `flow-supervisor`: handoff delivery, ACK, timeout, retry, and board integrity.

Every input starts as an Intake Card, then becomes a Triage Card, Mini Bug Card, Incident Report, Bug Batch Plan, PRD, or ordinary plan.

## Handoff Rules

- If `next_agent` is not empty, `handoff_required=true` is mandatory.
- A stage is not complete until the handoff is actually sent, or explicitly marked `handoff_delivery_status=blocked`.
- `notify_xxx=true`, `next_agent=xxx`, or "需要通知" does not count as delivery.
- Risky handoffs must include `idempotency_key`, `expected_ack_deadline`, `handoff_quality`, `locked_paths`, and `merge_owner`.
- The receiver must validate the handoff before ACK: `complete`, `missing_inputs`, or `rejected`.
- Important handoff messages must be written to `docs/worklogs/` before sending.

## Evidence And Closure

Evidence levels:

- `E0`: oral or unclear description.
- `E1`: static evidence such as screenshots, snippets, or error text.
- `E2`: local reproduction, unit test, local script, or local screenshot.
- `E3`: CI, integration test, build, or automated validation.
- `E4`: real production evidence.

Done rules:

- `L0`: product accepted with at least `E1`.
- `L1`: QA targeted pass and product accepted.
- `L2`: QA pass, product accepted, and release gate when required.
- `L3`: QA pass, product accepted, and `production_fixed` with `E4`.
- `blocked` and `unproven` are never `done`.

## Debug-First Policy

- Do not add silent fallbacks, fake success paths, or defensive bypasses just to make a task run.
- Surface real failures through explicit errors, logs, or failing tests.
- Do not use mocks as proof unless the user explicitly asks for mock-only validation.

## Quality Baseline

- Keep functions under 50 lines and files under 500 lines.
- Keep nesting depth at 3 or less.
- Prefer dependency injection and immutable data flow.
- Avoid magic numbers; use named constants.
- Validate external input at boundaries.
- Never hardcode secrets.

