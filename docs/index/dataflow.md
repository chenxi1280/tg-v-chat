# Dataflow Index

## Business Objects

| object | source | transformations | storage | consumers | owner |
| --- | --- | --- | --- | --- | --- |
| BoundTgAccount | TG Bot binding flow | phone/code/2FA 授权后生成 session 槽位，并保存 display_name / username 作为接收账号身份 | bound_tg_accounts, tg_session_slots | session listener, session selector, Bot push renderer | product/dev |
| RelayMessage | Telegram user session listener | 私聊消息归一化为文字、emoji、图片、sticker 元数据，推送时补齐接收账号名和 `@username` | relay_messages, bot_push_messages, reply_mappings | Bot push, Bot reply handler, QA | product/dev |
| ReplyMapping | BotPushMessage creation | system_user_id + bot_message_id 映射到 bound_tg_account_id、peer_id、source_message_id、media context | reply_mappings | Bot reply handler | product/dev |
| SessionFailoverEvent | session selector | primary -> standby_1 -> standby_2 自动切换记录 | session_failover_events | ops, QA, product acceptance | product/dev |
| ApplicationScaffold | product scaffold authorization | Python/Telethon app bootstrap, SQLAlchemy repositories, worker entrypoints, pytest verification | repo source tree and dependency manifest | dev, qa | product/dev |
| ReleaseDeployment | product deploy decision | release branch -> GitHub Actions checks/build/deploy -> server compose on infra network | GHCR image, server compose env, infra PostgreSQL, Alembic migration | dev, ops, prod-diagnosis | product/dev/ops |
| AccountManagementHome | TG Bot command or callback | SystemUser account count and attention count rendered as inline keyboard home | no durable state | binding wizard, account list, status view, help view | product/dev |
| BotConversationState | TG Bot callback and user text | per-user wizard state for phone/code/password collection | bot_conversation_states | auth flow router | product/dev |
| AuthChallenge | account binding wizard | phone/code/2FA challenge lifecycle and selected DeveloperAppSlot | auth_challenges, bound_tg_accounts, tg_session_slots | account detail, session listener, relay sender | product/dev |
| DurableDispatch | incoming Bot push, incoming album batch, or outgoing reply | persist pending before I/O, sending before remote call, then a durable terminal result | bot_push_messages, incoming album batches, outgoing replies | relay recovery, QA, ops | product/dev |
| MediaSpoolArtifact | Telegram media download or Bot reply upload | owner-only staging, atomic ready, delivery result, explicit release | shared `TG_V_CHAT_MEDIA_ROOT` | listener, Bot push, relay sender, cleanup worker | product/dev |
| ListenerBinding | operational BoundTgAccount plus selected usable session slot | account status and slot revision become a non-secret listener fingerprint | in-memory listener client registry | Telethon user-session listeners | dev/ops |
| RoleHeartbeat | connected bot/listener/worker process cycle | role process writes timestamp after real readiness/cycle evidence | `TG_V_CHAT_HEARTBEAT_ROOT` files | container healthcheck, ops | dev/ops |

## Flows

| flow_id | source_data | write_path | read_path | downstream | consistency_rules | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| flow-tg-private-incoming-v1 | Telegram private message from bound user session | listener stages media, persists BotPushMessage or album batch dispatch, sends to Bot, then creates ReplyMapping only for real Bot message ids | BoundTgAccount identity, Bot push renderer, reply handler | SystemUser receives Bot notification with sender,接收账号名,接收用户名, time, content | incoming idempotency = bound_tg_account_id + source_message_id; media_group sorted by sequence; unsupported media fails with no fake push/mapping | docs/product/tg-private-relay-v1.md |
| flow-tg-private-reply-v1 | SystemUser replies to BotPushMessage | Bot reply handler persists outgoing dispatch, sends through selected slot, and records terminal result plus optional SessionFailoverEvent | active ReplyMapping, BoundTgAccount, TgSessionSlot health | Telegram private peer receives reply | lookup = system_user_id + bot_message_id; idempotency = system_user_id + bot_reply_message_id; uncertain is not retried | docs/product/tg-private-relay-v1.md |
| flow-tg-auth-session-v1 | phone number, Telegram code, optional 2FA password for an explicit account and slot | auth flow independently writes one encrypted session slot selected by callback | session selector, health checker | listener and relay sender | primary/standby_1/standby_2 use their own DeveloperAppSlot authorization; never copy primary session | docs/product/tg-private-relay-v1.md; docs/product/account-management-bot-flow-v1.md |
| flow-tg-session-failover-v1 | classified SessionFailure from the current slot | session selector marks failed/degraded slot and writes SessionFailoverEvent | TgSessionSlot health, SessionFailoverEvent | retry with standby_1 then standby_2 | only SessionFailure permits failover; DeliveryFailure records delivery failure and leaves session health unchanged | docs/product/tg-private-relay-v1.md |
| flow-reply-mapping-invalidation-v1 | account disable, account delete, or explicit mapping invalidation | change active ReplyMapping to expired and write invalidated_at | ReplyMapping by BoundTgAccount | Bot reply handler | no time-based TTL producer; expired mapping cannot send | docs/product/tg-private-relay-v1.md |
| flow-tg-scaffold-bootstrap-v1 | Product authorization after dev missing-inputs handoff | dev initializes pyproject, source tree, models, repositories, bot/listener/worker entrypoints, and pytest tests | repo files, migrations if used | TG Private Relay V1 implementation | no mocks or fake success; secrets and sessions must be environment/config driven and encrypted at rest | docs/product/tg-private-relay-v1.md |
| flow-tg-release-pgsql-v1 | push to release branch | GitHub Actions runs PostgreSQL-backed checks, builds image, deploys server compose, and runs Alembic migration | infra-compose PostgreSQL via TG_V_CHAT_DATABASE_URL | production runtime and prod-diagnosis | release evidence must use PostgreSQL; no SQLite runtime release; compose joins infra_default; prod-diagnosis only after real release/deploy evidence | docs/product/deploy-postgres-release-plan.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |
| flow-account-management-home-v1 | `/start`, `/admin`, `/accounts`, or `account.home` callback | no write; render account summary and inline keyboard | SystemUser, BoundTgAccount, TgSessionSlot | Account Management home | repeated command only re-renders home; no duplicate state writes | docs/product/account-management-bot-flow-v1.md |
| flow-account-bind-wizard-v1 | `account.bind.start` callback and phone/code/password messages | AuthChallenge lifecycle, BoundTgAccount binding state, encrypted TgSessionSlot, display_name, username | AuthChallenge, BotConversationState, BoundTgAccount, TgSessionSlot | session listener, relay sender, account detail | one active binding wizard per SystemUser; no success before encrypted session commit and identity save | docs/product/account-management-bot-flow-v1.md |
| flow-account-list-detail-v1 | `account.list` and `account.detail:{id}` callbacks | no write for reads; disable/reauth actions write only after explicit action | BoundTgAccount, TgSessionSlot, SessionFailoverEvent | user-facing account management, QA, ops evidence | account reads scoped to SystemUser; display_name and username are primary identity; phone numbers masked in detail | docs/product/account-management-bot-flow-v1.md |
| flow-listener-reconciliation-v1 | operational active/degraded accounts and usable session slots | listener selects primary first, otherwise standby; changed account/slot/revision fingerprints reconnect; removed accounts disconnect | BoundTgAccount, TgSessionSlot revision, in-memory clients | incoming private message handlers | one account startup failure cannot stop other accounts; fingerprints contain no session plaintext | docs/product/tg-private-relay-v1.md |
| flow-session-health-worker-v1 | authorized session slots and terminal media artifacts | worker verifies session authorization, recomputes account status, and retries terminal artifact release | TgSessionSlot, BoundTgAccount, relay_media_artifacts, media spool | listener/sender slot selection, health evidence | unauthorized -> revoked; transport uncertainty does not change auth; sent/failed media can release, uncertain retained | docs/product/tg-private-relay-v1.md |
| flow-role-health-v1 | bot/listener/worker process readiness or completed work cycle | role process writes heartbeat; healthcheck verifies PostgreSQL and fresh role heartbeat | heartbeat files under `TG_V_CHAT_HEARTBEAT_ROOT` | Docker healthcheck | stale or missing heartbeat fails even when DB is healthy | docker-compose.server.yml |

## Relay Consistency Contracts

```text
pending -> sending -> sent | failed | uncertain
staging -> ready -> sent -> released
staging | ready -> failed -> released
```

- Outgoing dispatch, BotPushMessage dispatch, and Incoming album batch dispatch all use the durable dispatch lifecycle above.
- A remote success followed by a database update failure becomes `uncertain`; uncertain is never retried automatically because doing so can duplicate the remote side effect.
- Only SessionFailure advances to another slot. DeliveryFailure leaves session health unchanged and records only the delivery result.
- All roles share the owner-only `TG_V_CHAT_MEDIA_ROOT`; spool artifacts use the media lifecycle above. Cleanup releases `sent` and deterministic `failed` artifacts only after terminal metadata is durable; artifacts tied to `pending`, `sending`, or `uncertain` dispatch remain retained.
- Account status derives from authorized slots: primary usable is active; otherwise any usable standby is degraded; all authorized slots unusable is reauth_required. Disabled/deleted accounts are not recomputed, and a never-authorized standby does not degrade a usable primary.
- Listener bindings use non-secret fingerprints `(account_id, slot_id, developer_slot, session_revision)`; encrypted or plaintext session strings are never logged as identity.
- Runtime health is role-specific: database connectivity alone is insufficient for bot/listener/worker health.

## Update Rule

Product owns product/dataflow meaning. Dev must report implementation changes that require this index to change.

Path note: 原 handoff 路径 `/Users/xida/PycharmProjects/tg-v-caht` 是 typo，实际项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。
