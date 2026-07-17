# TG 原生双向转发 V2 Activation And Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task and `test-driven-development` for every behavior change. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在核心 bridge 已实现后，以显式开关启用 V2 入站路径，验证全部媒体和回复合同，并按照 PostgreSQL、GitHub Actions 和真实 Telegram E4 证据发布。

**Architecture:** 本计划依赖 [核心 bridge 实施计划](./2026-07-17-native-forward-relay-plan.md) 的 Tasks 1-4 已完成并通过 E3。运行时仅在 `TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED=true`、`TG_V_CHAT_BOT_USERNAME` 已配置且所有 operational BoundTgAccount 都有唯一 `telegram_user_id` 时选择 V2；否则保留明确的 V1 路径。V2 处理失败不得转回 V1 复制路径。

**Tech Stack:** Python 3.11、Telethon `>=1.34,<2`、SQLAlchemy 2、Alembic、PostgreSQL 16、pytest、GitHub Actions、Docker Compose。

---

## File Map

- `src/tg_v_chat/main.py`、`runtime.py`、`services/relay.py`：显式运行时路由和依赖注入。
- `src/tg_v_chat/telegram/telethon_clients/gateway.py`、`private_listener/formatting.py`：移除已被 V2 替代的入站复制调用，保留出站回复与失败提示。
- `tests/test_native_forward_end_to_end.py`：全链路媒体、回复、未知结果和 `777000` 合同。
- `docs/index/project-structure.md`、产品/数据流/QA 文档：实现后真实模块和证据同步。
- `.github/workflows/deploy-production.yml`、`docs/product/deploy-postgres-release-plan.md`：只在实际环境变量传递或发布路径不同的情况下修改。

### Task 1: Wire The Explicit Runtime Route Without A Per-Message Fallback

**Files:**
- Modify: `src/tg_v_chat/main.py`
- Modify: `src/tg_v_chat/runtime.py`
- Modify: `src/tg_v_chat/services/relay.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/gateway.py`
- Modify: `src/tg_v_chat/telegram/private_listener/formatting.py`
- Modify: `tests/test_private_listener.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `tests/test_listener_reconciliation.py`

- [ ] **Step 1: Write failing route-selection tests**

```python
def test_disabled_flag_uses_existing_v1_inbound_receiver(runtime):
    runtime = runtime.with_native_forward_v2_enabled(False)
    assert runtime.inbound_receiver.__class__ is V1InboundReceiver


def test_enabled_flag_requires_identity_readiness(runtime):
    runtime = runtime.with_native_forward_v2_enabled(True)
    runtime.accounts.return_value = [_operational_account(telegram_user_id=None)]
    with pytest.raises(RuntimeError, match="bound_account_identity_missing"):
        runtime.build_inbound_receiver()
```

Also assert that an enabled V2 `native_forward_restricted`, `bridge_timeout`, or `bridge_transport_unknown` does not invoke `TelethonBotGateway.push_private_message` or `push_private_batch`.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_bootstrap.py tests/test_listener_reconciliation.py tests/test_private_listener.py -q
```

Expected: FAIL because runtime does not select V2 services or perform readiness validation.

- [ ] **Step 3: Implement the one-way route decision**

Use one factory with no per-message fallback:

```python
def build_inbound_receiver(config: AppConfig, uow_factory, dependencies):
    if not config.native_forward_v2_enabled:
        return V1InboundReceiver(dependencies.v1_relay_service)
    if config.bot_username is None:
        raise RuntimeError("bot_username_missing")
    with UnitOfWork(uow_factory) as uow:
        missing = uow.accounts.list_operational_without_telegram_identity()
    if missing:
        raise RuntimeError("bound_account_identity_missing")
    return V2InboundReceiver(
        collector=dependencies.native_forward_collector,
        dispatcher=dependencies.native_forward_dispatcher,
    )
```

Delete only production calls that format and send incoming source content through the old Bot gateway when V2 is selected. Keep outgoing `TelethonReplySender`, session failover, account-management text, and deterministic failure notifications.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m pytest tests/test_bootstrap.py tests/test_listener_reconciliation.py tests/test_private_listener.py tests/test_private_relay_v1.py -q
```

Expected: false is an explicit V1 route, true is a ready V2 route, and V2 failures never copy source content.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/tg_v_chat/main.py src/tg_v_chat/runtime.py src/tg_v_chat/services/relay.py src/tg_v_chat/telegram tests/test_bootstrap.py tests/test_listener_reconciliation.py tests/test_private_listener.py tests/test_private_relay_v1.py
git commit -m "feat: activate native forward ingress by explicit flag"
```

### Task 2: Prove End-To-End Native Forward And Reply Contracts

**Files:**
- Create: `tests/test_native_forward_end_to_end.py`
- Modify: `tests/test_bot_media_push.py`
- Modify: `tests/test_bot_media_reply.py`
- Modify: `tests/test_private_relay_v1.py`

- [ ] **Step 1: Write failing behavior matrix tests**

Create fixtures for two SystemUsers, two unique BoundTgAccounts, two peers, and an injected clock. Cover one text, three normal texts, photo/video mixed album, video_note, audio, voice, sticker, `linked`, `name_only`, protected content, first-hop count mismatch, bridge timeout, and reply to every confirmed final item.

For final DB commit failure, require:

```python
assert [push.status for push in uow.native_forwards.list_pushes_for_batch(batch.id)] == ["uncertain", "uncertain"]
assert uow.mappings.get_by_bot_message(owner.id, visible_bot_message_id) is None
assert gateway.copy_calls == []
assert gateway.send_source_content_calls == []
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_native_forward_end_to_end.py tests/test_bot_media_push.py tests/test_bot_media_reply.py -q
```

Expected: FAIL until runtime wiring invokes the core bridge and its final mapping contract.

- [ ] **Step 3: Complete reply mapping integration**

For results returned in batch sequence order, persist mappings atomically with each `BotPushMessage` state update:

```python
for item, final_message in zip(items, final_messages, strict=True):
    uow.pushes.mark_sent(f"push:{item.relay_message_id}", bot_message_id=final_message.id)
    uow.native_forwards.mark_item_sent(item.id, final_message.id)
    uow.mappings.create(final_message.id, item.relay, system_user_id=batch.system_user_id)
uow.native_forwards.mark_batch_sent(batch.id)
uow.commit()
```

If any update cannot commit after remote success, call the existing explicit `mark_uncertain` transitions in a new transaction and do not create a synthetic mapping.

- [ ] **Step 4: Add the `777000` contract**

Use `peer_id == 777000` and sender name `Telegram`. Assert that the source RelayMessage retains the raw code body and V2 either creates confirmed native mappings or records a persistent Telegram failure code. The test must never accept a copied body as a native-forward result.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m pytest tests/test_native_forward_end_to_end.py tests/test_private_relay_v1.py tests/test_bot_media_push.py tests/test_bot_media_reply.py -q
python3 -m compileall -q src tests
```

Expected: enabled V2 preserves each confirmed item’s reply target; disabled V2 preserves the legacy behavior.

- [ ] **Step 6: Commit Task 2**

```bash
git add tests/test_native_forward_end_to_end.py tests/test_private_relay_v1.py tests/test_bot_media_push.py tests/test_bot_media_reply.py
git commit -m "test: cover native forward activation contracts"
```

### Task 3: Reconcile Truth Sources And E3 Evidence

**Files:**
- Modify: `docs/index/project-structure.md`
- Modify: `docs/product/native-forward-relay-v2.md`
- Modify: `docs/superpowers/specs/2026-07-17-native-forward-relay-design.md`
- Modify: `docs/index/dataflow.md`
- Modify: `docs/product/product-index.md`
- Modify: `docs/qa/validation-plan.md`
- Modify: `tests/test_document_contracts.py`

- [ ] **Step 1: Write failing document-contract assertions**

```python
required = {
    "TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED",
    "telegram_user_id",
    "batch_sequence",
    "bridge_deadline_at",
    "NativeForwardBridgeQuarantine",
    "第二跳前创建 BotPushMessage",
    "telethon>=1.34,<2",
    "777000",
    "E4 unproven",
}
assert required <= set(_all_native_forward_truth_source_terms())
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_document_contracts.py -q
```

Expected: FAIL until real module paths, migration revision, test files, environment values, and QA cases match code.

- [ ] **Step 3: Record only actual implementation evidence**

Update the project structure only with modules that were created, then record fresh E3 command output. Mark `implementation_e3_passed_e4_unproven` only after the local checks pass; retain E4 as unproven until online PostgreSQL, deploy, and real Telegram evidence exist. Documentation edits never count as execution evidence.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m pytest tests/test_document_contracts.py -q
python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'
python3 -m compileall -q src tests
docker compose --env-file .env.example -f docker-compose.server.yml config
git diff --check
```

When `TG_V_CHAT_TEST_DATABASE_URL` is available, also run:

```bash
python3 -m pytest tests/test_postgresql_relay_integration.py -q
```

Expected: local checks pass; missing PostgreSQL credentials remain unproven rather than replaced by SQLite evidence.

- [ ] **Step 5: Commit Task 3**

```bash
git add docs tests/test_document_contracts.py
git commit -m "docs: record native forward activation evidence"
```

### Task 4: Release With A Two-Phase Gate And Collect E4 Evidence

**Files:**
- Modify: `.github/workflows/deploy-production.yml` only if it does not already pass both V2 environment variables.
- Modify: `docs/product/deploy-postgres-release-plan.md` only if the actual release procedure differs below.

- [ ] **Step 1: Check production identity readiness before enabling V2**

Run these PostgreSQL queries before changing the production flag:

```sql
SELECT id, system_user_id, status
FROM bound_tg_accounts
WHERE status IN ('active', 'degraded')
  AND telegram_user_id IS NULL;

SELECT telegram_user_id, COUNT(*)
FROM bound_tg_accounts
WHERE telegram_user_id IS NOT NULL
GROUP BY telegram_user_id
HAVING COUNT(*) > 1;
```

Expected: both queries return zero rows.

- [ ] **Step 2: Release migration and backfill with V2 still false**

Merge to `master`, fast-forward `release`, and deploy through GitHub Actions with:

```text
TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED=false
TG_V_CHAT_BOT_USERNAME=relay_bot
TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS=30
```

Expected: migration, listener identity backfill, CI result, and role health are recorded separately; this is not V2 acceptance.

- [ ] **Step 3: Enable V2 only after zero-row readiness evidence**

Set only this production value, then redeploy through the same `master -> release -> GitHub Actions Deploy Production` path:

```text
TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED=true
```

Do not alter source code or database schema directly on the server.

- [ ] **Step 4: Execute isolated-account E4 cases**

Record real Bot dialogue, source peer, batch id, final message ids, and reply result for linked, name_only, normal three-message batch, photo/video mixed album, video_note, audio, voice, protected failure, bridge timeout/restart uncertainty, duplicate-account rejection, and `777000` official login code. Never include a real login code in the evidence artifact.

- [ ] **Step 5: Report only proven levels**

Report E3 test results, GitHub Actions deployment, and each E4 Telegram case separately. Any absent scenario remains `unproven`; production health alone is insufficient to claim native forward complete.

## Self-Review

- Coverage: explicit routing, no V2 fallback, all media/reply paths, final DB uncertainty, 777000, truth-source synchronization, PostgreSQL pre-enable checks, deployment and E4 each have a dedicated task.
- Type consistency: this plan consumes only `telegram_user_id`, `batch_sequence`, `bridge_deadline_at`, `TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED`, and `TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS` defined by the core plan.
