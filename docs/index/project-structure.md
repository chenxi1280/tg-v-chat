# Project Structure Index

## Current State

- project_path: `/Users/xida/PycharmProjects/tg-v-chat`
- initialized_at: `2026-06-28`
- last_dev_update: `2026-06-29`
- detected_runtime: Python 3.11+
- git_repository: present
- implementation_status: bot_update_runtime_deployed_account_management_code_complete_module_split_e3_passed
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
| Telegram adapters | `src/tg_v_chat/telegram/telethon_clients/` (package) | dev | Telethon bot process, inline callback handling, real user-session authenticator, gateway/sender ports; disconnected adapters raise explicit errors; `__init__.py` re-exports all public symbols |
| Telegram private listener | `src/tg_v_chat/telegram/private_listener/` (package) | dev | Bound listener session and private-message event parsing/formatting; `__init__.py` re-exports `BoundListenerSession`, `TelethonPrivateListenerProcess`, and event helpers |
| Worker runner | `src/tg_v_chat/workers/runner.py` | dev | Real worker loop entrypoint; raises when no workers are configured |
| Runtime healthcheck | `src/tg_v_chat/healthcheck.py` | dev | Validates required env and PostgreSQL connectivity |
| Runtime process | `src/tg_v_chat/runtime.py` | dev | Role-based bot/listener/worker process entrypoint |
| Storage bootstrap | `src/tg_v_chat/storage/database.py` | dev | SQLAlchemy engine/session factory; runtime requires PostgreSQL unless tests opt in to SQLite |
| Migrations | `migrations/versions/0001_initial_private_relay.py`; `migrations/versions/0002_bot_conversation_states.py` | dev | Alembic schema migrations; production must not rely on `Base.metadata.create_all` |
| CI release workflow | `.github/workflows/deploy-production.yml` | dev | release branch and manual trigger; PostgreSQL service, Alembic migration, pytest, GHCR image, SSH compose deploy |
| Server compose | `docker-compose.server.yml` | dev | Uses infra-compose PostgreSQL via `infra_default`; defines migrate, bot, listener, worker services |

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
| `tg_v_chat.storage.repositories` | UnitOfWork and repository layer | Service calls | persisted rows | Medium |
| `tg_v_chat.services.auth` | Phone code / 2FA binding and account limit enforcement | phone, code, password | Bound account and session slots | High: secrets and auth state |
| `tg_v_chat.services.relay` | Incoming idempotency, Bot push mapping, reply dispatch, failover, outgoing idempotency | Telegram private updates, Bot replies | Relay records, failover events, sent result | High: message correctness |
| `tg_v_chat.telegram` | Telethon integration boundary | connected clients/callables | Bot push and user-session sends | High: production network behavior |
| `tg_v_chat.workers` | Listener/health/failover worker entrypoint | worker callables | worker execution | Medium |
| `.github/workflows/deploy-production.yml` | GitHub Actions release path | push to `release` or manual dispatch | checks, image, deploy jobs | High: production deploy transport |
| `docker-compose.server.yml` | Production compose shape | env file and GHCR image | migrate/bot/listener/worker services | High: production runtime |

## Package Structure (Split 2026-06-29)

Single-file modules that exceeded the maintainability threshold were split into packages. Each package `__init__.py` re-exports the original public symbols so every external import path stays unchanged.

### `tg_v_chat.storage.repositories` (package, replaces `repositories.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports all 11 repository classes plus `UnitOfWork` |
| `users.py` | `UserRepository` |
| `accounts.py` | `AccountRepository` (bound accounts, status transitions) |
| `sessions.py` | `SessionSlotRepository` (primary/standby slots) |
| `auth.py` | `AuthChallengeRepository` (phone code / 2FA state) |
| `conversation.py` | `ConversationStateRepository` (per-user bind wizard state) |
| `relay.py` | `RelayRepository`, `PushRepository`, `MappingRepository`, `OutgoingReplyRepository`, `_relay_values` helper |
| `failover.py` | `FailoverRepository` (session failover events) |
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
| `__init__.py` | Re-exports `DeveloperAppConfig`, `TelethonAuthenticator`, `TelethonBotGateway`, `TelethonBotProcess`, `TelethonReplySender`, `TelethonSenderPool`, `_buttons` |
| `config.py` | `DeveloperAppConfig`, `message_kind_text` |
| `helpers.py` | `_run_async`, `_input_peer` |
| `gateway.py` | `TelethonBotGateway`, `TelethonSenderPool`, `TelethonReplySender` |
| `authenticator.py` | `TelethonAuthenticator` (send-code / complete-code / complete-password / session export) |
| `bot_process.py` | `TelethonBotProcess` and inline-keyboard button builders |

### `tg_v_chat.telegram.private_listener` (package, replaces `private_listener.py`)

| module | responsibility |
| --- | --- |
| `__init__.py` | Re-exports `BoundListenerSession`, `TelethonPrivateListenerProcess`, `_format_push_message`, `async_private_message_from_event`, `private_message_event_builder`, `private_message_from_event` |
| `event_parsing.py` | `_peer_id`, `_peer_access_hash`, `_sender_name`, `_media_kind` and event-to-domain conversion |
| `formatting.py` | `_format_push_message`, `_format_message_time` |
| `process.py` | `BoundListenerSession`, `TelethonPrivateListenerProcess`, `_incoming_handler`, `_load_active_bindings`, `_NoopSenderPool` |

### Test split (`tests/`)

`tests/test_account_management_bot_flow.py` (490 lines) was split by scenario into four focused files plus a shared helpers module. Each file stays well under the 500-line limit and isolates a single concern for regression targeting.

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
| BoundTgAccount | `bound_tg_accounts` | Max 20 per SystemUser |
| TgSessionSlot | `tg_session_slots` | primary/standby_1/standby_2, encrypted session nullable until authorized |
| AuthChallenge | `auth_challenges` | phone code and optional 2FA flow state |
| BotConversationState | `bot_conversation_states` | one active phone/code/password wizard state per SystemUser |
| RelayMessage | `relay_messages` | incoming idempotency by bound account and source message id |
| BotPushMessage | `bot_push_messages` | Bot message id generated for reply mapping |
| ReplyMapping | `reply_mappings` | bot_message_id to original peer/source message context |
| OutgoingReply | `outgoing_replies` | outgoing idempotency by bot reply message id |
| SessionFailoverEvent | `session_failover_events` | primary -> standby switch evidence |

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
- ReplyMapping is required for outbound relay; non-reply input fails explicitly.
- ReplyMapping and outgoing idempotency records are scoped to `system_user_id`; cross-user replay is rejected before sender invocation.
- Outgoing bot replies are idempotent by `bot_reply_message_id`.
- Session-layer primary failure switches to standby_1 and records `SessionFailoverEvent`.
- Exhausted sessions raise explicit `SessionFailure` and record an `exhausted` failover event.
- Media groups defer pushes until the sequence anchor arrives, then flush pending relays by sequence.
- Bot `/start` and `/admin` now return the Account Management home with inline keyboard navigation.
- `runtime --role bot` starts a real Telethon Bot process instead of building dependencies and sleeping.
- Account Management Bot Flow PRD is complete and E3-tested for `/start`, `/admin`, bind button, phone/code/2FA wizard, account list, account detail, disable confirmation, callback handling, and reply passthrough.
- Module split (2026-06-29): `storage/repositories.py`, `bot/account_management.py`, `telegram/telethon_clients.py`, and `telegram/private_listener.py` were split into subpackages; `tests/test_account_management_bot_flow.py` was split into 4 scenario files plus shared helpers. All external import paths are preserved via `__init__.py` re-exports; largest file is now 274 lines (`service.py`), well under the 500-line cap. `pytest` (53 passed) and `compileall` both green.

## Known Limits Before Production

- Account Management inline keyboard flow is code-complete and E3-tested locally, but not yet E4-verified in production Telegram after release.
- User session listener and user-session send adapters still need environment-specific production wiring and E4 verification.
- Alembic initial migration exists and PostgreSQL SQL generation is verified, but local online PostgreSQL migration is unproven because Docker daemon is unavailable in this environment.
- GitHub Actions release workflow exists and has deployed the bot update runtime; Account Management still requires release and E4 production verification.
- Release gate for Account Management remains pending until real Telegram callback UI, phone code delivery, optional 2FA, account list/detail, and disable confirmation are verified in production.

## Update Rule

Dev must update this file when API, worker, page loading, models, schema,
external integrations, or major module boundaries change.
