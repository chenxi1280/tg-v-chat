# TG Private Relay V1 Product Design Complete

- message_id: product-design-tg-private-relay-v1
- intake_id: intake-tg-private-relay-001
- from_agent: product
- to_agent: dev
- related_version: tg-private-relay-v1
- actual_project_path: `/Users/xida/PycharmProjects/tg-v-chat`
- path_correction: 原 handoff 路径 `/Users/xida/PycharmProjects/tg-v-caht` 是 typo，实际项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。
- level: L2
- evidence_level: E1
- design_status: complete
- ready_status: ready
- done_status: not_done
- release_gate: pending
- production_verification_required: true
- dev_handoff_ready: true
- dev_handoff_status: sent
- dev_thread_id: 019f0a3a-bc28-7480-a612-28fa45d81fe6
- dev_ack_status: complete

## Intake Card

- source: user
- received_at: 2026-06-28
- raw_request: 用户在 TG Bot 上绑定多个 Telegram 个人账号 user session；绑定账号收到私聊后通过 Bot 推送给用户；用户回复哪条 Bot 消息，就通过对应绑定账号回复原私聊对象；支持图片、表情、文字；为防止账号掉线，支持 1 主 2 从 developer app session。
- suspected_type: feature
- affected_surface: TG Bot, Telegram user session, private chat relay, media relay, session failover
- user_visible: true
- production_related: true
- initial_evidence_level: E1
- owner_agent: product

## Triage Card

- proposed_level: L2
- cost_tier: standard_team
- ready_status: ready
- route: product -> dev -> qa -> product -> prod-diagnosis
- validation_required: true
- production_verification_required: true
- release_gate: pending
- escalation_triggers: 账号授权不可用、session 数据丢失、消息错发、媒体丢失、standby 全部失效、生产发送失败率异常。

## Original Requirement Coverage

| user_requirement | product_decision | functional_design | backend_design | dataflow_design | qa_acceptance | status |
| --- | --- | --- | --- | --- | --- | --- |
| 绑定多个 TG 账号 | 每个系统用户最多 20 个 Telegram 个人账号 | Bot 内完成绑定和列表管理 | 绑定账号、session 槽位、授权状态持久化 | SystemUser -> BoundTgAccount -> TgSessionSlot | 可绑定、查看、禁用，超过 20 个拒绝 | covered |
| 私聊通过 Bot 推送 | V1 只支持私聊，不支持群/频道 | 收到私聊后推送给绑定用户 | user session 监听私聊事件 | IncomingPrivateMessage -> RelayMessage -> BotPushMessage | 文本、图片、emoji、sticker 均能推送 | covered |
| 回复哪条就回哪条 | 以 Bot reply_to_message 反查原始私聊 | 用户必须 reply Bot 推送消息 | ReplyMapping 保存 bot_message_id 到原私聊上下文 | BotReply -> ReplyMapping -> Telegram send | 错误回复方式给出明确失败提示 | covered |
| 图片、表情、文字 | 支持文字、普通 emoji、图片、Telegram sticker | 图片相册保留顺序 | media_group_id 和 sequence 记录顺序 | MediaGroup ordered relay | 相册顺序一致，sticker 可回传 | covered |
| 1 主 2 从 session | 所有绑定账号共享三套 developer app；每账号三 session 槽 | primary 失败自动切 standby | SessionSelector 选择 healthy slot | SessionHealth -> failover event -> send | primary 失败自动切换并记录事件 | covered |
| 手机验证码和 2FA | 授权流程支持 phone code 与 2FA password | Bot 分步引导授权 | AuthChallenge 状态机 | auth_start -> code -> password -> active | 验证码/2FA 成功和失败都可见 | covered |

## Scope

### In Scope

- Telegram 个人账号 user session 绑定，不使用 Bot token 代替个人账号。
- 每个系统用户最多绑定 20 个 Telegram 个人账号。
- 所有绑定账号共享三套 Telegram developer application 配置，分别作为 primary、standby_1、standby_2。
- 每个绑定账号在三套 developer application 下各维护一个 session 槽位。
- primary session 异常时自动切换到 standby_1，再切换到 standby_2。
- 监听绑定账号收到的私聊文本、普通 emoji、图片、Telegram sticker。
- 通过 Bot 推送私聊消息给系统用户，并保存回复映射。
- 用户 reply Bot 推送消息时，通过对应绑定账号回复原私聊对象。
- 图片相册按原始顺序推送和回复。
- 授权流程支持手机号验证码和 2FA 密码。
- 所有收发、授权、failover、失败必须显式记录。

### Out Of Scope

- 群聊、频道、话题消息中转。
- 语音、视频、文件、GIF、联系人、位置等非 V1 媒体。
- 多客服分配、标签、CRM、自动回复。
- 自动养号或规避平台风控策略。
- 端到端加密承诺。

## Product Model

| object | key_states | notes |
| --- | --- | --- |
| SystemUser | active, disabled | 使用本产品 Bot 的用户。 |
| DeveloperAppSlot | primary, standby_1, standby_2 | 全局三套 Telegram developer app 配置。 |
| BoundTgAccount | binding, active, degraded, reauth_required, disabled | 用户绑定的 Telegram 个人账号。 |
| TgSessionSlot | active, standby, failed, expired, revoked | 每个绑定账号在某个 DeveloperAppSlot 下的 session。 |
| BotConversationState | awaiting_phone, awaiting_code, awaiting_password | Bot 内账号绑定向导的用户级状态。 |
| PrivateChatPeer | known, blocked, unavailable | 原私聊对象。 |
| RelayMessage | received, pushed, reply_pending, sent, failed | 私聊中转消息。 |
| BotPushMessage | pushed, failed | Bot 推送给 SystemUser 的消息。 |
| ReplyMapping | active, expired, missing | Bot 推送消息到原私聊上下文的映射。 |
| SessionFailoverEvent | switched, exhausted | session 自动切换事件。 |

## Functional Design

- Bot 提供绑定入口、账号列表、解绑/禁用入口、授权状态查看入口。
- 绑定流程按手机号、验证码、2FA 密码分步推进。
- 收到私聊后，Bot 推送内容必须包含绑定账号标识、私聊对象标识、消息内容和媒体。
- 用户回复必须使用 Telegram reply 操作；非 reply 输入不进入代发链路。
- 发送时优先使用 primary session；失败后按 standby_1、standby_2 顺序自动尝试。
- 自动切换只针对 session 不可用、授权失效、连接失败等 session 层失败；业务拒绝或 peer 不可达不伪装为 session failover 成功。

## Backend / API / Worker Design

- affected_services: bot update handler, user session listener, auth flow service, relay mapping service, session selector, media relay service.
- affected_workers: Telegram user session listener workers, retry/failover worker, session health checker.
- data_models: SystemUser, DeveloperAppSlot, BoundTgAccount, TgSessionSlot, RelayMessage, BotPushMessage, ReplyMapping, SessionFailoverEvent.
- idempotency: incoming message 使用 bound_tg_account_id + source_message_id 去重；outgoing reply 使用 bot_reply_message_id 去重。
- concurrency: 同一绑定账号的 session failover 需要串行化；同一 media_group 内按 sequence 顺序发送。
- failure_handling: 授权失败、session 全部不可用、映射缺失、媒体下载失败、发送失败均返回明确错误并记录。

## Dataflow Design

### Incoming Private Message

1. TgSessionSlot listener receives private message.
2. Resolve BoundTgAccount, PrivateChatPeer, source_message_id, media_group_id, and sequence.
3. Write RelayMessage and media metadata.
4. Send BotPushMessage to SystemUser.
5. Write ReplyMapping from bot_message_id to relay context.
6. Use incoming idempotency key: bound_tg_account_id + source_message_id.

### User Reply

1. Bot receives SystemUser reply.
2. Read reply_to_message_id and resolve ReplyMapping.
3. Select healthy TgSessionSlot by primary, standby_1, standby_2 order.
4. Send text, emoji, image, or sticker to PrivateChatPeer.
5. Write send result and failover events.
6. Use outgoing idempotency key: bot_reply_message_id.
7. Missing ReplyMapping, exhausted sessions, media failure, and unavailable peer must return explicit failure.

### Auth Flow

1. User starts binding with phone number.
2. System sends Telegram code through selected DeveloperAppSlot.
3. User submits code.
4. If 2FA is required, user submits password.
5. Persist encrypted session and mark BoundTgAccount active when at least primary is valid.

### Session Failover

1. SessionSelector attempts primary first.
2. On session-layer failure, mark slot failed or degraded with reason.
3. Try standby_1, then standby_2.
4. Write SessionFailoverEvent for each switch.
5. If all slots fail, mark relay send failed and return explicit error to the user.

## QA Acceptance

- Bind one account through phone code only.
- Bind one account requiring 2FA password.
- Reject binding the 21st account for one SystemUser.
- Receive private text and emoji, then reply through Bot and confirm original peer receives it.
- Receive private image and image album; verify album order is preserved.
- Receive and reply Telegram sticker.
- Simulate primary session failure and verify standby_1 is used with SessionFailoverEvent.
- Simulate primary and standby_1 failure and verify standby_2 is used.
- Simulate all session slots failure and verify no fake success is returned.
- Reply without replying to a pushed Bot message and verify explicit error.
- Duplicate incoming update must not create duplicate Bot pushes.
- Duplicate Bot reply update must not double-send.

## Release And Production Verification

- release_gate_required: true
- production_verification_required: true
- evidence_required: E3 before release, E4 after release.
- rollback_considerations: disable listeners and outbound relay feature flag; keep existing sessions stored but stop processing new relay messages.

## Dev Handoff

- handoff_delivery_status: sent
- target_thread_id: 019f0a3a-bc28-7480-a612-28fa45d81fe6
- ack_status: complete
- locked_paths: repo implementation files, docs/index/project-structure.md
- merge_owner: dev
- affected_business_objects: SystemUser, DeveloperAppSlot, BoundTgAccount, TgSessionSlot, PrivateChatPeer, RelayMessage, BotPushMessage, ReplyMapping, SessionFailoverEvent
- affected_pages: TG Bot command and conversation surfaces
- affected_api_or_worker_flows: auth flow, user session listener, bot reply handler, media relay, session health/failover
- index_updates: updated

## Product Decision: Scaffold Authorization

- decision_message_id: product-resync-tg-private-relay-v1-scaffold
- reply_to_message_id: dev-blocked-tg-private-relay-v1
- decision_status: complete
- reason: Dev verified that the actual repository contains collaboration docs only and no application scaffold. To avoid fake implementation or silent fallback, product authorizes dev to initialize a real application scaffold inside `/Users/xida/PycharmProjects/tg-v-chat`.
- runtime: Python 3.11+
- telegram_client_library: Telethon
- bot_runtime: Telethon bot client or aiogram if dev finds a clearer separation; the chosen bot framework must support reply_to_message_id and media/sticker handling.
- dependency_manifest: `pyproject.toml`
- application_entrypoints:
  - `src/tg_v_chat/main.py` for process bootstrap.
  - `src/tg_v_chat/bot/` for Bot command and reply handlers.
  - `src/tg_v_chat/telegram/` for user session clients and listeners.
  - `src/tg_v_chat/workers/` for listener and health/failover worker loops.
- storage_plan: SQLAlchemy models and repository layer backed by PostgreSQL for runtime/release, with Alembic or equivalent explicit migrations before release.
- runtime_database: PostgreSQL via `TG_V_CHAT_DATABASE_URL`; SQLite is not allowed as the deployed/runtime database.
- infra_database: use the existing infra-compose PostgreSQL database/network, following the `tg-yunying` deployment pattern.
- session_persistence: encrypted session storage using an environment-provided encryption key; never hardcode API credentials, Bot token, phone numbers, 2FA passwords, or session strings.
- developer_app_config: three shared DeveloperAppSlot records/config entries, mapped to primary, standby_1, standby_2.
- test_runner: pytest.
- verification_commands:
  - `python -m pytest`
  - project lint/type checks if dev introduces them in the dependency manifest.
- product_boundary: This authorizes application scaffold initialization and feature implementation for TG Private Relay V1 only. It does not authorize mocks, fake success paths, unrelated CRM/multi-agent features, or production deployment.

## Product Decision: PostgreSQL And Release Deployment

- decision_message_id: product-resync-tg-private-relay-v1-pgsql-release
- decision_status: complete
- database_engine: PostgreSQL
- runtime_database_url_env: `TG_V_CHAT_DATABASE_URL`
- release_branch: `release`
- deploy_mechanism: GitHub Actions, following `tg-yunying/.github/workflows/deploy-production.yml`
- database_source: existing infra-compose PostgreSQL, not an app-owned database container
- server_compose_pattern: follow `tg-yunying/docker-compose.server.yml` with service attached to `infra_default`
- migration_requirement: Alembic or equivalent explicit migration workflow must exist before release gate can pass
- release_gate_status: blocked until PostgreSQL CI, Docker/GHCR image build, server compose, migration workflow, worker runtime, env wiring, and E4 verification route exist

## Product Decision: Account Management Bot Flow

- decision_message_id: product-resync-account-management-bot-flow-v1
- decision_status: complete
- source_request: 用户指出当前 `/start` 只回复在线状态的逻辑错误；`/start` 应进入“账号管理”，并通过按钮引导绑定要管理的 TG 账号。
- linked_prd: `docs/product/account-management-bot-flow-v1.md`
- product_decision:
  - `/start` 和 `/admin` 的第一响应必须是“账号管理”首页。
  - Bot 必须使用 inline keyboard 引导绑定、账号列表、授权状态、中转说明和帮助。
  - 绑定流程必须按手机号、验证码、可选 2FA 分步推进，每一步可取消或返回首页。
  - 账号列表和账号详情必须展示 session 状态，并提供重新授权、补充备用授权、禁用账号等动作。
  - 所有授权失败和状态异常必须显式展示，不允许假成功或静默降级。
- affected_api_or_worker_flows: bot command routing, callback query routing, auth flow, account list/detail rendering, session status display
- dataflow_index_update: updated
- project_structure_index_update: updated
- product_index_update: updated
