# Dataflow Index

## Business Objects

| object | source | transformations | storage | consumers | owner |
| --- | --- | --- | --- | --- | --- |
| BoundTgAccount | TG Bot binding flow | phone/code/2FA 授权后生成 session 槽位 | bound_tg_accounts, tg_session_slots | session listener, session selector | product/dev |
| RelayMessage | Telegram user session listener | 私聊消息归一化为文字、emoji、图片、sticker 元数据 | relay_messages, bot_push_messages, reply_mappings | Bot push, Bot reply handler, QA | product/dev |
| ReplyMapping | BotPushMessage creation | bot_message_id 映射到 bound_tg_account_id、peer_id、source_message_id、media context | reply_mappings | Bot reply handler | product/dev |
| SessionFailoverEvent | session selector | primary -> standby_1 -> standby_2 自动切换记录 | session_failover_events | ops, QA, product acceptance | product/dev |
| ApplicationScaffold | product scaffold authorization | Python/Telethon app bootstrap, SQLAlchemy repositories, worker entrypoints, pytest verification | repo source tree and dependency manifest | dev, qa | product/dev |
| ReleaseDeployment | product deploy decision | release branch -> GitHub Actions checks/build/deploy -> server compose on infra network | GHCR image, server compose env, infra PostgreSQL, Alembic migration | dev, ops, prod-diagnosis | product/dev/ops |
| AccountManagementHome | TG Bot command or callback | SystemUser account count and attention count rendered as inline keyboard home | no durable state | binding wizard, account list, status view, help view | product/dev |
| BotConversationState | TG Bot callback and user text | per-user wizard state for phone/code/password collection | bot_conversation_states | auth flow router | product/dev |
| AuthChallenge | account binding wizard | phone/code/2FA challenge lifecycle and selected DeveloperAppSlot | auth_challenges, bound_tg_accounts, tg_session_slots | account detail, session listener, relay sender | product/dev |

## Flows

| flow_id | source_data | write_path | read_path | downstream | consistency_rules | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| flow-tg-private-incoming-v1 | Telegram private message from bound user session | listener writes RelayMessage, media metadata, BotPushMessage, ReplyMapping | Bot push renderer, reply handler | SystemUser receives Bot notification | incoming idempotency = bound_tg_account_id + source_message_id; media_group sorted by sequence | docs/product/tg-private-relay-v1.md |
| flow-tg-private-reply-v1 | SystemUser replies to BotPushMessage | Bot reply handler writes outgoing result and optional SessionFailoverEvent | ReplyMapping, TgSessionSlot health | Telegram private peer receives reply | outgoing idempotency = bot_reply_message_id; missing mapping fails explicitly | docs/product/tg-private-relay-v1.md |
| flow-tg-auth-session-v1 | phone number, Telegram code, optional 2FA password | auth flow writes encrypted session slots | session selector, health checker | listener and relay sender | sessions belong to BoundTgAccount and DeveloperAppSlot; secrets must not be hardcoded | docs/product/tg-private-relay-v1.md |
| flow-tg-session-failover-v1 | send/listen failure from primary slot | session selector marks failed/degraded slot and writes SessionFailoverEvent | TgSessionSlot health, SessionFailoverEvent | retry with standby_1 then standby_2 | failover is allowed only for session-layer failure; all slots failed means explicit relay failure | docs/product/tg-private-relay-v1.md |
| flow-tg-scaffold-bootstrap-v1 | Product authorization after dev missing-inputs handoff | dev initializes pyproject, source tree, models, repositories, bot/listener/worker entrypoints, and pytest tests | repo files, migrations if used | TG Private Relay V1 implementation | no mocks or fake success; secrets and sessions must be environment/config driven and encrypted at rest | docs/product/tg-private-relay-v1.md |
| flow-tg-release-pgsql-v1 | push to release branch | GitHub Actions runs PostgreSQL-backed checks, builds image, deploys server compose, and runs Alembic migration | infra-compose PostgreSQL via TG_V_CHAT_DATABASE_URL | production runtime and prod-diagnosis | release evidence must use PostgreSQL; no SQLite runtime release; compose joins infra_default; prod-diagnosis only after real release/deploy evidence | docs/product/deploy-postgres-release-plan.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |
| flow-account-management-home-v1 | `/start`, `/admin`, `/accounts`, or `account.home` callback | no write; render account summary and inline keyboard | SystemUser, BoundTgAccount, TgSessionSlot | Account Management home | repeated command only re-renders home; no duplicate state writes | docs/product/account-management-bot-flow-v1.md |
| flow-account-bind-wizard-v1 | `account.bind.start` callback and phone/code/password messages | AuthChallenge lifecycle, BoundTgAccount binding state, encrypted TgSessionSlot | AuthChallenge, BotConversationState, BoundTgAccount, TgSessionSlot | session listener, relay sender, account detail | one active binding wizard per SystemUser; no success before encrypted session commit | docs/product/account-management-bot-flow-v1.md |
| flow-account-list-detail-v1 | `account.list` and `account.detail:{id}` callbacks | no write for reads; disable/reauth actions write only after explicit action | BoundTgAccount, TgSessionSlot, SessionFailoverEvent | user-facing account management, QA, ops evidence | account reads scoped to SystemUser; phone numbers masked in Bot UI | docs/product/account-management-bot-flow-v1.md |

## Update Rule

Product owns product/dataflow meaning. Dev must report implementation changes that require this index to change.

Path note: 原 handoff 路径 `/Users/xida/PycharmProjects/tg-v-caht` 是 typo，实际项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。
