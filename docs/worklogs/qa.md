# QA Worklog

## 2026-06-28

- message_id: init-qa-2026-06-28
- action: 初始化验收 Agent 工作日志
- input: 多 Agent 协作协议初始化
- output: worklog created
- evidence: docs/qa/validation-plan.md
- decision: 等待 dev Development Complete 或 product 验收请求
- next_agent:
- unresolved:

## Validation Report: tg-private-relay-v1

- message_id: qa-validation-tg-private-relay-v1-failed-1
- reply_to_message_id: dev-complete-tg-private-relay-v1-update-1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- validator: qa
- status: failed
- evidence_level: E3
- handoff_quality: complete
- action: 独立验收 Development Complete 与 update-1
- verification:
  - `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest", "tests/test_private_relay_v1.py::test_reply_fails_when_all_sessions_are_unavailable"], timeout=60, check=True)'` passed: 1 test
  - `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'` passed: 8 tests
  - `python3 -m compileall -q src tests` passed
  - AST metrics scan passed
- passed:
  - phone code / 2FA binding service behavior has E3 coverage.
  - 21st account rejection has E3 coverage.
  - incoming idempotency has E3 coverage.
  - missing reply / missing ReplyMapping explicit failures have E3 coverage.
  - outgoing idempotency has E3 coverage.
  - primary -> standby_1 failover has E3 coverage.
  - all sessions exhausted now records `switched`, `switched`, `exhausted` and raises explicit `SessionFailure`.
  - disconnected Telethon bot/sender boundaries raise explicit errors instead of fake success.
- failed:
  - `failed-qa-001`: `PrivateRelayService.handle_bot_reply` resolves `ReplyMapping` by `reply_to_message_id` but does not verify that `OutgoingReply.system_user_id` owns the original `BotPushMessage` / `ReplyMapping`. A different system user can reply to another user's bot push id and send through that user's bound account if the bot message id is known or leaked. Evidence script reproduced `cross_user_reply_allowed 42 [(1, 88, 2)]`.
- unproven:
  - Product acceptance items requiring real Telegram network/Bot/user session evidence remain E4-unproven: original peer receives text/emoji/image/sticker, real listener receives private messages, real Bot push/reply update parsing, and production release gate.
  - Image album push order is not proven by current implementation; service pushes messages in arrival order and only provides sorted stored relay ids through `ordered_media_group`. Evidence script showed `bot_push_sequence_order [2, 1]` when album events arrive out of order.
- next_agent: dev
- handoff_required: true
- handoff_delivery_status: sent
- target_thread_id: 019f0a3a-bc28-7480-a612-28fa45d81fe6
- handoff_message_id: unavailable_from_tool_response
- requested_fix:
  - Fix `failed-qa-001` without expanding scope.
  - Add regression coverage proving a system user cannot relay using another system user's `ReplyMapping`.
  - Clarify or fix album push ordering so QA can validate product requirement without relying only on post-hoc sorted storage.

## Validation Report: tg-private-relay-v1 Recheck

- message_id: qa-validation-tg-private-relay-v1-pass-1
- reply_to_message_id: dev-fix-tg-private-relay-v1-qa-failed-1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- validator: qa
- status: qa_pass
- evidence_level: E3
- handoff_quality: complete
- action: 复验 QA failed / unproven 修复项
- verification:
  - `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest", "tests/test_private_relay_v1.py::test_incoming_private_message_is_idempotent_and_album_ordered", "tests/test_private_relay_v1.py::test_reply_rejects_cross_user_mapping_without_sending"], timeout=60, check=True)'` passed: 2 tests
  - `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'` passed: 9 tests
  - `python3 -m compileall -q src tests` passed
  - `git diff --check` passed
  - AST metrics scan passed
  - Independent QA script confirmed `cross_user_rejected`, `sender_calls []`, and `bot_push_sequence_order [1, 2]`
- passed:
  - `failed-qa-001` fixed: cross-user ReplyMapping use is rejected before sender invocation.
  - outgoing reply idempotency owner guard is covered for reply id collision.
  - album sequence out-of-order arrival is deferred and flushed in sequence order.
  - previous exhausted failover event update remains covered by full suite.
- unproven:
  - Real Telegram network authorization, real Bot updates, real user session listener, real peer receiving text/emoji/image/sticker, release gate, and production verification remain E4-unproven.
- next_agent: product
- handoff_required: true
- handoff_delivery_status: sent
- target_thread_id: 019f0a3a-923a-7690-9052-472aeaf618a6
- handoff_message_id: unavailable_from_tool_response
- product_acceptance_request:
  - Product Agent should perform Product Acceptance against original scope, dataflow, and remaining E4/release gate requirements.

## Product Acceptance Receipt: tg-private-relay-v1

- message_id: qa-receipt-product-acceptance-tg-private-relay-v1
- reply_to_message_id: product-acceptance-tg-private-relay-v1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- validator: qa
- status: acknowledged
- product_status: product_accepted
- evidence_level: E3
- release_gate: pending
- production_verification: pending
- done_status: not_done
- evidence:
  - `docs/product/product-acceptance-tg-private-relay-v1.md` exists and records `status: product_accepted`.
  - `agent-status-board.md` records product acceptance and next route to dev release gate.
- decision:
  - QA acknowledges Product Acceptance receipt.
  - QA does not mark final done because L2 release gate and E4 production verification remain pending/unproven.
- next_agent: dev
- handoff_status_observed: product already sent `product-release-gate-request-tg-private-relay-v1` to dev.
