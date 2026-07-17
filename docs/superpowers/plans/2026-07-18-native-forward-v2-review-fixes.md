# Native Forward V2 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task and `test-driven-development` for every behavior change. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 V2 原生双向转发的跨账号消息关联、授权身份、终态事务、封批并发和解绑重绑缺陷，且不改变 V1 路径。

**Architecture:** 每个 bridge item 保存其 Bot 私聊的 sender scope 和第一跳 RPC 返回的预期 message id。Bot 端只允许预期 id 归入当前批次；其它转发写 quarantine。授权完成时原子持久化真实 Telegram identity，删除时在同一 identity lock 下释放它；收集和封批使用同一 account lock。

**Tech Stack:** Python 3.11、SQLAlchemy、Alembic、Telethon 1.x、pytest、PostgreSQL。

---

## Root-cause record

- `NativeForwardItem.bridge_message_id` 和 repository lookup 全局唯一，但 Telegram message id 只在 Bot 私聊内唯一。
- 第一跳 adapter 已返回 `bridge_message_ids`，调用方只校验数量，未持久化；Bot 只能按当前 batch 的“下一条”猜测归属。
- `get_me().id` 在 authenticator 中被丢弃，授权事务无法填充 `BoundTgAccount.telegram_user_id`，而 V2 listener 会先执行 identity gate。
- `claim_final()` 的 failed 状态由 `_prepare_final()` 的早退丢弃，且 `dispatch_due()` 没有 collector 的 account lock。
- 软删除保留 `telegram_user_id`，违反“删除后先解除原绑定再重绑”的产品流程。

## File map

- `src/tg_v_chat/storage/models.py`：bridge item 的 sender scope 与 expected id 约束。
- `migrations/versions/0011_scope_native_forward_bridge_items.py`：保留 0010 不变，升级既有 V2 数据和约束。
- `src/tg_v_chat/storage/repositories/native_forward.py`：按 expected id 查找/写入 bridge item。
- `src/tg_v_chat/services/native_forward.py`：保存第一跳 id，并以 account lock 包裹封批。
- `src/tg_v_chat/telegram/telethon_clients/forward_bridge.py`：隔离不属于本批的 forwarded update，并提交终态。
- `src/tg_v_chat/services/auth.py`、`src/tg_v_chat/telegram/telethon_clients/authenticator.py`：授权时传递及持久化真实 identity。
- `src/tg_v_chat/storage/repositories/accounts.py`：删除时释放 identity。
- `tests/test_bot_native_forward_bridge.py`、`tests/test_native_forward_collector.py`、`tests/test_native_forward_schema.py`、`tests/test_native_forward_identity.py`、`tests/test_account_actions.py`：回归合同。
- `docs/product/native-forward-relay-v2.md`、`docs/index/dataflow.md`、`docs/index/project-structure.md`、`docs/qa/validation-plan.md`：产品、数据流、迁移和 E3/E4 口径。

### Task 1: Capture bridge sender scope and exact first-hop IDs

**Files:**
- Modify: `tests/test_native_forward_schema.py`
- Modify: `tests/test_bot_native_forward_bridge.py`
- Modify: `tests/test_native_forward_collector.py`
- Modify: `src/tg_v_chat/storage/models.py`
- Modify: `src/tg_v_chat/storage/repositories/native_forward.py`
- Modify: `src/tg_v_chat/services/native_forward.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/forward_bridge.py`
- Create: `migrations/versions/0011_scope_native_forward_bridge_items.py`

- [ ] **Step 1: Write RED regression tests**

```python
def test_same_bridge_message_id_in_two_bot_private_chats_stays_scoped(factory):
    first = _awaiting_batch(factory, sender_id=7001, token="first")
    second = _awaiting_batch(factory, sender_id=7002, token="second")
    _record_first_hop(factory, first.id, (601,))
    _record_first_hop(factory, second.id, (601,))

    _append_bridge(factory, 7001, 601)
    _append_bridge(factory, 7002, 601)

    assert _item(factory, first.id).bridge_message_id == 601
    assert _item(factory, second.id).bridge_message_id == 601
```

```python
def test_unexpected_forwarded_item_is_quarantined_not_attached_to_active_batch(factory):
    batch = _awaiting_batch(factory, sender_id=7001, token="expected")
    _record_first_hop(factory, batch.id, (601,))

    consumed = asyncio.run(NativeForwardBridgeHandler(factory).handle_message(_event(602, forwarded=True)))

    assert consumed is True
    assert _item(factory, batch.id).bridge_message_id is None
    assert _quarantine(factory).failure_code == "bridge_item_mismatch"
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_native_forward_schema.py tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py -q`

Expected: FAIL because item records do not contain `expected_bridge_message_id`, 601 conflicts globally, and an unmatched update is appended by queue position.

- [ ] **Step 3: Add persistent sender scope and expected id**

Add nullable item columns and named composite constraints:

```python
bridge_sender_telegram_user_id = Column(BigInteger, nullable=True)
expected_bridge_message_id = Column(Integer, nullable=True)

UniqueConstraint(
    "bridge_sender_telegram_user_id", "bridge_message_id",
    name="uq_native_forward_item_bridge_message",
)
UniqueConstraint(
    "bridge_sender_telegram_user_id", "expected_bridge_message_id",
    name="uq_native_forward_item_expected_bridge_message",
)
```

Migration 0011 drops the old global bridge constraint, adds both columns, backfills the sender from `native_forward_batches` for existing bridged rows, sets expected id equal to existing actual id, creates the composite constraints, and clears identities from already deleted accounts. It must downgrade in reverse order.

- [ ] **Step 4: Make first-hop persistence and bridge matching exact**

Change repository contract to accept ordered ids and write them to the batch items:

```python
def record_first_hop_result(self, batch_id: int, *, marker_message_id: int,
                            bridge_message_ids: tuple[int, ...]) -> NativeForwardBatchModel:
    batch = self._batch_for_update(batch_id)
    items = self.list_items(batch.id)
    if len(items) != len(bridge_message_ids):
        raise ValueError("native forward bridge item count mismatch")
    for item, bridge_message_id in zip(items, bridge_message_ids):
        item.bridge_sender_telegram_user_id = batch.bridge_sender_telegram_user_id
        item.expected_bridge_message_id = bridge_message_id
```

`append_bridge_message()` must look up `(sender_id, expected_bridge_message_id)`, ensure its batch is the active sender batch, then set `bridge_message_id`. It must raise a dedicated mismatch error rather than select `_next_bridge_item()`. The handler converts this error to `bridge_item_mismatch` quarantine without creating a reply mapping.

- [ ] **Step 5: Persist results before releasing the identity lock**

Pass the actual tuple from `_forward_claimed_batch()` into `record_first_hop_result()` after verifying both count and uniqueness. Preserve the existing `DeliveryUncertain` behavior for malformed RPC results; do not attach an unmatched forwarded message as a fallback.

- [ ] **Step 6: Run GREEN**

Run: `python3 -m pytest tests/test_native_forward_schema.py tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py -q`

Expected: scoped collisions and mismatch quarantine pass, while existing V2 batch and timeout behavior remains green.

### Task 2: Persist authorization identity and free it on deletion

**Files:**
- Modify: `tests/account_management_helpers.py`
- Modify: `tests/test_native_forward_identity.py`
- Modify: `tests/test_account_actions.py`
- Modify: `tests/test_private_relay_v1.py`
- Modify: `tests/test_account_slot_authorization.py`
- Modify: `src/tg_v_chat/services/auth.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/authenticator.py`
- Modify: `src/tg_v_chat/storage/repositories/accounts.py`

- [ ] **Step 1: Write RED authorization and rebinding tests**

```python
def test_authorization_persists_authenticated_telegram_identity(bot_parts):
    router, _authenticator, _commands, factory = bot_parts
    _complete_primary_bind(router)

    with UnitOfWork(factory) as uow:
        assert uow.accounts.get(1).telegram_user_id == 7001
```

```python
def test_deleted_account_releases_identity_for_new_binding(factory):
    old = _authorized_account(factory, identity=7001)
    _delete_account(factory, old.id)
    replacement = _authorized_account(factory, identity=7001)

    assert replacement.id != old.id
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_native_forward_identity.py tests/test_account_actions.py tests/test_private_relay_v1.py tests/test_account_slot_authorization.py -q`

Expected: authorization leaves identity NULL and reusing 7001 hits the unique constraint.

- [ ] **Step 3: Carry identity through the authenticator**

Add `telegram_user_id: int` to `AuthenticatedSession`; require a positive `me.id` in `_authenticated_session()`:

```python
return AuthenticatedSession(
    session_string=client.session.save(),
    telegram_user_id=_telegram_user_id(me),
    display_name=_display_name(me),
    username=getattr(me, "username", None),
)
```

Update all test authenticators to provide their stable test identity. In `_complete_authorization()`, acquire the new identity lock, call `update_telegram_identity()`, update the profile and session, then commit one transaction. Translate a unique conflict into the explicit `bound_account_already_bound` authorization failure; never overwrite another account.

- [ ] **Step 4: Release identity in the existing delete lock**

Make `AccountRepository.mark_deleted()` clear `telegram_user_id` before flush:

```python
account.status = "deleted"
account.telegram_user_id = None
self._session.flush()
```

The existing account-management delete path already holds the old identity lock, terminates active V2 batches, and invalidates mappings, so it remains the sole release boundary.

- [ ] **Step 5: Run GREEN**

Run: `python3 -m pytest tests/test_native_forward_identity.py tests/test_account_actions.py tests/test_private_relay_v1.py tests/test_account_slot_authorization.py -q`

Expected: newly authorized accounts satisfy V2 readiness and deleted identities can be bound again; a still-live duplicate identity is rejected explicitly.

### Task 3: Preserve terminal failure and serialize sealing

**Files:**
- Modify: `tests/test_bot_native_forward_bridge.py`
- Modify: `tests/test_native_forward_collector.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/forward_bridge.py`
- Modify: `src/tg_v_chat/services/native_forward.py`

- [ ] **Step 1: Write RED state and lock-boundary tests**

```python
def test_prepare_final_commits_account_unavailable_failure(factory):
    batch = _fully_bridged_batch(factory)
    _mark_account_reauth_required(factory, batch.bound_tg_account_id)

    assert NativeForwardBridgeHandler(factory)._prepare_final(batch.marker_token) is None
    assert _batch(factory, batch.id).status == "failed"
```

```python
def test_dispatch_due_uses_same_account_lock_as_collector(factory, monkeypatch):
    dispatcher = NativeForwardDispatchService(factory, _forwarder(), bridge_timeout_seconds=30)
    entered = []
    monkeypatch.setattr(dispatcher._account_locks, "acquire", _recording_lock(entered))

    dispatcher.dispatch_due(account_id=1)

    assert entered == [1]
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py -q`

Expected: failed state is rolled back and dispatch service lacks `_account_locks`.

- [ ] **Step 3: Implement minimal transaction and lock fixes**

Commit the UoW before the `_prepare_final()` early return:

```python
batch = uow.native_forwards.claim_final(marker_token)
if batch is None:
    uow.commit()
    return None
```

Initialize `self._account_locks = UnitOfWork(session_factory).account_locks`. Acquire it around the `seal_due()` plus sealed-id snapshot in `dispatch_due()`, then release it before potentially slow first-hop RPC dispatch.

- [ ] **Step 4: Run GREEN**

Run: `python3 -m pytest tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py -q`

Expected: account-unavailable remains failed and the seal snapshot uses the collector lock domain.

### Task 4: Synchronize contracts and validate the full change

**Files:**
- Modify: `docs/product/native-forward-relay-v2.md`
- Modify: `docs/index/dataflow.md`
- Modify: `docs/index/project-structure.md`
- Modify: `docs/qa/validation-plan.md`
- Modify: `tests/test_document_contracts.py`

- [ ] **Step 1: Update product and QA wording before final verification**

Document that Bot message ids are scoped by sender/private chat; first-hop returned ids are persisted and matched exactly; unexpected forwarded messages quarantine; authorization commits identity before `active`; deleting a binding releases identity only after terminalizing in-flight batches; collection and sealing share the account lock. Add E3 regression cases and retain E4 as unproven.

- [ ] **Step 2: Update document contracts and run docs tests**

Run: `python3 -m pytest tests/test_document_contracts.py -q`

Expected: contract tests prove all new product guarantees are present.

- [ ] **Step 3: Run complete local verification**

Run:

```bash
python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'
python3 -m compileall -q src tests migrations
TG_V_CHAT_DATABASE_URL='postgresql+psycopg://app_user:secret@localhost:5432/tg_v_chat' python3 -m alembic upgrade head --sql
docker compose --env-file .env.example -f docker-compose.server.yml config
git diff --check HEAD
```

Expected: tests, compile, migration SQL, compose config, and whitespace check succeed. If no real PostgreSQL test URL is configured, report integration and E4 separately as unproven.

## Worktree rule

This is a shared dirty `release` worktree containing the user’s V2 implementation. Do not create a separate worktree, reset, stage, commit, or delete unrelated changes while executing this repair.
