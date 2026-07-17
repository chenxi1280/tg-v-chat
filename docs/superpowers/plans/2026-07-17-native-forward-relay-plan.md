# TG 原生双向转发 V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task and `test-driven-development` for every behavior change. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以可恢复、不可串批的两跳 Telegram 原生转发替换 V1 入站复制路径，同时保留现有 Bot 会话、逐条 ReplyMapping、多账号身份和显式失败语义。

**Architecture:** V2 默认关闭。启用前，listener 回填并唯一化 BoundTgAccount 的真实 `telegram_user_id`；collector 为每个 batch 分配独立 `batch_sequence`。第一跳和 Bot bridge 都按该 Telegram identity 加 PostgreSQL advisory lock；第一跳返回实际条数、bridge 有显式 deadline。第二跳在 Telegram RPC 前创建所有 BotPushMessage，确认最终 message id 后才写 ReplyMapping；未知副作用进入 `uncertain`，从不复制或自动重放。

**Tech Stack:** Python 3.11、Telethon `>=1.34,<2`、SQLAlchemy 2、Alembic、PostgreSQL 16、pytest、Docker Compose。

---

## File Map

- `pyproject.toml`：将 Telethon 固定在 1.x。
- `.env.example`、`docker-compose.server.yml`、`src/tg_v_chat/config.py`：V2 显式开关、Bot username target 和 bridge timeout 配置。
- `src/tg_v_chat/domain.py`：不可变 batch、第一跳结果和状态类型。
- `src/tg_v_chat/storage/models.py`、`migrations/versions/0010_native_forward_bridge.py`：Telegram identity、batch/item、deadline、header、bridge quarantine 和约束。
- `src/tg_v_chat/storage/repositories/accounts.py`：回填和唯一化 `telegram_user_id`。
- `src/tg_v_chat/storage/repositories/native_forward.py`：batch/item 原子序号、CAS、到期和 quarantine 查询。
- `src/tg_v_chat/storage/repositories/locks.py`、`unit_of_work.py`：按真实 Telegram identity 的 advisory lock。
- `src/tg_v_chat/services/native_forward.py`：收集、第一跳调度、deadline reconciliation 和第二跳账本编排。
- `src/tg_v_chat/telegram/private_listener/native_forward.py`、`process.py`：已连接 user session 的第一跳和 identity 回填。
- `src/tg_v_chat/telegram/telethon_clients/forward_bridge.py`、`bot_process.py`：桥接先于普通 router 的 marker/item/album 消费。
- `src/tg_v_chat/main.py`、`runtime.py`、`services/relay.py`：显式选择 V1 或 V2 入站路径，保留原出站 reply/failover。
- `tests/test_native_forward_schema.py`、`tests/test_native_forward_collector.py`、`tests/test_user_session_native_forward.py`、`tests/test_bot_native_forward_bridge.py`、`tests/test_private_listener_native_forward.py`、`tests/test_native_forward_end_to_end.py`：V2 合同测试。
- `docs/product/native-forward-relay-v2.md`、`docs/index/dataflow.md`、`docs/product/product-index.md`、`docs/qa/validation-plan.md`：产品、数据流和 QA 真相源。

### Task 1: Add The Explicit V2 Gate And Unique Telegram Identity

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `docker-compose.server.yml`
- Modify: `src/tg_v_chat/config.py`
- Modify: `src/tg_v_chat/storage/models.py`
- Modify: `src/tg_v_chat/storage/repositories/accounts.py`
- Modify: `src/tg_v_chat/telegram/private_listener/process.py`
- Create: `migrations/versions/0010_native_forward_bridge.py`
- Modify: `tests/test_bootstrap.py`
- Create: `tests/test_native_forward_identity.py`

- [ ] **Step 1: Write failing configuration and identity tests**

```python
def test_native_forward_v2_is_disabled_by_default(valid_environment, monkeypatch):
    monkeypatch.delenv("TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED", raising=False)
    config = load_config()
    assert config.native_forward_v2_enabled is False
    assert config.bot_username is None
    assert config.native_forward_bridge_timeout_seconds == 30


def test_account_telegram_identity_is_unique(session_factory):
    first = _account(session_factory, system_user_id=1)
    second = _account(session_factory, system_user_id=2)
    _set_telegram_identity(session_factory, first.id, 7001)
    with pytest.raises(IntegrityError):
        _set_telegram_identity(session_factory, second.id, 7001)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m pytest tests/test_bootstrap.py tests/test_native_forward_identity.py -q
```

Expected: FAIL because V2 config fields, identity column, and its unique constraint do not exist.

- [ ] **Step 3: Add strict configuration and package boundary**

Set the dependency exactly to `telethon>=1.34,<2`. Add these constants and parsers to `src/tg_v_chat/config.py`:

```python
DEFAULT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS = 30


def _optional_bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise RuntimeError(f"environment variable must be true or false: {name}")


def _optional_positive_int_env(name: str, *, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"environment variable must be an integer: {name}") from exc
    if parsed <= 0:
        raise RuntimeError(f"environment variable must be positive: {name}")
    return parsed
```

Append these frozen fields to `AppConfig` and load them through the helpers:

```python
native_forward_v2_enabled: bool = False
bot_username: str | None = None
native_forward_bridge_timeout_seconds: int = DEFAULT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS
```

Set `.env.example` and all service environments in `docker-compose.server.yml` to:

```text
TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED=false
TG_V_CHAT_BOT_USERNAME=
TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS=30
```

- [ ] **Step 4: Add the migration and account repository contract**

Add nullable `BoundTgAccountModel.telegram_user_id = Column(BigInteger, unique=True, nullable=True)`. In migration `0010_native_forward_bridge.py`, add the nullable column and named PostgreSQL unique constraint `uq_bound_tg_account_telegram_user_id`; PostgreSQL allows multiple NULL values, so only real identities are unique.

Add these repository methods:

```python
def update_telegram_identity(self, account_id: int, telegram_user_id: int) -> BoundTgAccountModel:
    account = self.get(account_id)
    account.telegram_user_id = telegram_user_id
    self._session.flush()
    return account


def list_operational_without_telegram_identity(self) -> list[BoundTgAccountModel]:
    return (
        self._session.query(BoundTgAccountModel)
        .filter(BoundTgAccountModel.status.in_(("active", "degraded")))
        .filter(BoundTgAccountModel.telegram_user_id.is_(None))
        .order_by(BoundTgAccountModel.id.asc())
        .all()
    )
```

Translate the unique-constraint `IntegrityError` at the auth/listener boundary into explicit `DeliveryFailure("bound_account_already_bound", ...)`; never pick one owner or overwrite identity silently.

- [ ] **Step 5: Make listener identity sync persist `get_me().id`**

Replace profile-only sync with an immutable profile object and one repository update:

```python
@dataclass(frozen=True)
class TelegramAccountIdentity:
    telegram_user_id: int
    display_name: str | None
    username: str | None


async def _sync_bound_account_identity(client, binding, session_factory):
    identity = _identity_from_user(await client.get_me())
    with UnitOfWork(session_factory) as uow:
        uow.accounts.update_telegram_identity(binding.account_id, identity.telegram_user_id)
        uow.accounts.update_profile(
            binding.account_id,
            display_name=identity.display_name,
            username=identity.username,
        )
        uow.commit()
    return replace(binding, telegram_user_id=identity.telegram_user_id,
                   display_name=identity.display_name, username=identity.username)
```

Extend `BoundListenerSession` with `telegram_user_id: int | None`. A duplicate identity must leave the listener start visibly failed for that account and must not start V2 forwarding for it.

- [ ] **Step 6: Verify GREEN and migration SQL**

Run:

```bash
python3 -m pytest tests/test_bootstrap.py tests/test_native_forward_identity.py tests/test_listener_reconciliation.py -q
TG_V_CHAT_DATABASE_URL=postgresql+psycopg://user:pass@localhost/db alembic upgrade head --sql > /tmp/tg-v-chat-0010.sql
rg -n "telegram_user_id|uq_bound_tg_account_telegram_user_id" /tmp/tg-v-chat-0010.sql
```

Expected: configuration is strict, duplicate real identities are rejected, and PostgreSQL SQL contains the named constraint.

- [ ] **Step 7: Commit Task 1**

```bash
git add pyproject.toml .env.example docker-compose.server.yml src/tg_v_chat/config.py src/tg_v_chat/storage src/tg_v_chat/telegram/private_listener/process.py migrations/versions/0010_native_forward_bridge.py tests/test_bootstrap.py tests/test_native_forward_identity.py tests/test_listener_reconciliation.py
git commit -m "feat: gate native forwarding by unique telegram identity"
```

### Task 2: Persist Native Batches, Independent Order, Deadline, And Durable Push Claims

**Files:**
- Modify: `src/tg_v_chat/domain.py`
- Modify: `src/tg_v_chat/storage/models.py`
- Create: `src/tg_v_chat/storage/repositories/native_forward.py`
- Modify: `src/tg_v_chat/storage/repositories/unit_of_work.py`
- Modify: `migrations/versions/0010_native_forward_bridge.py`
- Create: `tests/test_native_forward_schema.py`
- Modify: `tests/test_postgresql_relay_integration.py`

- [ ] **Step 1: Write failing schema and sequence tests**

```python
def test_append_assigns_batch_sequence_not_relay_sequence(repository, relay_one, relay_two):
    batch = repository.create_collecting(_request(peer_id=88))
    first = repository.append_item(batch.id, relay_one.id)
    second = repository.append_item(batch.id, relay_two.id)
    assert (first.batch_sequence, second.batch_sequence) == (1, 2)


def test_item_push_is_claimed_before_final_rpc(repository, batch):
    repository.claim_final(batch.marker_token)
    assert [push.status for push in repository.list_pushes_for_batch(batch.id)] == ["sending", "sending"]
```

Also assert that `native_forward_batches` contains `bridge_sender_telegram_user_id`, `bridge_deadline_at`, `header_bot_message_id`, that items contain `batch_sequence`, that `native_forward_bridge_quarantines` contains only audit fields, and that `UNIQUE(batch_id, batch_sequence)` exists.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_native_forward_schema.py -q
```

Expected: FAIL because native batch schema and repository do not exist.

- [ ] **Step 3: Define immutable domain contracts**

```python
@dataclass(frozen=True)
class NativeForwardRequest:
    batch_id: int
    marker_token: str
    expected_count: int
    source_peer: TelegramPeer
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True)
class FirstHopForwardResult:
    marker_message_id: int
    bridge_message_ids: tuple[int, ...]


class UserSessionForwarder(Protocol):
    def forward_batch(self, request: NativeForwardRequest) -> FirstHopForwardResult:
        raise NotImplementedError
```

Keep batch statuses `collecting, sealed, bridge_sending, awaiting_bot, final_sending, sent, failed, uncertain` and item statuses `pending, bridged, sent, failed, uncertain`.

- [ ] **Step 4: Implement repositories with transactional order allocation**

Use this public contract; all methods perform state-conditional updates and return whether exactly one row changed where applicable:

```python
create_collecting(system_user_id, account_id, telegram_user_id, peer, collect_until, marker_token)
append_item(batch_id, relay_message_id)
seal(batch_id)
claim_bridge(batch_id, bridge_deadline_at)
mark_first_hop_uncertain(batch_id, code, detail)
mark_awaiting_bot(marker_token, sender_id)
append_bridge_message(sender_id, bridge_message_id, identity_visibility)
claim_final(marker_token)
create_and_claim_final_pushes(batch_id)
list_pushes_for_batch(batch_id)
record_header(marker_token, header_bot_message_id)
record_first_hop_result(batch_id, marker_message_id, bridge_message_ids)
mark_final_sent(marker_token, final_message_ids)
mark_failed(batch_id, code, detail)
mark_uncertain(batch_id, code, detail)
expire_bridge_batches(now)
find_active_bridge(sender_id)
record_quarantine(sender_id, bot_message_id, marker_token, failure_code)
```

`append_item` must execute under a row lock on the batch and compute `max(batch_sequence) + 1`; never accept a `sequence` argument. `create_and_claim_final_pushes` must use the existing `PushRepository.create_pending(relay_id, system_user_id, f"push:{relay_id}")` then `claim` before any final Telegram call.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m pytest tests/test_native_forward_schema.py -q
python3 -m pytest tests/test_postgresql_relay_integration.py -q
```

Expected: SQLite schema tests pass. If `TG_V_CHAT_TEST_DATABASE_URL` is absent, PostgreSQL advisory-lock and uniqueness evidence remains skipped/unproven rather than treated as passed.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/tg_v_chat/domain.py src/tg_v_chat/storage migrations/versions/0010_native_forward_bridge.py tests/test_native_forward_schema.py tests/test_postgresql_relay_integration.py
git commit -m "feat: persist ordered native forward batches"
```

### Task 3: Collect, Seal, Dispatch, And Expire The First Hop

**Files:**
- Create: `src/tg_v_chat/services/native_forward.py`
- Create: `src/tg_v_chat/telegram/private_listener/native_forward.py`
- Modify: `src/tg_v_chat/telegram/private_listener/process.py`
- Modify: `src/tg_v_chat/storage/repositories/locks.py`
- Modify: `src/tg_v_chat/storage/repositories/unit_of_work.py`
- Create: `tests/test_native_forward_collector.py`
- Create: `tests/test_user_session_native_forward.py`
- Modify: `tests/test_listener_reconciliation.py`

- [ ] **Step 1: Write failing collector, adapter, and timeout tests**

```python
def test_three_normal_messages_receive_distinct_batch_sequence(collector, clock):
    first = collector.ingest(_message(source_id=11, sequence=1), now=clock.now())
    second = collector.ingest(_message(source_id=12, sequence=1), now=clock.advance(milliseconds=100))
    third = collector.ingest(_message(source_id=13, sequence=1), now=clock.advance(milliseconds=100))
    assert [item.batch_sequence for item in (first, second, third)] == [1, 2, 3]


def test_first_hop_count_mismatch_is_uncertain(dispatcher, batch):
    dispatcher.forwarder = _Forwarder(FirstHopForwardResult(501, (601,)))
    dispatcher.dispatch(batch.id)
    assert dispatcher.batch(batch.id).failure_code == "bridge_item_count_mismatch"
    assert dispatcher.batch(batch.id).status == "uncertain"


def test_expired_awaiting_bot_becomes_uncertain(reconciliation, batch, clock):
    reconciliation.expire(now=clock.advance(seconds=31))
    assert reconciliation.batch(batch.id).failure_code == "bridge_timeout"
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_native_forward_collector.py tests/test_user_session_native_forward.py tests/test_listener_reconciliation.py -q
```

Expected: FAIL because V2 collector, forwarder result, and deadline reconciliation do not exist.

- [ ] **Step 3: Implement collector and first-hop protocol**

Define named constants only:

```python
NATIVE_FORWARD_QUIET_WINDOW = timedelta(seconds=1)
TELEGRAM_FORWARD_BATCH_LIMIT = 100
```

`NativeForwardCollector.ingest` persists RelayMessage first, then calls `append_item`; `ingest_album` seals immediately and preserves source-id order. `NativeForwardDispatchService.dispatch` must perform this exact terminal split:

```python
result = forwarder.forward_batch(request)
if len(result.bridge_message_ids) != batch.expected_count:
    repository.mark_uncertain(batch.id, "bridge_item_count_mismatch", "第一跳返回条数不一致")
    return
repository.record_first_hop_result(batch.id, result.marker_message_id, result.bridge_message_ids)
```

Before the call, `claim_bridge` computes `now + timedelta(seconds=config.native_forward_bridge_timeout_seconds)`. Acquire a new `TelegramIdentityLock` with `batch.bridge_sender_telegram_user_id`, not `batch.bound_tg_account_id`.

- [ ] **Step 4: Wire only the enabled route**

When `native_forward_v2_enabled` is false, listener uses the existing V1 service. When true, it must call the collector/dispatcher only after `list_operational_without_telegram_identity()` is empty. A non-empty list raises one explicit readiness error naming `bound_account_identity_missing`; it must not send a V1 copy for an affected message.

Update `TelethonUserSessionForwarder` to resolve `TG_V_CHAT_BOT_USERNAME` in its own user session, return actual forwarded destination message ids, send marker first and source ids in ascending batch_sequence order second. A missing username raises `DeliveryFailure("bot_username_missing", ...)`; it must never use `send_message` for source content or reuse the Bot session access hash.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m pytest tests/test_native_forward_collector.py tests/test_user_session_native_forward.py tests/test_listener_reconciliation.py tests/test_private_listener.py -q
```

Expected: normal batch ordering, album ordering, first-hop count checks, expiry, and V1 listener regressions pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/tg_v_chat/services/native_forward.py src/tg_v_chat/telegram/private_listener src/tg_v_chat/storage/repositories tests/test_native_forward_collector.py tests/test_user_session_native_forward.py tests/test_listener_reconciliation.py tests/test_private_listener.py
git commit -m "feat: dispatch deadline-bound native forward bridge"
```

### Task 4: Serialize Bot Bridge Traffic Before The Ordinary Router

**Files:**
- Create: `src/tg_v_chat/telegram/telethon_clients/forward_bridge.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/bot_process.py`
- Modify: `src/tg_v_chat/main.py`
- Modify: `src/tg_v_chat/runtime.py`
- Create: `tests/test_bot_native_forward_bridge.py`
- Modify: `tests/test_native_forward_collector.py`

- [ ] **Step 1: Write failing bridge race and quarantine tests**

```python
async def test_forwarded_item_waits_for_marker_commit(bridge, delayed_repository):
    marker = _marker(sender_id=7001, token="abc123", count=2)
    first_item = _forwarded(sender_id=7001, message_id=501)
    await asyncio.gather(bridge.handle_new_message(marker), bridge.handle_new_message(first_item))
    assert delayed_repository.bridge_ids("abc123") == [501]
    assert bridge.generic_router_calls == []


async def test_orphan_forwarded_message_is_quarantined(bridge):
    await bridge.handle_new_message(_forwarded(sender_id=7001, message_id=501))
    assert bridge.failure_code_for(7001) == "bridge_orphan_forward"
    assert bridge.generic_router_calls == []
```

Also cover an unknown marker from a known bound sender, wrong expected count, marker from another sender, `Album` delivery, and two different unique Telegram identities running concurrently.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py -q
```

Expected: FAIL because the bridge handler and bridge-first routing do not exist.

- [ ] **Step 3: Implement sender-locked bridge routing**

Inject `session_factory` and `ForwardBridgeHandler` into `TelethonBotProcess`. Register in this order:

```python
client.add_event_handler(self._bridge.handle_new_message, events.NewMessage(incoming=True))
client.add_event_handler(self._bridge.handle_album, events.Album())
client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
client.add_event_handler(self._handle_album, events.Album())
client.add_event_handler(self._handle_callback, events.CallbackQuery())
```

For every private marker/forwarded event from a known BoundTgAccount sender, acquire `TelegramIdentityLock.acquire(sender_id)`, perform the repository CAS, then raise `events.StopPropagation`. The marker and item paths must share the same lock key and use `find_active_bridge(sender_id)`; no “most recent batch” query is permitted. Invalid marker/orphan forwarded paths call `record_quarantine(sender_id, event.message.id, marker_token_or_none, failure_code)` and do not persist the event body or media.

- [ ] **Step 4: Implement final preclaim and terminal notification**

The final-forward path must have this order:

```python
repository.claim_final(marker_token)
repository.create_and_claim_final_pushes(batch.id)
header_id = await bot_client.send_message(owner.telegram_user_id, _pending_header(batch))
repository.record_header(marker_token, header_id)
final_messages = await bot_client.forward_messages(owner.telegram_user_id, bridge_ids)
repository.mark_final_sent(marker_token, tuple(message.id for message in final_messages))
```

If `mark_final_sent` cannot commit after Telegram returns, mark all claimed pushes/items and batch `uncertain` with `bridge_transport_unknown`; then edit the stored header to an uncertain notice or send a dedicated uncertain notice when editing fails. Do not create ReplyMapping unless each final id was durably persisted. Do not call `send_file`, `copy_message`, or `copy_messages` for source content.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m pytest tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py tests/test_bot_media_reply.py -q
```

Expected: marker-before-router, NewMessage/Album race handling, quarantine, preclaim, final mapping, and existing Bot reply behavior pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/tg_v_chat/telegram/telethon_clients src/tg_v_chat/main.py src/tg_v_chat/runtime.py tests/test_bot_native_forward_bridge.py tests/test_native_forward_collector.py
git commit -m "feat: serialize bot native forward bridge"
```

## Phase 2 Plan

The runtime switch, end-to-end matrix, documentation reconciliation, and production release are intentionally separated from the core bridge work. Continue with [V2 activation and release plan](./2026-07-17-native-forward-relay-activation-release-plan.md).

## Self-Review

- Core coverage: identity uniqueness, batch order, marker/update race, no-content quarantine, first-hop count, bridge deadline, preclaimed final dispatch and per-item ReplyMapping each map to Tasks 1-4.
- Phase boundary: linked Phase 2 enabled-route wiring, `777000` contract and documentation reconciliation are now locally E3-verified; only online PostgreSQL, release and E4 checks remain.
- Type consistency: `telegram_user_id`, `batch_sequence`, `bridge_deadline_at`, `FirstHopForwardResult`, `TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED`, and `TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS` match the PRD and technical design.
