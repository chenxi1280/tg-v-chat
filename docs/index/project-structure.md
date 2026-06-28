# Project Structure Index

## Current State

- project_path: `/Users/xida/PycharmProjects/tg-v-chat`
- initialized_at: `2026-06-28`
- last_dev_update: `2026-06-28`
- detected_runtime: Python 3.11+
- git_repository: present
- implementation_status: ready_for_validation
- dependency_manifest: `pyproject.toml`
- migration_tool: Alembic
- test_runner: pytest

## Entry Points

| surface | entrypoint | owner | notes |
| --- | --- | --- | --- |
| Process bootstrap | `src/tg_v_chat/main.py` | dev | Builds SQLite/PostgreSQL-injectable storage, Telethon adapters, Bot handler, and worker runner |
| Bot reply handling | `src/tg_v_chat/bot/handlers.py` | dev | Converts Bot reply commands into relay service calls; non-reply is explicit failure in service |
| Telegram adapters | `src/tg_v_chat/telegram/telethon_clients.py` | dev | Telethon-only gateway/sender ports; disconnected adapters raise explicit errors rather than fake success |
| Worker runner | `src/tg_v_chat/workers/runner.py` | dev | Real worker loop entrypoint; raises when no workers are configured |
| Runtime healthcheck | `src/tg_v_chat/healthcheck.py` | dev | Validates required env and PostgreSQL connectivity |
| Runtime process | `src/tg_v_chat/runtime.py` | dev | Role-based bot/listener/worker process entrypoint |
| Storage bootstrap | `src/tg_v_chat/storage/database.py` | dev | SQLAlchemy engine/session factory; runtime requires PostgreSQL unless tests opt in to SQLite |
| Migrations | `migrations/versions/0001_initial_private_relay.py` | dev | Alembic initial schema migration; production must not rely on `Base.metadata.create_all` |
| CI release workflow | `.github/workflows/deploy-production.yml` | dev | release branch and manual trigger; PostgreSQL service, Alembic migration, pytest, GHCR image, SSH compose deploy |
| Server compose | `docker-compose.server.yml` | dev | Uses infra-compose PostgreSQL via `infra_default`; defines migrate, bot, listener, worker services |

## Module Boundaries

| module | responsibility | data_in | data_out | risk |
| --- | --- | --- | --- | --- |
| `tg_v_chat.domain` | Immutable enums, commands, relay results, and session failure type | Product data model | Typed service contracts | Low |
| `tg_v_chat.crypto` | Encrypt/decrypt Telegram session strings using environment-provided key material | Session string | Fernet token | High: wrong key prevents session restore |
| `tg_v_chat.config` | Required environment loading | env vars | `AppConfig` | Medium: missing secrets block startup explicitly |
| `tg_v_chat.storage.models` | SQLAlchemy persistence schema | Repository writes | Database rows | High: schema changes affect migrations |
| `tg_v_chat.storage.repositories` | UnitOfWork and repository layer | Service calls | persisted rows | Medium |
| `tg_v_chat.services.auth` | Phone code / 2FA binding and account limit enforcement | phone, code, password | Bound account and session slots | High: secrets and auth state |
| `tg_v_chat.services.relay` | Incoming idempotency, Bot push mapping, reply dispatch, failover, outgoing idempotency | Telegram private updates, Bot replies | Relay records, failover events, sent result | High: message correctness |
| `tg_v_chat.telegram` | Telethon integration boundary | connected clients/callables | Bot push and user-session sends | High: production network behavior |
| `tg_v_chat.workers` | Listener/health/failover worker entrypoint | worker callables | worker execution | Medium |
| `.github/workflows/deploy-production.yml` | GitHub Actions release path | push to `release` or manual dispatch | checks, image, deploy jobs | High: production deploy transport |
| `docker-compose.server.yml` | Production compose shape | env file and GHCR image | migrate/bot/listener/worker services | High: production runtime |

## Data Models

| model | table | notes |
| --- | --- | --- |
| SystemUser | `system_users` | Bot user identity |
| BoundTgAccount | `bound_tg_accounts` | Max 20 per SystemUser |
| TgSessionSlot | `tg_session_slots` | primary/standby_1/standby_2, encrypted session nullable until authorized |
| AuthChallenge | `auth_challenges` | phone code and optional 2FA flow state |
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

## Known Limits Before Production

- Telethon adapters are real integration boundaries but production network clients/listeners still need environment-specific wiring and E4 verification.
- Alembic initial migration exists and PostgreSQL SQL generation is verified, but local online PostgreSQL migration is unproven because Docker daemon is unavailable in this environment.
- GitHub Actions release workflow exists but has not run from `release` in this turn; release gate cannot pass until Actions checks/build/deploy evidence exists.
- Release gate remains pending/blocked until CI and production verification complete.

## Update Rule

Dev must update this file when API, worker, page loading, models, schema,
external integrations, or major module boundaries change.
