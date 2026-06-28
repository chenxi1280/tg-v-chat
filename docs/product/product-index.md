# Product Index

## Scope

This file is the product-side index for requirements, business objects, permissions, states, and acceptance criteria.

## Requirements

| requirement_id | source | decision | status | linked_docs |
| --- | --- | --- | --- | --- |
| req-tg-private-relay-v1 | user | 支持用户通过 TG Bot 绑定最多 20 个 Telegram 个人账号，并中转私聊文字、emoji、图片、sticker；所有绑定账号共享三套 developer app，每账号维护 primary/standby_1/standby_2 session 并自动 failover。原 handoff 路径 `/Users/xida/PycharmProjects/tg-v-caht` 是 typo，实际项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。 | product_accepted_e3_release_blocked_until_actions | docs/product/tg-private-relay-v1.md; docs/product/product-acceptance-tg-private-relay-v1.md; docs/product/release-gate-tg-private-relay-v1-blocked-1.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |
| req-tg-private-relay-v1-scaffold | dev-blocked-tg-private-relay-v1 | 当前仓库只有协作文档，没有应用源码；product 授权 dev 在真实目录 `/Users/xida/PycharmProjects/tg-v-chat` 从零初始化真实应用脚手架，推荐 Python 3.11+、Telethon、SQLAlchemy/Alembic、pytest、环境变量注入密钥。 | resync_ready | docs/product/tg-private-relay-v1.md |
| req-tg-private-relay-v1-pgsql-release | user | 数据库明确使用 PostgreSQL；部署参考 `tg-yunying`，通过 GitHub Actions 从 `release` 分支发布，运行时数据库使用 infra-compose 中的 PostgreSQL。dev 已补齐 PostgreSQL、Alembic、GitHub Actions、Docker/compose/env 的文件级 release engineering；release gate 仍等待真实 release actions 和 E4 生产证据。 | release_engineering_ready_actions_pending | docs/product/deploy-postgres-release-plan.md; docs/product/release-gate-tg-private-relay-v1-blocked-1.md; docs/product/release-gate-recheck-tg-private-relay-v1-pgsql-1.md |

## Business Objects

| object | states | permissions | pages | APIs_or_workers | notes |
| --- | --- | --- | --- | --- | --- |
| SystemUser | active, disabled | 只能管理自己的绑定账号和回复映射 | TG Bot | bot update handler | 产品用户。 |
| DeveloperAppSlot | primary, standby_1, standby_2 | 仅系统配置可维护 | none | session selector | 全局共享三套 Telegram developer app。 |
| BoundTgAccount | binding, active, degraded, reauth_required, disabled | 归属 SystemUser | TG Bot binding flow | auth flow, session listener | 每用户最多 20 个。 |
| TgSessionSlot | active, standby, failed, expired, revoked | 归属 BoundTgAccount | TG Bot status view | session health checker, failover worker | 每绑定账号三槽位。 |
| RelayMessage | received, pushed, reply_pending, sent, failed | SystemUser 仅可处理自己的消息 | TG Bot private relay | listener, relay service | 私聊中转主记录。 |
| BotPushMessage | pushed, failed | 归属 SystemUser | TG Bot private relay | bot push service | Bot 推送消息，供 ReplyMapping 反查。 |
| ReplyMapping | active, expired, missing | SystemUser 仅可按 reply 使用自己的映射 | TG Bot reply | bot reply handler | 防止错发到错误 peer。 |
| SessionFailoverEvent | switched, exhausted | 系统内部记录 | none | session selector, ops evidence | 自动切换证据。 |
| ApplicationScaffold | initialized, blocked | dev 可初始化真实应用；不得创建 mock success | repo | main, bot handlers, listeners, workers, repositories, tests | Python/Telethon 优先；无现有源码可兼容。 |
| ReleaseDeployment | blocked, engineering_ready, actions_pending, deployed | 仅 owner/ops 可提供生产密钥和发布目标 | GitHub Actions, server compose | release branch workflow, GHCR image, infra-compose PostgreSQL | 按 `tg-yunying` 模式；数据库必须是 PostgreSQL；当前文件级 release engineering ready，真实 release run/deploy/E4 pending。 |

## Acceptance Contracts

| contract_id | level | evidence_required | qa_required | production_verification_required |
| --- | --- | --- | --- | --- |
| accept-tg-private-relay-v1 | L2 | E3 before release, E4 after release | true | true |
| product-accept-tg-private-relay-v1 | L2 | E3 product acceptance passed; release engineering files ready; E4 unproven | qa_pass accepted | blocked_until_release_actions |
