# Product Acceptance Report: TG Private Relay V1

- message_id: product-acceptance-tg-private-relay-v1
- reply_to_message_id: qa-validation-tg-private-relay-v1-pass-1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- from_agent: product
- to_agent: dev, qa, flow-supervisor
- status: product_accepted
- evidence_level: E3
- release_gate: blocked
- production_verification: pending
- done_status: not_done

## Acceptance Decision

Product accepts the local/service-level implementation for TG Private Relay V1.

This acceptance is scoped to the original Product Design Complete, product model,
dataflow, and QA E3 evidence. It does not mean release gate passed, production
verification completed, or `production_fixed`.

## Product Scope Check

| requirement | product_result | evidence | status |
| --- | --- | --- | --- |
| Telegram personal account user session binding | phone code and 2FA binding behavior covered | QA E3 + product pytest rerun | accepted |
| Max 20 bound TG accounts per SystemUser | 21st account rejected | QA E3 + full pytest rerun | accepted |
| Incoming private message relay to Bot | idempotent incoming relay and Bot push covered | QA E3 + full pytest rerun | accepted |
| Reply via original bound account | ReplyMapping required, owner guard, outgoing idempotency covered | QA E3 + product targeted rerun | accepted |
| Text and normal emoji | service-level text path covered | QA E3 | accepted |
| Image album ordering | out-of-order arrival deferred and flushed as `[1, 2]` | QA E3 + product targeted rerun | accepted |
| Telegram sticker | service boundary covered | QA E3 | accepted |
| primary -> standby failover | failover and event coverage | QA E3 + full pytest rerun | accepted |
| exhausted sessions explicit failure | no fake success; `switched`, `switched`, `exhausted` events covered | QA E3 | accepted |
| disconnected Telegram boundaries | explicit errors instead of fake success | QA E3 | accepted |

## Product Verification Commands

- `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest", "tests/test_private_relay_v1.py::test_incoming_private_message_is_idempotent_and_album_ordered", "tests/test_private_relay_v1.py::test_reply_rejects_cross_user_mapping_without_sending"], timeout=60, check=True)'`: passed, 2 tests.
- `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'`: passed, 9 tests.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

## Remaining Unproven Items

- Real Telegram network authorization is E4-unproven.
- Real Bot update ingestion is E4-unproven.
- Real Telegram user session listener receiving private messages is E4-unproven.
- Real peer receiving text, emoji, image, album, and sticker is E4-unproven.
- Release gate is blocked by missing CI/deploy pipeline, migration workflow,
  production worker/listener runtime, Telegram production config verification,
  and E4 evidence.
- Production verification remains pending.

## Next Route

- next_agent: flow-supervisor
- task_type: release_gate_blocked
- reason: L2 release gate is blocked. Project owner / ops must provide release
  infrastructure, production configuration, migration strategy, worker runtime,
  and E4 verification access before production verification can start.
- release_gate_required: true
- production_verification_required_after_release: true
