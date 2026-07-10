# tg-v-chat Relay Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for every behavior change and `subagent-driven-development` for task execution and two-stage review. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the approved Telegram private-relay V1 contract for state safety, independent session slots, runtime health, real photo/sticker/album relay, and evidence-aligned documentation.

**Architecture:** Keep the current service/repository boundaries. Add durable dispatch and media-artifact lifecycles, slot-aware authorization, per-account listener reconciliation, a real health worker, and explicit adapter error categories. External Telegram delivery is not described as exactly-once; ambiguous in-flight outcomes become durable `uncertain` records and are not automatically duplicated.

**Tech Stack:** Python 3.11, Telethon, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest, Docker Compose.

---

## File Map

- `docs/product/tg-private-relay-v1.md`: approved state, media, error, mapping, and evidence contract.
- `docs/product/account-management-bot-flow-v1.md`: slot authorization and account-state UI contract.
- `docs/index/dataflow.md`: durable dispatch, media spool, failover, and lifecycle flows.
- `src/tg_v_chat/domain.py`: immutable media, delivery-error, and dispatch contracts.
- `src/tg_v_chat/storage/models.py`: lifecycle fields and relational constraints.
- `migrations/versions/0009_relay_runtime_hardening.py`: schema migration and orphan preflight.
- `src/tg_v_chat/storage/repositories/`: status gates, locking, slot upsert, dispatch, artifact, and invalidation queries.
- `src/tg_v_chat/services/relay.py`: validation, durable failure persistence, error-specific failover, and batch media relay.
- `src/tg_v_chat/services/auth.py`: initial and existing-account slot authorization.
- `src/tg_v_chat/bot/account_management/`: slot-aware callbacks and state transitions.
- `src/tg_v_chat/telegram/media_store.py`: secure shared-spool lifecycle.
- `src/tg_v_chat/telegram/private_listener/`: incoming-only message/album parsing, download, and client reconciliation.
- `src/tg_v_chat/telegram/telethon_clients/`: slot authenticator, Bot media parsing/push, sender classification, and media send.
- `src/tg_v_chat/workers/session_health.py`: real session verification and media cleanup.
- `src/tg_v_chat/runtime.py`, `src/tg_v_chat/healthcheck.py`, `docker-compose.server.yml`: worker and role heartbeat wiring.
- `tests/`: focused service, adapter, migration, runtime, and media-store regression tests.

### Task 1: Freeze Product And Dataflow Contracts

**Files:**
- Modify: `docs/product/tg-private-relay-v1.md`
- Modify: `docs/product/account-management-bot-flow-v1.md`
- Modify: `docs/product/product-index.md`
- Modify: `docs/index/dataflow.md`
- Modify: `docs/qa/validation-plan.md`

- [ ] **Step 1: Add a failing contract scan**

Add `tests/test_document_contracts.py` asserting the PRD contains `pending/sending/sent/failed/uncertain` for outbound, Bot push, and album dispatch; `TG_V_CHAT_MEDIA_ROOT`; `account.slot.bind`; `DeliveryFailure`; `SessionFailure`; mapping `active/expired`; and explicit `E4 unproven` wording.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_document_contracts.py -q`

Expected: failures for missing contract terms.

- [ ] **Step 3: Update product truth before code**

Document these exact decisions:

```text
ReplyMapping: active/expired; invalidated_at records explicit invalidation; time TTL is unimplemented until product approves a duration.
Outgoing, Bot push, and album dispatch: pending/sending/sent/failed/uncertain.
Slot authorization: primary, standby_1, standby_2 are independently authorized.
Media storage: shared owner-only spool at TG_V_CHAT_MEDIA_ROOT with durable artifact state.
Error split: session failures may fail over; business failures never poison session health.
Evidence: local/CI completion remains E3; real Telegram and production remain E4-unproven.
Account state: usable primary=active; only standby usable=degraded; no usable slot=reauth_required; disabled/deleted never auto-recomputed.
Product index: remediation_in_progress during implementation, never prematurely complete.
```

- [ ] **Step 4: Verify GREEN and review diff**

Run: `.venv/bin/python -m pytest tests/test_document_contracts.py -q`

Expected: pass. Request spec-compliance review before Task 2.

### Task 2: Add Domain And Schema Foundations

**Files:**
- Modify: `src/tg_v_chat/domain.py`
- Modify: `src/tg_v_chat/storage/models.py`
- Create: `src/tg_v_chat/storage/repositories/dispatch.py`
- Create: `src/tg_v_chat/storage/repositories/media.py`
- Modify: `src/tg_v_chat/storage/repositories/accounts.py`
- Modify: `src/tg_v_chat/storage/repositories/sessions.py`
- Modify: `src/tg_v_chat/storage/repositories/relay.py`
- Modify: `src/tg_v_chat/storage/repositories/unit_of_work.py`
- Create: `migrations/versions/0009_relay_runtime_hardening.py`
- Test: `tests/test_relay_schema_hardening.py`
- Test: `tests/test_postgresql_relay_integration.py`

- [ ] **Step 1: Write failing model and repository tests**

Tests must require:

```python
MediaArtifact(storage_key="abc", file_name="a.webp", mime_type="image/webp", byte_size=4, media_kind=MediaKind.STICKER, sequence=1)
DeliveryFailure("peer_invalid", "peer unavailable")
DeliveryUncertain("transport_unknown", "result unknown")
```

Assert mapping invalidation timestamps, nullable outgoing and Bot-push result fields, `pending/sending/sent/failed/uncertain`, nullable exhausted `to_slot`, artifact/group tables, required foreign keys, account advisory-lock support, slot authorization upsert, session versioning, AuthChallenge purpose, and mapping invalidation by account.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_relay_schema_hardening.py -q`

Expected: imports/columns/repositories missing.

- [ ] **Step 3: Implement minimal domain and persistence contracts**

Add immutable domain types and error categories. Add models for `RelayMediaArtifactModel` and `RelayMediaGroupModel`. Extend `OutgoingReplyModel`, `BotPushMessageModel`, `ReplyMappingModel`, `TgSessionSlotModel`, `AuthChallengeModel`, and `SessionFailoverEventModel`. Add repositories with explicit create/claim/transition methods and an account-scoped PostgreSQL advisory-lock context; business services must not mutate models directly.

Migration preflight must raise on orphan references before adding foreign keys. It must not delete data.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_relay_schema_hardening.py -q
TG_V_CHAT_DATABASE_URL=postgresql+psycopg://user:pass@localhost/db .venv/bin/alembic upgrade head --sql > /tmp/tg-v-chat-0009.sql
```

Expected: tests pass and offline PostgreSQL SQL generation succeeds.

When `TG_V_CHAT_TEST_DATABASE_URL` is available, run the PostgreSQL integration file and require online orphan-preflight and foreign-key rejection. A missing PostgreSQL test database is reported as `unproven`, not replaced by SQLite evidence.

- [ ] **Step 5: Spec and code-quality review checkpoint**

Review only Task 2 files. Fix all Critical/Important findings before Task 3.

### Task 3: Enforce Status Gates And Durable Failure Results

**Files:**
- Modify: `src/tg_v_chat/services/relay.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/gateway.py`
- Modify: `src/tg_v_chat/storage/repositories/relay.py`
- Modify: `src/tg_v_chat/bot/account_management/service.py`
- Modify: `src/tg_v_chat/bot/account_management/state_helpers.py`
- Test: `tests/test_private_relay_v1.py`
- Create: `tests/test_relay_transactions.py`
- Test: `tests/test_account_actions.py`

- [ ] **Step 1: Write failing state-gate tests**

Add tests where setup, disable/invalidate, and reply occur in separate UoWs. Disabled/deleted/reauth-required accounts, disabled system users, and expired mappings must raise before sender invocation. Add incoming tests proving disabled/deleted accounts and disabled system users create zero Bot pushes, zero successful RelayMessage rows, and zero mappings during the listener reconciliation window.

- [ ] **Step 2: Write failing transaction and classification tests**

Require:

- all session failures remain persisted after the service UoW exits;
- exhausted `to_slot` is null and account becomes `reauth_required`;
- a standby success persists failed primary plus switch event and sets account `degraded`;
- peer/permission/FloodWait/media failures create failed outgoing state but no failover;
- ambiguous transport creates `uncertain` and duplicate Bot update performs no second send;
- stale outbound or Bot-push `sending` becomes `uncertain` after restart and performs no second send;
- failed duplicate reply returns the stored failure without resending.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_private_relay_v1.py tests/test_relay_transactions.py tests/test_account_actions.py -q`

Expected: focused assertions fail for current behavior.

- [ ] **Step 4: Implement service flow**

The service order is:

```text
resolve active system user -> resolve mapping -> acquire account advisory lock
-> revalidate statuses -> CAS claim dispatch as sending and commit
-> send using usable slots -> commit sent/failed/uncertain and failover/account state
-> release advisory lock in finally -> return or raise
```

Only `SessionFailure` advances slots. `DeliveryFailure` and `DeliveryUncertain` do not. Commit durable terminal state before surfacing the exception. Disable and delete call mapping invalidation in the same transaction as the account-state change. Do not claim a row lock survives the pre-I/O commit.

- [ ] **Step 5: Verify GREEN and existing relay regression**

Run: `.venv/bin/python -m pytest tests/test_private_relay_v1.py tests/test_relay_transactions.py tests/test_account_actions.py -q`

Expected: pass.

- [ ] **Step 6: Spec and code-quality review checkpoint**

Fix all incorrect status transitions, retry behavior, or transaction findings before Task 4.

### Task 4: Implement Independent Slot Authorization

**Files:**
- Modify: `src/tg_v_chat/services/auth.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/authenticator.py`
- Modify: `src/tg_v_chat/main.py`
- Modify: `src/tg_v_chat/runtime.py`
- Modify: `src/tg_v_chat/bot/account_management/service.py`
- Modify: `src/tg_v_chat/bot/account_management/rendering.py`
- Modify: `src/tg_v_chat/bot/account_management/state_helpers.py`
- Test: `tests/test_account_slot_authorization.py`
- Test: `tests/test_account_bind_wizard.py`

- [ ] **Step 1: Write failing slot tests**

Require primary, standby_1, and standby_2 to save three different encrypted sessions through their corresponding developer app configs. Test owner scoping, targeted upsert, other-slot preservation, 2FA cancel safety, reauthorization preserving account id/mappings, and status recomputation.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_account_slot_authorization.py tests/test_account_bind_wizard.py -q`

Expected: callbacks and slot-aware authenticator missing.

- [ ] **Step 3: Implement slot-aware flow**

Use callbacks:

```text
account.slot.bind:{account_id}:standby_1
account.slot.bind:{account_id}:standby_2
account.slot.reauth:{account_id}:{slot}
```

Add `AuthChallenge.purpose` values `initial_bind` and `slot_authorization`. Initial completion writes primary and placeholders only; slot completion upserts only the target slot. Cancelling a slot challenge never disables a still-operational account.

- [ ] **Step 4: Verify GREEN and full account-management regression**

Run: `.venv/bin/python -m pytest tests/test_account_slot_authorization.py tests/test_account_bind_wizard.py tests/test_account_actions.py tests/test_account_relogin.py -q`

Expected: pass.

- [ ] **Step 5: Spec and code-quality review checkpoint**

Confirm no primary session is copied into a standby slot and no reauth path deletes account history.

### Task 5: Add Secure Media Store And Incoming Media Batches

**Files:**
- Create: `src/tg_v_chat/telegram/media_store.py`
- Modify: `src/tg_v_chat/telegram/private_listener/event_parsing.py`
- Modify: `src/tg_v_chat/telegram/private_listener/process.py`
- Modify: `src/tg_v_chat/telegram/private_listener/__init__.py`
- Modify: `src/tg_v_chat/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.server.yml`
- Modify: `src/tg_v_chat/services/relay.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/gateway.py`
- Test: `tests/test_media_store.py`
- Test: `tests/test_private_listener_media.py`
- Test: `tests/test_bot_media_push.py`

- [ ] **Step 1: Write failing secure-spool tests**

Require an absolute shared media root, owner-only directory/file modes, server-generated storage keys, file-name sanitization, `.part` atomic finalize, explicit download failure, release of sent/deterministic-failed artifacts, retention of pending/sending/uncertain artifacts, and non-blocking-lock cleanup of unreferenced `.part` files. Compose must define one named `tg-v-chat-media` volume and mount that same volume at the same `TG_V_CHAT_MEDIA_ROOT` path in bot, listener, and worker; tests inspect the rendered compose config and assert identical source/target mounts.

- [ ] **Step 2: Write failing listener and Bot-push tests**

Require `NewMessage(incoming=True)` for non-grouped messages, grouped-message suppression, Album incoming validation in the handler, explicit message-id sort, real sequence `1..N`, whole-album failure on one download failure, real `send_file`, one returned Bot id per relay row, unsupported incoming media rejection, and zero `[photo]`/`[sticker]` fallback text.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_media_store.py tests/test_private_listener_media.py tests/test_bot_media_push.py -q`

Expected: media store/batch APIs missing and current placeholder assertions fail.

- [ ] **Step 4: Implement minimal media batch flow**

Introduce `MediaStore`, immutable artifacts, `IncomingPrivateBatch`, and `BotGateway.push_batch`. Download and finalize every album item before service dispatch. Persist relay/artifact/group rows and claim Bot-push dispatch before Bot I/O. Use a single ordered `send_file` batch for albums and zip returned message ids with ordered relay rows. Add a crash injection where Bot returns ids but terminal DB update is skipped; recovery must mark stale `sending` uncertain and avoid a second push.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_media_store.py tests/test_private_listener.py tests/test_private_listener_media.py tests/test_bot_media_push.py -q`

Expected: pass.

- [ ] **Step 6: Spec and code-quality review checkpoint**

Confirm Telethon objects do not enter services, paths cannot escape media root, and album partial success is not reported.

### Task 6: Implement Bot Media Replies And User-Session Media Send

**Files:**
- Modify: `src/tg_v_chat/bot/router.py`
- Modify: `src/tg_v_chat/bot/handlers.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/bot_process.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/gateway.py`
- Modify: `src/tg_v_chat/services/relay.py`
- Test: `tests/test_bot_media_reply.py`
- Test: `tests/test_telethon_media_sender.py`

- [ ] **Step 1: Write failing Bot-reply parsing tests**

Require photo/sticker classification, caption preservation, real download to MediaStore, ordered outgoing album parsing, identical reply mapping across album items, and explicit rejection of unsupported media.

- [ ] **Step 2: Write failing sender tests**

Require text to use `send_message`; photo/sticker/album to use ordered `send_file`; result ids to be persisted; peer/content/FloodWait errors to remain DeliveryFailure; session/auth errors alone to be SessionFailure; ambiguous transport to be DeliveryUncertain.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_bot_media_reply.py tests/test_telethon_media_sender.py -q`

Expected: current Bot parser reports text and sender always calls send_message.

- [ ] **Step 4: Implement media reply path**

Download Bot media inside the Bot event loop before crossing to the synchronous router. Send ordered artifact paths through the selected user session. Persist terminal dispatch state before releasing files; sent and deterministic-failed files are released after metadata is durable, while pending/sending/uncertain files remain.

- [ ] **Step 5: Verify GREEN and relay regression**

Run: `.venv/bin/python -m pytest tests/test_bot_media_reply.py tests/test_telethon_media_sender.py tests/test_private_relay_v1.py -q`

Expected: pass.

- [ ] **Step 6: Spec and code-quality review checkpoint**

Confirm media failures never silently become text and never poison session state unless classification is session-specific.

### Task 7: Implement Listener Reconciliation, Worker, And Role Health

**Files:**
- Modify: `src/tg_v_chat/telegram/private_listener/process.py`
- Create: `src/tg_v_chat/workers/session_health.py`
- Modify: `src/tg_v_chat/workers/runner.py`
- Modify: `src/tg_v_chat/runtime.py`
- Modify: `src/tg_v_chat/healthcheck.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/bot_process.py`
- Modify: `docker-compose.server.yml`
- Test: `tests/test_listener_reconciliation.py`
- Test: `tests/test_session_health_worker.py`
- Test: `tests/test_role_health.py`

- [ ] **Step 1: Write failing listener reconciliation tests**

Require one-account failure isolation, disconnected-client replacement, slot/version fingerprint replacement, disabled-account disconnect, operational `active/degraded` queries, incoming-only subscriptions, and listener-side standby selection with persisted event.

- [ ] **Step 2: Write failing worker and heartbeat tests**

Require worker role to call a real `run_forever`, unauthorized session to become revoked, transport probe failure not to poison authorization, account status recomputation, media release retries, fresh heartbeat acceptance, and stale/missing heartbeat rejection despite a healthy database.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_listener_reconciliation.py tests/test_session_health_worker.py tests/test_role_health.py -q`

Expected: current account-id-only reconciliation, idle worker, and DB-only health fail.

- [ ] **Step 4: Implement runtime loops**

Store listener clients with account, slot id, session revision, and connection state. Catch startup errors per account. Worker runs session checks and media cleanup. Bot heartbeat is refreshed only by its connected event loop; listener heartbeat only after a connected reconciliation cycle; worker heartbeat only after a completed work cycle. Startup removes stale role files. `healthcheck --role` validates PostgreSQL plus a fresh role heartbeat using named, test-injectable timing constants aligned with compose intervals.

- [ ] **Step 5: Verify GREEN and compose interpolation**

Run:

```bash
.venv/bin/python -m pytest tests/test_listener_reconciliation.py tests/test_session_health_worker.py tests/test_role_health.py -q
docker compose --env-file .env.example -f docker-compose.server.yml config >/tmp/tg-v-chat-compose.yml
```

Expected: tests and compose validation pass.

- [ ] **Step 6: Spec and code-quality review checkpoint**

Confirm a dead role cannot remain healthy solely because PostgreSQL responds.

### Task 8: Reconcile Indexes, QA Evidence, And Full Verification

**Files:**
- Modify: `docs/product/product-index.md`
- Modify: `docs/index/project-structure.md`
- Modify: `docs/index/dataflow.md`
- Modify: `docs/qa/validation-plan.md`
- Modify: `docs/product/tg-private-relay-v1.md`
- Modify: `docs/product/account-management-bot-flow-v1.md`

- [ ] **Step 1: Update evidence from actual test results**

Populate the validation matrix with each automated contract and mark real Telegram network behavior and production deployment `unproven`. Remove stale `release_pending` or `deployed` wording that conflicts with verified evidence.

- [ ] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -c 'import subprocess; subprocess.run([".venv/bin/python", "-m", "pytest", "-q"], timeout=60, check=True)'
.venv/bin/python -m compileall -q src tests
git diff --check
TG_V_CHAT_DATABASE_URL=postgresql+psycopg://user:pass@localhost/db .venv/bin/alembic upgrade head --sql >/tmp/tg-v-chat-upgrade.sql
docker compose --env-file .env.example -f docker-compose.server.yml config >/tmp/tg-v-chat-compose.yml
```

Expected: zero test failures, compile success, clean diff, migration SQL generation, and compose interpolation success.

- [ ] **Step 3: Independent final review**

Dispatch a fresh reviewer with the design, this plan, base SHA `fa1e2b2`, current head/diff, and complete test output. Fix every Critical or Important finding and re-run the full verification bundle.

- [ ] **Step 4: Clean temporary planning artifacts**

Keep the approved design and implementation plan. Remove root `task_plan.md`, `findings.md`, and `progress.md` unless the user asks to retain session working notes. Do not commit, push, merge, deploy, or modify production without separate authorization.
