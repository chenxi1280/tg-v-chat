# Validation Plan

## Validation Matrix

| message_id | intake_id | bug_id | level | expected_behavior | evidence_required | result |
| --- | --- | --- | --- | --- | --- | --- |
| relay-hardening-dispatch | intake-tg-private-relay-001 | none | E3/E4 | Outgoing、BotPushMessage、入站 album batch 均持久化 pending/sending/sent/failed/uncertain；remote success 后 DB 更新失败进入 uncertain 且不自动重复 | E3 自动化状态/事务测试；E4 真实 Telegram 与生产记录 | unproven |
| relay-hardening-failure-split | intake-tg-private-relay-001 | none | E3/E4 | 仅 SessionFailure failover；DeliveryFailure 不改变 session health | E3 分类与 failover 测试；E4 生产错误分类证据 | unproven |
| relay-hardening-mapping | intake-tg-private-relay-001 | none | E3/E4 | ReplyMapping 仅 active/expired；disable/delete/显式失效写 invalidated_at；不引入 time-based TTL | E3 repository/service 测试；E4 生产失效证据 | unproven |
| relay-hardening-slots | intake-tg-account-management-001 | none | E3/E4 | primary/standby_1/standby_2 独立授权且绝不复制 primary session | E3 callback/auth persistence 测试；E4 三套真实授权 | unproven |
| relay-hardening-account-state | intake-tg-account-management-001 | none | E3/E4 | primary 可用为 active；仅 standby 可用为 degraded；全部已授权槽不可用为 reauth_required；未授权 standby 不降级 | E3 状态推导测试；E4 真实 session health | unproven |
| relay-hardening-media | intake-tg-private-relay-001 | none | E3/E4 | TG_V_CHAT_MEDIA_ROOT artifact 按 staging/ready/sent/failed/released 流转；unsupported media 显式失败且不创建假 push/mapping | E3 media store/listener 测试；E4 真实图片/sticker/album | unproven |
| relay-hardening-listener | intake-tg-private-relay-001 | none | E3/E4 | listener reconciliation 隔离单账号启动失败，slot/revision 变化重连，disabled/deleted 断开，degraded 账号可用 standby 监听 | E3 listener reconciliation 测试；E4 多账号真实 listener 运行证据 | unproven |
| relay-hardening-worker | intake-tg-private-relay-001 | none | E3/E4 | worker 真循环执行 session health、账号状态重算、terminal media release retry；网络不确定不污染授权 | E3 session health worker 测试；E4 生产 worker heartbeat 和 DB 状态变化证据 | unproven |
| relay-hardening-role-health | intake-tg-private-relay-001 | none | E3/E4 | bot/listener/worker healthcheck 必须同时满足 PostgreSQL 和 fresh role heartbeat | E3 role health 测试和 compose config；E4 Docker health status | unproven |
| relay-hardening-scope | intake-tg-private-relay-001 | none | E3/E4 | 不扩群/频道/其他媒体，不新增静默大小上限，不宣称 exactly-once | PRD 合同扫描；E4 保持 unproven | unproven |

## Evidence Boundary

- Local and CI evidence can prove only E3。
- real Telegram and production remain E4 unproven，直到真实账号、真实媒体、真实发送结果和生产运行证据完成验收。
- 本轮实现证据可在交付汇报中标为 E3 通过；矩阵结果仍保持 `unproven`，不得用文档或本地通过替代 E4。

## QA Rules

- Output `qa_pass`, `failed`, `blocked`, or `unproven`.
- Do not accept unclear success evidence.
- `qa_pass` must be handed to product for Product Acceptance.
- `failed` must be handed back to dev with exact repro and evidence.
