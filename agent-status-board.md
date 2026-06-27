# Agent Status Board

| message_id | intake_id | batch_id | bug_id | level | lane | owner_agent | current_agent | status | evidence_level | ready_status | done_status | next_agent | handoff_required | handoff_delivery_status | target_thread | handoff_message_id | ack_deadline | retry_count | supervisor_action | locked_paths | depends_on | sla_deadline | release_gate | last_update | blocking_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| init-prod-diagnosis-2026-06-28 |  |  |  |  | init | flow-supervisor | prod-diagnosis | acknowledged | E0 | ready | acknowledged |  | false | not_required | 019f0a3a-4dbd-7c41-a4df-976cda0bfebf |  |  | 0 | thread_created_and_acknowledged | docs/worklogs/prod-diagnosis.md |  |  | not_required | 2026-06-28 |  |
| init-product-2026-06-28 |  |  |  |  | init | flow-supervisor | product | acknowledged | E0 | ready | acknowledged |  | false | not_required | 019f0a3a-923a-7690-9052-472aeaf618a6 |  |  | 0 | thread_created_and_acknowledged | docs/worklogs/product.md |  |  | not_required | 2026-06-28 |  |
| init-dev-2026-06-28 |  |  |  |  | init | flow-supervisor | dev | acknowledged | E0 | ready | acknowledged |  | false | not_required | 019f0a3a-bc28-7480-a612-28fa45d81fe6 |  |  | 0 | thread_created_and_acknowledged | docs/worklogs/dev.md |  |  | not_required | 2026-06-28 |  |
| init-qa-2026-06-28 |  |  |  |  | init | flow-supervisor | qa | acknowledged | E0 | ready | acknowledged |  | false | not_required | 019f0a3a-e90e-75e1-9d6e-bfbb104e22f2 |  |  | 0 | thread_created_and_acknowledged | docs/worklogs/qa.md |  |  | not_required | 2026-06-28 |  |
| init-flow-supervisor-2026-06-28 |  |  |  |  | init | flow-supervisor | flow-supervisor | acknowledged | E0 | ready | acknowledged |  | false | not_required | 019f0a3b-10ce-7ef2-a04a-7c083c463d09 |  |  | 0 | thread_created_and_acknowledged | docs/worklogs/flow-supervisor.md |  |  | not_required | 2026-06-28 |  |

## Board Rules

- Any non-empty `next_agent` requires `handoff_required=true`.
- `handoff_delivery_status=pending` or `timeout` cannot be treated as done.
- `blocked` must include `blocking_reason`.
- `release_gate=passed` requires recorded QA/product evidence, and production evidence for L3.
