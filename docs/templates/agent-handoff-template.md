# Agent Handoff Message

- message_id:
- from_agent:
- to_agent:
- related_version:
- intake_id:
- batch_id:
- bug_id:
- level: L0 | L1 | L2 | L3
- evidence_level: E0 | E1 | E2 | E3 | E4
- ready_status: ready | not_ready | blocked
- done_status: not_done | qa_pass | product_accepted | production_fixed | blocked | unproven
- sla_deadline:
- release_gate: not_required | pending | passed | failed | blocked
- cost_tier: single_agent | light_agents | standard_team | full_team
- lane:
- depends_on:
- task_type: triage | batch_plan | incident | plan | implement | quick_fix | verify | fix | recheck | resync | product_acceptance | production_verify | postmortem
- priority: P0 | P1 | P2 | P3
- created_at:
- source_thread:
- target_thread:
- expected_ack: true | false
- expected_ack_deadline:
- next_agent:
- handoff_required: true | false
- handoff_delivery_status: not_required | pending | sent | acknowledged | timeout | blocked
- target_thread_id:
- handoff_message_id:
- sent_at:
- ack_deadline:
- retry_count:
- requires_orchestrator_send: true | false
- orchestrator_send_reason:
- idempotency_key:
- supersedes_message_id:
- handoff_quality: complete | missing_inputs | rejected
- status: new | acknowledged | in_progress | ready_for_validation | qa_pass | product_accepted | product_rejected | failed | blocked | unproven | production_fixed | production_failed | done

## 背景

## 本次要你做什么

## 输入材料

## 索引沉淀

- product_docs:
- dataflow_index:
- structure_index:
- affected_business_objects:
- affected_pages:
- affected_api_or_worker_flows:
- changed_entrypoints:
- changed_modules:
- changed_data_models:
- index_updates: updated | unchanged | unproven
- index_update_reason:

## 必须遵守的边界

## 锁定范围

- locked_paths:
- merge_owner:

## 完成标准

## 需要回传的内容

## 下游真实投递

- next_agent:
- must_send_thread_message: true | false
- thread_message_sent: true | false
- target_thread:
- target_thread_id:
- handoff_message_id:
- handoff_delivery_status: not_required | pending | sent | acknowledged | timeout | blocked
- ack_expected: true | false
- ack_deadline:
- retry_count:
- requires_orchestrator_send: true | false
- blocked_reason:

