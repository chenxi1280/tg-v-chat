# Product Index

## Scope

This file is the product-side index for requirements, business objects, permissions, states, and acceptance criteria.

## Requirements

| requirement_id | source | decision | status | linked_docs |
| --- | --- | --- | --- | --- |
| req-tg-private-relay-v1 | user | 支持用户通过 TG Bot 绑定最多 20 个 Telegram 个人账号，并中转私聊文字、emoji、图片、sticker；所有绑定账号共享三套 developer app，每账号维护 primary/standby_1/standby_2 session 并自动 failover。原 handoff 路径 `/Users/xida/PycharmProjects/tg-v-caht` 是 typo，实际项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。 | remediation_code_complete_e3_passed; E4 unproven | docs/product/tg-private-relay-v1.md; docs/product/product-acceptance-tg-private-relay-v1.md; docs/product/release-gate-tg-private-relay-v1-blocked-1.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |
| req-tg-private-relay-v1-scaffold | dev-blocked-tg-private-relay-v1 | 当前仓库只有协作文档，没有应用源码；product 授权 dev 在真实目录 `/Users/xida/PycharmProjects/tg-v-chat` 从零初始化真实应用脚手架，推荐 Python 3.11+、Telethon、SQLAlchemy/Alembic、pytest、环境变量注入密钥。 | resync_ready | docs/product/tg-private-relay-v1.md |
| req-tg-private-relay-v1-pgsql-release | user | 数据库明确使用 PostgreSQL；部署参考 `tg-yunying`，通过 GitHub Actions 从 `release` 分支发布，运行时数据库使用 infra-compose 中的 PostgreSQL。dev 已补齐 PostgreSQL、Alembic、GitHub Actions、Docker/compose/env 的文件级 release engineering；release gate 仍等待真实 release actions 和 E4 生产证据。 | release_engineering_ready_actions_pending | docs/product/deploy-postgres-release-plan.md; docs/product/release-gate-tg-private-relay-v1-blocked-1.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |
| req-account-management-bot-flow-v1 | user | `/start` 和 `/admin` 必须进入“账号管理”首页，并通过按钮引导绑定 TG 账号、查看账号、查看授权状态、中转说明和帮助；绑定按手机号、验证码、可选 2FA 分步完成。 | remediation_code_complete_e3_passed; E4 unproven | docs/product/account-management-bot-flow-v1.md; docs/product/tg-private-relay-v1.md |
| req-native-forward-relay-v2 | user | 入站文字、图片、视频、圆形视频、音频文件、语音和 sticker 必须在当前 Bot 对话中显示 Telegram 原生“转发自”来源；同一接收账号、同一私聊对象的连续消息可批量转发，每条最终消息独立建立 ReplyMapping。真实 Telegram 账号唯一归属、可由 user session 解析的 Bot username、bridge 超时、预创建投递账本和显式 V2 灰度开关是该需求的不可省略合同；禁止在原生转发失败时静默复制。 | implementation_e3_passed; E4 unproven | docs/product/native-forward-relay-v2.md; docs/superpowers/specs/2026-07-17-native-forward-relay-design.md; docs/superpowers/plans/2026-07-17-native-forward-relay-plan.md; docs/superpowers/plans/2026-07-17-native-forward-relay-activation-release-plan.md |

## Business Objects

| object | states | permissions | pages | APIs_or_workers | notes |
| --- | --- | --- | --- | --- | --- |
| SystemUser | active, disabled | 只能管理自己的绑定账号和回复映射 | TG Bot | bot update handler | 产品用户。 |
| DeveloperAppSlot | primary, standby_1, standby_2 | 仅系统配置可维护 | none | session selector | 全局共享三套 Telegram developer app。 |
| BoundTgAccount | binding, active, degraded, reauth_required, disabled | 归属 SystemUser | TG Bot binding flow | auth flow, session listener | primary usable 为 active；primary 不可用但 standby 可用为 degraded；全部已授权槽不可用为 reauth_required；未授权 standby 不触发降级。授权事务先持久化真实 `telegram_user_id` 再激活；V2 要求非空值全局唯一，一个真实账号不能归属两个 SystemUser；删除后释放 identity，旧 listener refresh 不得回填删除行。 |
| TgSessionSlot | active, standby, failed, expired, revoked | 归属 BoundTgAccount | TG Bot status view | session health checker, failover worker | 每绑定账号三槽位。 |
| RelayMessage | received, pushed, reply_pending, sent, failed | SystemUser 仅可处理自己的消息 | TG Bot private relay | listener, relay service | 私聊中转主记录。 |
| BotPushMessage | pending, sending, sent, failed, uncertain | 归属 SystemUser | TG Bot private relay | bot push service | Bot durable dispatch，供 ReplyMapping 反查。 |
| ReplyMapping | active, expired | SystemUser 仅可按 reply 使用自己的映射 | TG Bot reply | bot reply handler | disable/delete/显式失效写 invalidated_at；未引入 time-based TTL。 |
| SessionFailoverEvent | switched, exhausted | 系统内部记录 | none | session selector, ops evidence | 自动切换证据。 |
| ApplicationScaffold | initialized, blocked | dev 可初始化真实应用；不得创建 mock success | repo | main, bot handlers, listeners, workers, repositories, tests | Python/Telethon 优先；无现有源码可兼容。 |
| ReleaseDeployment | blocked, engineering_ready, actions_pending, deployed | 仅 owner/ops 可提供生产密钥和发布目标 | GitHub Actions, server compose | release branch workflow, GHCR image, infra-compose PostgreSQL | 按 `tg-yunying` 模式；数据库必须是 PostgreSQL；当前文件级 release engineering ready，真实 release run/deploy/E4 pending。 |
| AccountManagementHome | empty, normal, attention_required | SystemUser 只能看到自己的账号摘要 | TG Bot account management home | bot command router, callback router, account summary query | `/start`、`/admin`、`/accounts` 的首屏。 |
| BotConversationState | home, awaiting_phone, awaiting_code, awaiting_password, account_detail | 归属 SystemUser；只能驱动自己的绑定流程 | TG Bot binding wizard | bot state resolver, auth flow | 每个 SystemUser 同时只能有一个主动绑定向导。 |
| AuthChallenge | code_required, password_required, complete, cancelled, expired | 归属 SystemUser 的 BoundTgAccount | TG Bot binding wizard | auth service, Telegram authenticator | 手机号、验证码、2FA 分步授权状态。 |
| NativeForwardBatch | collecting, sealed, bridge_sending, awaiting_bot, final_sending, sent, failed, uncertain | 归属 SystemUser 和 BoundTgAccount | TG Bot native relay | listener collector, Bot forward bridge, reconciliation service | 同一账号、同一 peer 的两跳原生转发批次；不跨 peer 合并。bridge 以真实 `telegram_user_id` 串行，并有 deadline，超时进入 uncertain；账号禁用/删除会在同一 identity lock 内终止非终态批次，避免重建 ReplyMapping。 |
| NativeForwardItem | pending, bridged, sent, failed, uncertain | 归属 NativeForwardBatch | TG Bot native relay | Bot forward bridge, ReplyMapping writer | 一条 RelayMessage 对应一个最终 Bot message id；`batch_sequence` 是批次内唯一顺序，不能复用媒体 sequence；第一跳只确认条数，实际 `bridge_message_id` 按 Bot sender/private-chat scope 唯一持久化。 |
| ForwardBridgeMarker | pending, received, consumed | 归属 NativeForwardBatch | none | user-session forwarder, Bot forward bridge | token、sender、count 三项均匹配才能激活。 |
| NativeForwardBridgeQuarantine | recorded | 系统内部审计；不创建用户回复映射 | none | Bot forward bridge, QA, ops | 已知绑定账号 sender 的错误 marker、orphan 或超过批次条数的 forwarded item 只记录 sender、Bot message id、token 和 failure code，不保存原内容，且绝不进入普通 Bot router。 |

## Acceptance Contracts

| contract_id | level | evidence_required | qa_required | production_verification_required |
| --- | --- | --- | --- | --- |
| accept-tg-private-relay-v1 | L2 | hardening E3 passed; E4 unproven | true | true |
| product-accept-tg-private-relay-v1 | L2 | remediation E3 passed; E4 unproven | pending | blocked_until_release_actions |
| accept-account-management-bot-flow-v1 | L2 | hardening E3 passed; E4 unproven | true | true |
| accept-native-forward-relay-v2 | L2 | 修订设计已覆盖身份唯一性、batch_sequence、超时、预创建投递账本、串行 bridge 和灰度回填；实现与 E4 仍 unproven | true | true |
