# Project Structure Index

## Current State

- project_path: `/Users/xida/PycharmProjects/tg-v-chat`
- initialized_at: `2026-06-28`
- last_dev_update: `2026-07-17`
- detected_runtime: Python 3.11+
- git_repository: present
- implementation_status: native_forward_v2_implemented_e3_passed_e4_unproven
- dependency_manifest: `pyproject.toml`
- migration_tool: Alembic
- test_runner: pytest

## Entry Points

| surface | entrypoint | owner | notes |
| --- | --- | --- | --- |
| Process bootstrap | `src/tg_v_chat/main.py` | dev | Builds SQLite/PostgreSQL-injectable storage, Telethon adapters, Bot handler, and worker runner |
| Bot update routing | `src/tg_v_chat/bot/router.py` | dev | Handles commands, callback queries, account-management text steps, and reply command dispatch with explicit failure replies |
| Account management Bot flow | `src/tg_v_chat/bot/account_management/` (package) | dev | Renders Account Management home/list/detail/status/help, tracks bind wizard steps, and routes phone/code/2FA inputs; re-exports `AccountManagementService` from `__init__.py` so external import paths stay unchanged |
| Bot reply handling | `src/tg_v_chat/bot/handlers.py` | dev | Converts Bot reply commands into relay service calls; non-reply is explicit failure in service |
| Telegram adapters | `src/tg_v_chat/telegram/telethon_clients/` (package) | dev | Telethon bot process, inline callback handling, real user-session authenticator, V2 Bot bridge, gateway/sender ports; disconnected adapters raise explicit errors; `__init__.py` re-exports public symbols |
| Telegram private listener | `src/tg_v_chat/telegram/private_listener/` (package) | dev | Bound listener session identity, V1/V2 private event parsing, V2 user-session first hop, and push formatting; `__init__.py` re-exports `BoundListenerSession`, `TelethonPrivateListenerProcess`, and event helpers |
| Native forward protocol | `src/tg_v_chat/telegram/native_forward_protocol.py` | dev | Package-neutral V2 marker serialization and parsing shared by the listener first hop and Bot bridge, avoiding a listener/Bot package import cycle |
| Worker runner | `src/tg_v_chat/workers/runner.py` | dev | Real `run_once` / `run_forever` worker loop entrypoint; raises when no workers are configured |
| Session health worker | `src/tg_v_chat/workers/session_health.py` | dev | Verifies authorized sessions, recomputes account status, and retries terminal media release |
| Runtime healthcheck | `src/tg_v_chat/healthcheck.py` | dev | Validates PostgreSQL connectivity plus optional role heartbeat freshness |
| Runtime heartbeat | `src/tg_v_chat/runtime_health.py` | dev | Role heartbeat files and freshness constants for bot/listener/worker health |
| Runtime process | `src/tg_v_chat/runtime.py` | dev | Role-based bot/listener/worker process entrypoint with stale heartbeat cleanup |
| Storage bootstrap | `src/tg_v_chat/storage/database.py` | dev | SQLAlchemy engine/session factory; runtime requires PostgreSQL unless tests opt in to SQLite |
| Media store | `src/tg_v_chat/telegram/media_store.py` | dev | Owner-only shared file spool for incoming/outgoing Telegram media artifacts |
| Migrations | `migrations/versions/0001_initial_private_relay.py` ... `migrations/versions/0011_scope_native_forward_bridge_items.py` | dev | Alembic schema migrations; production must not rely on `Base.metadata.create_all` |
| CI release workflow | `.github/workflows/deploy-production.yml` | dev | release branch and manual trigger; PostgreSQL service, Alembic migration, pytest, GHCR image, SSH compose deploy |
| Server compose | `docker-compose.server.yml` | dev | Uses infra-compose PostgreSQL via `infra_default`; defines migrate, root-only media-volume initialization, bot, listener, worker services, shared media volume, and role healthchecks |
| Container image | `Dockerfile` | dev | Installs the application and creates the appuser-owned `0700` role-heartbeat directory before dropping privileges |

## Account Management Bot Flow

| surface | entrypoint | owner | notes |
| --- | --- | --- | --- |
| Account management PRD | `docs/product/account-management-bot-flow-v1.md` | product | Defines `/start` and `/admin` as the Account Management home with inline keyboard actions |
| Bot account flow | `src/tg_v_chat/bot/account_management/` (package) | dev | Renderer and stateful handler for home, empty state, account list, account detail, status, help, binding prompts, cancel, and disable confirmation; split into `constants.py`, `parsing.py`, `rendering.py`, `state_helpers.py`, and `service.py` |
| Bot callback routing | `src/tg_v_chat/bot/router.py` | dev | Routes `account.home`, `account.bind.start`, `account.list`, `account.status`, `account.detail:{id}`, and disable callbacks |
| Bot conversation state | `bot_conversation_states` table | dev | Per-SystemUser state resolver for phone/code/password binding steps |
| Telegram authenticator | `src/tg_v_chat/telegram/telethon_clients/authenticator.py` | dev | Real user-session auth adapter for send-code, complete-code, complete-password, and session export |

## Module Boundaries

| module | responsibility | data_in | data_out | risk |
| --- | --- | --- | --- | --- |
| `tg_v_chat.domain` | Immutable enums, commands, relay results, and session failure type | Product data model | Typed service contracts | Low |
| `tg_v_chat.crypto` | Encrypt/decrypt Telegram session strings using environment-provided key material | Session string | Fernet token | High: wrong key prevents session restore |
| `tg_v_chat.config` | Required environment loading | env vars | `AppConfig` | Medium: missing secrets block startup explicitly |
| `tg_v_chat.bot.router` | Bot command/callback/reply routing | Telegram Bot private messages and callbacks | BotResponse or BotReplyCommand dispatch | High: user-visible bot responsiveness |
| `tg_v_chat.bot.account_management` | Account management message rendering and wizard state transitions | account summary, binding state, account detail | Bot message text and reply markup | High: first-run UX and binding clarity |
| `tg_v_chat.storage.models` | SQLAlchemy persistence schema | Repository writes | Database rows | High: schema changes affect migrations |
| `tg_v_chat.storage.repositories` | UnitOfWork and repository layer | Service calls | persisted rows, locks, dispatch records, media metadata | Medium |
| `tg_v_chat.services.auth` | Phone code / 2FA binding, account limit enforcement, and atomic real-Telegram identity persistence | phone, code, password | Bound account and session slots | High: secrets and auth state |
| `tg_v_chat.services.relay` | Incoming idempotency, Bot push mapping, reply dispatch, failover, outgoing idempotency, media artifact release | Telegram private updates, Bot replies | Relay records, dispatch rows, media metadata, failover events, sent result | High: message correctness |
| `tg_v_chat.services.native_forward` | V2 collecting, first-hop claim/dispatch, deadline reconciliation, and explicit failure notification | V2 listener updates, user-session forwarder | Native batch/item state and first-hop result | High: no copy fallback and no unsafe replay |
| `tg_v_chat.services.relay_conversions` | Pure relay model-to-domain conversion helpers | ORM rows and account identity | typed relay domain objects | Low |
| `tg_v_chat.telegram` | Telethon integration boundary | connected clients/callables/media files | Bot push, user-session sends, listener downloads | High: production network behavior |
| `tg_v_chat.workers` | Session health and media cleanup worker entrypoint | worker callables, database rows, media spool | worker execution and account/session recompute | Medium |
| `.github/workflows/deploy-production.yml` | GitHub Actions release path | push to `release` or manual dispatch | checks, image, deploy jobs | High: production deploy transport |
| `docker-compose.server.yml` | Production compose shape | env file and GHCR image | migrate/bot/listener/worker services | High: production runtime |

## Package Structure (Split 2026-06-29)

Single-file modules that exceeded the maintainability threshold were split into packages. Each package `__init__.py` re-exports the original public symbols so every external import path stays unchanged.

### `tg_v_chat.storage.repositories` (package, replaces `repositories.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports repository classes, account operation locks, media repositories, and `UnitOfWork` |
| `users.py` | `UserRepository` |
| `accounts.py` | `AccountRepository` (bound accounts, operational status queries, status transitions) |
| `sessions.py` | `SessionSlotRepository` (primary/standby slots, revision, health transitions) |
| `auth.py` | `AuthChallengeRepository` (phone code / 2FA state) |
| `conversation.py` | `ConversationStateRepository` (per-user bind wizard state) |
| `relay.py` | `RelayRepository`, `MappingRepository`, `_relay_values` helper |
| `dispatch.py` | `PushRepository`, `OutgoingReplyRepository`, durable dispatch claim/terminal transitions |
| `failover.py` | `FailoverRepository` (session failover events) |
| `media.py` | `MediaArtifactRepository`, `MediaGroupRepository`, artifact lifecycle and album dispatch metadata |
| `native_forward.py` | `NativeForwardRepository`, atomic batch order, sender-scoped expected/actual bridge item persistence, final push claims, terminal state, and quarantine audit |
| `locks.py` | `AccountOperationLock`, `TelegramIdentityLock`, database-backed per-account/real-Telegram-identity operation locking |
| `unit_of_work.py` | `UnitOfWork` aggregating every repository |

### `tg_v_chat.bot.account_management` (package, replaces `account_management.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports `AccountManagementService` |
| `constants.py` | `STATE_AWAITING_*`, `ACCOUNT_STATUS_*`, `AUTH_STATUS_*`, `PHONE_PATTERN`, help/prompt text |
| `parsing.py` | `_parse_id`, `_require_challenge` |
| `rendering.py` | `_home_text`, `_home_buttons`, `_accounts_text`, `_detail_text`, `_mask_phone`, `_code_prompt_response` and all message rendering |
| `state_helpers.py` | `_cancel_abandoned_bindings`, `_delete_account_for_user`, `_auth_failure_response` |
| `service.py` | `AccountManagementService` main handler class |

### `tg_v_chat.telegram.telethon_clients` (package, replaces `telethon_clients.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports `DeveloperAppConfig`, `NativeForwardBridgeHandler`, `TelethonAuthenticator`, `TelethonBotGateway`, `TelethonBotProcess`, `TelethonReplySender`, `TelethonSenderPool`, `_buttons` |
| `config.py` | `DeveloperAppConfig`, `message_kind_text` |
| `helpers.py` | `_run_async`, `_input_peer` |
| `gateway.py` | `TelethonBotGateway`, `TelethonSenderPool`, `TelethonReplySender` |
| `authenticator.py` | `TelethonAuthenticator` (send-code / complete-code / complete-password / session export) |
| `bot_process.py` | `TelethonBotProcess`, Bot reply media download, album reply parsing, role heartbeat, and inline-keyboard button builders |
| `forward_bridge.py` | V2 marker/item/album interception before the normal Bot router, final native forwarding, preclaimed ledger completion, and quarantine |

### `tg_v_chat.telegram.private_listener` (package, replaces `private_listener.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports `BoundListenerSession`, `TelethonPrivateListenerProcess`, V1/V2 event conversion helpers, `_format_push_message`, and event builders |
| `event_parsing.py` | `_peer_id`, `_peer_access_hash`, `_sender_name`, V1/V2 media-kind detection, and event-to-domain conversion |
| `formatting.py` | `_format_push_message`, `_format_message_time` |
| `native_forward.py` | User-session marker + source-message first-hop `forward_messages` adapter and marker serialization/parser |
| `process.py` | `BoundListenerSession`, `ListenerClientState`, V1/V2 listener reconciliation, incoming media/album handlers, identity readiness, and `_NoopSenderPool` |

### `tg_v_chat.workers`

| module | responsibility |
| --- | --- |
| `runner.py` | `WorkerRunner.run_once` and `run_forever` scheduling loop |
| `session_health.py` | `SessionHealthWorker`, `TelethonSessionVerifier`, account status recompute, terminal media release retry |

### Test split (`tests/`)

The former 490-line account-management test was split by scenario into four focused files plus a shared helpers module. Each file stays well under the 500-line limit and isolates a single concern for regression targeting.

| module | responsibility |
| --- | --- |
| `tests/account_management_helpers.py` | Shared `FakeAuthenticator`, `RetryPasswordAuthenticator`, `bot_parts` fixture, `submit_code_with_keypad` helper |
| `tests/test_account_management_home.py` | `/start` and `/admin` home rendering, empty state, help text |
| `tests/test_account_bind_wizard.py` | Phone entry, code keypad, 2FA password, code/password failure handling |
| `tests/test_account_relogin.py` | Expired-code recovery, abandoned binding state, relogin with same phone |
| `tests/test_account_actions.py` | Account list, detail, disable confirmation, delete (active and incomplete) |

## Data Models

| model | table | notes |
| --- | --- | --- |
| SystemUser | `system_users` | Bot user identity |
| BoundTgAccount | `bound_tg_accounts` | Max 20 per SystemUser; stores phone_number, display_name, username, and nullable globally unique real `telegram_user_id` for V2 bridge ownership |
| TgSessionSlot | `tg_session_slots` | primary/standby_1/standby_2, encrypted session nullable until authorized |
| AuthChallenge | `auth_challenges` | phone code and optional 2FA flow state |
| BotConversationState | `bot_conversation_states` | one active phone/code/password wizard state per SystemUser |
| RelayMessage | `relay_messages` | incoming idempotency by bound account, peer, and source message id |
| BotPushMessage | `bot_push_messages` | Bot message id generated for reply mapping; unique by `system_user_id + bot_message_id` |
| ReplyMapping | `reply_mappings` | `system_user_id + bot_message_id` to original peer/source message context |
| OutgoingReply | `outgoing_replies` | outgoing idempotency by `system_user_id + bot_reply_message_id` |
| SessionFailoverEvent | `session_failover_events` | primary -> standby switch evidence |
| RelayMediaArtifact | `relay_media_artifacts` | incoming/outgoing file artifact metadata, status, release evidence |
| RelayMediaGroup | `relay_media_groups` | album dispatch metadata and terminal state |
| NativeForwardBatch | `native_forward_batches` | V2 collecting/sealed/bridge/final lifecycle, marker token, peer, deadline, header result, and terminal evidence |
| NativeForwardItem | `native_forward_items` | Per-relay V2 `batch_sequence`, sender-scoped expected/actual bridge ids, final message id, final BotPushMessage linkage, identity visibility, and terminal evidence |
| NativeForwardBridgeQuarantine | `native_forward_bridge_quarantines` | Known bridge sender’s malformed/orphan audit without private message content or media |

## Test And Verification Entrypoints

| command | purpose | owner | status |
| --- | --- | --- | --- |
| `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'` | Backend unit tests with 60 second timeout | dev | passed |
| `python3 -m pytest` | Full pytest suite | dev/qa | passed |
| `python3 -m compileall -q src tests` | Syntax/import compilation | dev | passed |
| `TG_V_CHAT_DATABASE_URL=postgresql+psycopg://... alembic upgrade head --sql` | PostgreSQL migration SQL generation | dev | passed |
| `docker compose --env-file .env.example -f docker-compose.server.yml config` | Server compose interpolation check | dev | passed |
| AST metrics scan | File and function length guard | dev | passed |

## Verified Behaviors

- Phone code plus 2FA binding persists encrypted primary session and creates three session slots.
- The 21st account for one SystemUser is rejected explicitly.
- Incoming private message idempotency prevents duplicate Bot pushes.
- Bot pushes show sender,接收账号名,接收用户名, time, and content so multi-account inboxes are distinguishable.
- ReplyMapping is required for outbound relay; non-reply input fails explicitly.
- ReplyMapping and outgoing idempotency records are scoped to `system_user_id`; cross-user replay is rejected before sender invocation.
- Bot message IDs are scoped per SystemUser, matching Telegram chat-local `message_id` semantics.
- Outgoing bot replies are idempotent by `system_user_id + bot_reply_message_id`.
- Session-layer primary failure switches to standby_1 and records `SessionFailoverEvent`.
- Exhausted sessions raise explicit `SessionFailure` and record an `exhausted` failover event.
- Media groups defer pushes until the sequence anchor arrives, then flush pending relays by sequence.
- Incoming and outgoing photo/sticker artifacts use the shared media spool and are released after sent or deterministic failed dispatch.
- Duplicate media updates and duplicate Bot media replies discard newly downloaded artifacts without resending or leaving ready files.
- Listener reconciliation isolates one-account startup failures, replaces changed session fingerprints, disconnects non-operational accounts, and can listen via standby for degraded accounts.
- Worker role runs real health cycles: unauthorized sessions become revoked, transport probe failures do not poison session health, account status is recomputed, and terminal media release is retried.
- Role healthchecks require PostgreSQL plus fresh bot/listener/worker heartbeat files.
- Bot `/start` and `/admin` now return the Account Management home with inline keyboard navigation.
- `runtime --role bot` starts a real Telethon Bot process instead of building dependencies and sleeping.
- Account Management Bot Flow PRD is complete and E3-tested for `/start`, `/admin`, bind button, phone/code/2FA wizard, account identity display, account detail, disable confirmation, callback handling, and reply passthrough.
- Module split (2026-06-29): `storage/repositories.py`, `bot/account_management.py`, `telegram/telethon_clients.py`, and `telegram/private_listener.py` were split into subpackages; the former account-management monolith test was split into 4 scenario files plus shared helpers. All external import paths are preserved via `__init__.py` re-exports. Current source audit finds no file over 500 lines, no function over 50 lines, and no block nesting deeper than 3.
- Native Forward V2 local E3 verification (2026-07-17): full pytest is `249 passed, 8 skipped`; the skipped tests require `TG_V_CHAT_TEST_DATABASE_URL` for online PostgreSQL evidence. `compileall`, PostgreSQL offline migration SQL generation, and `docker compose ... config` also pass. This is not GitHub Actions, online PostgreSQL integration, or Telegram E4 evidence.

## Known Limits Before Production

- Account Management inline keyboard flow is code-complete and E3-tested locally, but not yet E4-verified in production Telegram after release.
- User session listener, user-session send adapters, media relay, and role heartbeat are E3-tested locally but still need environment-specific E4 verification.
- Alembic initial migration exists and PostgreSQL SQL generation is verified, but local online PostgreSQL migration is unproven because Docker daemon is unavailable in this environment.
- GitHub Actions release workflow and compose wiring exist, but this branch has not been released or E4-verified in production.
- Native Forward V2 remains disabled by default; its real linked/name_only, media, reply, protected-content, timeout/restart and `777000` cases require isolated-account E4 evidence after release.
- Release gate remains pending until real Telegram callback UI, phone code delivery, optional 2FA, account list/detail, listener intake, media relay, outbound reply, role heartbeat, and disable confirmation are verified in production.

## Update Rule

Dev must update this file when API, worker, page loading, models, schema,
external integrations, or major module boundaries change.
