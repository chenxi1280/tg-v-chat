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
| native-forward-identity-gate | req-native-forward-relay-v2 | none | E3/E4 | 非空 BoundTgAccount.telegram_user_id 全局唯一；授权事务先保存 get_me identity 再激活；删除/解除原绑定后释放 identity；旧 listener refresh 不得把 identity 写回已删除账号；V2 启用时必须配置 TG_V_CHAT_BOT_USERNAME；存量回填未完成、有重复归属或 Bot username 缺失时 V2 不可启用，分别返回 bound_account_already_bound / bot_username_missing | E3 PostgreSQL unique/migration、授权 identity persistence、删除后重绑、删除后的 listener refresh 拒绝、Bot peer 解析、回填完整率与 feature-gate 测试；E4 已启用前后的真实账号检查 | unproven |
| native-forward-batch-sequence | req-native-forward-relay-v2 | none | E3/E4 | 同一批普通消息获得独立 `batch_sequence=1..N`，不复用 RelayMessage/media sequence；RelayMessage 仅按账号、peer、source message id 去重，相册顺序仍保留；collecting append 与到期封批共用 account lock | E3 repository concurrency、封批边界、普通三条、跨 peer 相同 source id 与混合 album 测试；E4 真实三条和混合相册 | unproven |
| native-forward-bridge-isolation | req-native-forward-relay-v2 | none | E3/E4 | 同一 telegram_user_id 的 marker、NewMessage、Album 在同一 sender lock 内串行；每个 item 必须精确匹配第一跳持久化的 expected bridge message id，且 bridge id 仅在 sender/private-chat scope 唯一；错误 marker、orphan 和 mismatch forwarded item quarantine，绝不进入普通 router | E3 延迟 marker commit、并发 item/album、双账号相同 Bot message id、mismatch quarantine 与 router-spy 测试；E4 多账号并发真实观察 | unproven |
| native-forward-first-hop-liveness | req-native-forward-relay-v2 | none | E3/E4 | 第一跳实际返回数和唯一性必须等于 expected_count，并在释放 sender lock 前持久化；bridge_sending/awaiting_bot 到 `bridge_deadline_at` 进入 bridge_timeout/uncertain 并通知，不自动重放 | E3 adapter count/id ledger、时钟/reconciliation、重启和通知测试；E4 临时断开/受控延迟验证 | unproven |
| native-forward-final-ledger | req-native-forward-relay-v2 | none | E3/E4 | 第二跳前逐条创建并 claim BotPushMessage；远端成功后 DB 提交失败保留 uncertain 账本，不创建猜测性 ReplyMapping，不复制/重发；账号禁用/删除会终止在途批次且不允许重建 active ReplyMapping；最终转发前账号不可用必须提交 failed | E3 事务故障注入、account_unavailable commit、partial count、header terminal-notification、disable/delete race 测试；E4 真实批次和运营核对记录 | unproven |
| native-forward-telethon-contract | req-native-forward-relay-v2 | none | E3 | 依赖固定 `telethon>=1.34,<2`；bridge 使用 StopPropagation 的 NewMessage/Album 路由仅在 Telethon 1.x 验证 | E3 dependency manifest 与 bridge router 测试 | unproven |
| native-forward-777000 | req-native-forward-relay-v2 | none | E3/E4 | 777000 官方登录消息原始 code 正文不被截断；若 Telegram 不允许原生转发，返回持久化 failure code，绝不复制正文伪装成功 | E3 official-peer 分类/失败合同测试；E4 隔离测试账号上的真实 Telegram 官方登录消息 | unproven |

## Evidence Boundary

- Local and CI evidence can prove only E3。
- real Telegram and production remain E4 unproven，直到真实账号、真实媒体、真实发送结果和生产运行证据完成验收。
- 本轮实现证据可在交付汇报中标为 E3 通过；矩阵结果仍保持 `unproven`，不得用文档或本地通过替代 E4。

## QA Rules

- Output `qa_pass`, `failed`, `blocked`, or `unproven`.
- Do not accept unclear success evidence.
- `qa_pass` must be handed to product for Product Acceptance.
- `failed` must be handed back to dev with exact repro and evidence.
