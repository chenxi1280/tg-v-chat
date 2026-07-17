# TG 原生双向转发 V2 PRD

- requirement_id: req-native-forward-relay-v2
- source: user
- received_at: 2026-07-17
- product_status: implementation_e3_passed_e4_unproven
- evidence_level: E3
- production_verification_required: true

> 2026-07-17 实现修订：本版本已完成本地 E3 验证，覆盖批次顺序、绑定账号身份唯一性、桥接超时、Bot 端串行消费、最终投递账本和显式灰度启用合同。在线 PostgreSQL integration、GitHub Actions 发布和真实 Telegram E4 仍未取证。

## 1. 背景与问题

当前入站私聊由 Bot 使用 `send_message` / `send_file` 重新发送。消息正文虽然包含发送人名称，但 Telegram 客户端把它显示为 Bot 自己发送的新消息，不显示原生“转发自”来源，重名时无法可靠判断是谁。

用户要求：

1. 在当前 SystemUser 与 Bot 的对话中显示 Telegram 原生“转发自”消息。
2. 同一批多条消息使用一次批量转发；客户端仍保留为多条原消息，不拼接成一条新文本。
3. 回复批次中的任意一条，都通过对应 BoundTgAccount 回复原 PrivateChatPeer。

## 2. 产品目标

- 原消息的文字、emoji、图片、视频、圆形视频、音频、语音、sticker 和原生转发来源尽量由 Telegram 保留。
- 多账号场景继续明确显示接收账号名和接收用户名。
- 同一接收账号、同一私聊对象的连续消息可聚合为一次批量转发。
- 每条最终 Bot 消息都建立独立 ReplyMapping，不共享模糊的批次级回复目标。
- 同一真实 Telegram 账号只能归属一个 BoundTgAccount，防止跨 SystemUser 串批或串回复。
- 失败、部分成功和结果未知必须显式记录，不以复制消息伪装原生转发成功。

## 3. 非目标

- 不把多条消息拼接为一条新的长文本。
- 不在绑定账号与 SystemUser 的私人会话中直接投递；用户交互入口仍是产品 Bot。
- 不承诺绕过 Telegram 的发送者隐私、受保护内容或禁止转发限制。
- 不扩展群、频道、普通 document、GIF、联系人、位置等类型。
- 本次只扩展入站原生转发；SystemUser 通过 Bot 主动发送 video/audio/voice 回复不在本次范围。
- 不提供 `send_message` / `copy_message` 静默回退。

## 4. 用户体验

### 4.1 单条消息

Bot 先发送一条批次说明，再发送 Telegram 原生转发消息：

```text
接收账号：小号A
接收用户名：@example_user
本批消息：1 条
```

随后一条消息显示 Telegram 客户端原生“转发自 …”卡片。用户必须回复这条原生转发消息；批次说明不创建 ReplyMapping。

### 4.2 多条消息

- 同一 BoundTgAccount、同一 PrivateChatPeer 的消息才允许进入同一批次。
- 普通连续消息以最后一条到达后的 1 秒静默窗口封批。
- Telegram album 事件在相册完整到达后立即封批，并保留 album grouping。
- 一批最多按 Telegram `forwardMessages` 的 100 条协议上限发送；超过 100 条时拆成相邻批次，不丢弃消息。
- 不同发送人、不同接收账号的消息禁止合并。
- 批次内的 `batch_sequence` 从 1 开始单调递增；它只表示本批展示/回复顺序，不复用原 RelayMessage 的媒体 sequence。

### 4.3 回复

- 用户回复批次内第 N 条最终 Bot 转发消息时，按该消息自己的 ReplyMapping 找到原 BoundTgAccount、PrivateChatPeer 和 source_message_id。
- 回复链路继续使用 primary -> standby_1 -> standby_2 session failover。
- 回复批次说明、已失效映射或非 reply 输入，继续明确失败。

## 5. 身份展示与隐私边界

Telegram 原生转发头可能包含可点击的原发送者，也可能因原发送者隐私设置只包含 `from_name`。因此定义：

| identity_visibility | 含义 | 产品表现 |
| --- | --- | --- |
| linked | Telegram 提供可关联的原发送者或频道 | 显示原生可点击“转发自”来源 |
| name_only | Telegram 只提供原发送者名称 | 保留原生名称，并在批次说明中显示“来源受 Telegram 隐私保护，仅能显示名称” |

- `name_only` 不是系统识别成功，不得伪造 `@username`、Telegram user id 或可点击链接。
- 内部 ReplyMapping 仍使用 listener 已知的原 PrivateChatPeer，不依赖转发头反查回复目标。
- 产品改善的是 Telegram 可见范围内的来源识别，不承诺每一条都能展示唯一账号。

## 6. 功能范围

### In Scope

- text、普通 emoji、photo、video、video_note、audio、voice、sticker 的原生双跳转发。
- 单条和 1-100 条批量原生转发。
- 图片/视频相册及混合媒体组保留原始顺序和 album grouping。
- 接收账号批次说明。
- 每条最终转发消息的 ReplyMapping。
- Telegram 官方服务号 `777000` 走相同原生转发规则。
- 受保护内容、消息缺失、数量不一致、网络结果未知的显式失败状态。

### Out Of Scope

- 跨 PrivateChatPeer 合批。
- 消息编辑、撤回与 reaction 的双向同步。
- 为 `name_only` 来源补全或猜测身份。
- 原生转发失败后复制正文或重新上传媒体。
- SystemUser 通过 Bot 主动上传 video、video_note、audio 或 voice 作为出站回复。

### 支持的入站媒体

| 产品类型 | Telegram/Telethon 识别 | 转发要求 |
| --- | --- | --- |
| 图片 | `photo` | 原生转发，不下载后重新上传 |
| 视频 | `video` | 保留视频、caption 和原发送者 |
| 圆形视频 | `video_note` | 按原消息原生转发 |
| 音频文件 | `audio` | 保留音频文件、标题和表演者元数据 |
| 语音消息 | `voice` | 保留语音消息形态，不转换为普通文件 |
| Sticker | `sticker` | 保留 sticker 形态 |

上述类型只要 Telegram 允许转发，就统一走两跳 `forward_messages`，不经过 `TG_V_CHAT_MEDIA_ROOT`。受保护内容仍按 `native_forward_restricted` 明确失败。

## 7. 业务对象

| object | states | 说明 |
| --- | --- | --- |
| NativeForwardBatch | collecting, sealed, bridge_sending, awaiting_bot, final_sending, sent, failed, uncertain | 同一账号、同一 peer 的原生转发批次 |
| NativeForwardItem | pending, bridged, sent, failed, uncertain | 批次内一条 RelayMessage 的两跳结果；有唯一的 `batch_sequence` |
| ForwardBridgeMarker | pending, received, consumed | 第一跳前发送给 Bot 的内部批次标记；按 token、绑定账号 Telegram user id 和条数校验 |
| NativeForwardBridgeQuarantine | recorded | 已知绑定账号 sender 的错误 marker 或 orphan forwarded item 的无内容审计记录；禁止进入普通 router |
| BotPushMessage | pending, sending, sent, failed, uncertain | 第二跳前先创建的最终 Bot -> SystemUser 投递账本；不是远端成功后的补写记录 |
| ReplyMapping | active, expired | 最终 Bot message id 到原私聊上下文的逐条映射 |

## 8. 核心流程

### 8.1 收集与封批

1. listener 收到私聊事件，立即按 `bound_tg_account_id + peer_id + source_message_id` 持久化 RelayMessage；Telegram `message_id` 只在所属聊天内唯一，不能跨 peer 去重。
2. 将 RelayMessage 加入相同 `bound_tg_account_id + peer_id` 的 collecting 批次，并在同一事务中分配下一个 `batch_sequence`。
3. album 完整到达后立即 sealed；普通消息在 1 秒静默窗口后 sealed。collecting 追加和到期 sealed 必须持有同一 `bound_tg_account_id` 锁，不能在 `find_collecting` 与 append 之间封批。
4. listener 重启后扫描 sealed 或到期 collecting 批次，继续第一跳，不依赖仅存在于内存的定时器。

### 8.2 第一跳：BoundTgAccount -> Bot

1. 仅当 `TG_V_CHAT_NATIVE_FORWARD_V2_ENABLED=true`、`TG_V_CHAT_BOT_USERNAME` 已配置，且 BoundTgAccount 已有唯一的 `telegram_user_id` 时，持久化 ForwardBridgeMarker token、expected_count、`bridge_deadline_at` 和批次关系；默认开关为 `false`。
2. BoundTgAccount user session 按真实 `telegram_user_id` 串行，先向产品 Bot 发送 marker，再使用 `forward_messages` 按 `batch_sequence` 升序转发原消息。
3. 第一跳远端调用前把批次置为 `bridge_sending`。adapter 必须返回 marker message id 和实际转入 Bot 对话的有序 message id 列表；服务在释放 sender lock 前逐项持久化为 item 的 `expected_bridge_message_id`。实际数量、唯一性或既有结果不匹配时进入 `uncertain`，不等待 Bot 猜测缺失项。
4. Bot 收到 marker 后，在同一 sender 串行锁内置 `awaiting_bot`。同一 sender 的 marker、普通 bridge item 和 album item 必须使用同一个持久化锁顺序处理。
5. 已知 BoundTgAccount sender 发来的 marker 格式错误、未知 token、没有 active marker 的 forwarded message，或不等于当前批次 `expected_bridge_message_id` 的 forwarded message，一律进入 bridge quarantine，禁止进入账号管理或 Bot reply router；非桥接普通用户消息才继续走现有 router。

### 8.3 第二跳：Bot -> SystemUser

1. Bot 按 marker 收集后续原生转发消息时，必须以 `bridge_sender_telegram_user_id + expected_bridge_message_id` 精确命中 item，再按 `batch_sequence` 记录 Bot 侧 bridge_message_id；Telegram `message_id` 只在该 Bot 私聊内唯一，不能跨绑定账号全局去重，也不能按“下一条”猜测归属。
2. 收齐后，先为每个 item 创建并 claim `BotPushMessage(pending -> sending)`；不得在最终 Telegram RPC 成功后才创建投递账本。
3. Bot 发送并持久化接收账号批次说明的 message id；它不创建 ReplyMapping。若其后第二跳失败，必须把该批次说明更新或补发为明确失败/uncertain 状态，不能留下看似成功的孤立说明。
4. Bot 按 `batch_sequence` 的已验证 bridge_message_id 调用批量 `forward_messages` 到 SystemUser。
5. 返回条数必须等于批次条数。相等时逐条把 BotPushMessage 标记 sent、写 NativeForwardItem 的 final bot message id，并创建 ReplyMapping；不足、传输未知或远端成功后本地提交失败时，保留预创建账本并把 item 和批次标记 `uncertain`。
6. 所有最终结果持久化后，批次才进入 `sent`。`uncertain` 不自动重放；人工核对后才能补录观察到的最终 message id 和 ReplyMapping。

## 9. 状态与一致性

```text
collecting -> sealed -> bridge_sending -> awaiting_bot -> final_sending -> sent
                                                       -> failed
                                                       -> uncertain
```

- 第一跳或第二跳开始远端 I/O 前必须先持久化当前状态；第二跳还必须逐条先创建 BotPushMessage。
- 远端明确拒绝且确认未发送，进入 failed。
- 远端可能已成功但本地无法确认、部分条数返回、或远端成功后数据库提交失败，进入 uncertain。
- failed 和 uncertain 都不自动复制消息；uncertain 不自动重试，避免重复转发。
- 不宣称 exactly-once；只承诺持久化状态、幂等入口和禁止不安全自动重放。
- `bridge_deadline_at` 由显式配置 `TG_V_CHAT_NATIVE_FORWARD_BRIDGE_TIMEOUT_SECONDS` 计算；到期仍处于 `bridge_sending`、`awaiting_bot` 或 `final_sending` 的批次进入 `uncertain`，并通知 SystemUser。
- 同一真实 `telegram_user_id` 的第一跳和 Bot 端 bridge 消费都串行，避免 marker 与批次消息交叉；数据库 advisory lock 是跨进程的唯一性边界。
- 授权 session 的 `get_me().id` 必须与 session/profile 在同一授权事务内写入 BoundTgAccount，随后才允许状态成为 active/degraded；V2 listener 不依赖启动后的异步回填来满足 identity gate。
- 账号禁用或删除必须先在同一 identity lock 内把该账号所有非终态 NativeForwardBatch 标记 failed，再失效 ReplyMapping；删除完成后释放该软删除行的 `telegram_user_id`，以允许该真实账号重新绑定。listener 的异步 identity refresh 必须按 account lock 后 identity lock 检查账号仍未删除，禁止把旧 listener 的 identity 回填到删除行。禁止在禁用/删除后继续创建最终 Bot 消息或重新激活 ReplyMapping。

## 10. 明确失败

| code | 场景 | 结果 |
| --- | --- | --- |
| native_forward_restricted | 受保护图片、视频、音频、语音或其他 Telegram 禁止转发内容 | failed，通知 SystemUser，不复制 |
| native_forward_source_missing | 原消息已删除或 source_message_id 不存在 | failed |
| bridge_marker_mismatch | token、sender 或 expected_count 不匹配 | 写 NativeForwardBridgeQuarantine，禁止进入普通 router |
| bridge_orphan_forward | 已知绑定账号 sender 发送了没有 active marker 的 forwarded message | 写 NativeForwardBridgeQuarantine，禁止进入普通 router |
| bridge_item_mismatch | 已知绑定账号 sender 的 forwarded message 不等于当前批次第一跳已持久化的 expected bridge message id | 写 NativeForwardBridgeQuarantine，保持原批次等待其预期 item |
| bridge_item_count_mismatch | Bot 收到或最终返回条数不一致 | uncertain |
| bridge_timeout | bridge_sending、awaiting_bot 或 final_sending 超过 `bridge_deadline_at` | uncertain，通知 SystemUser，不自动重放 |
| bridge_transport_unknown | 任一跳网络结果未知 | uncertain |
| native_forward_peer_invalid | Bot 或 SystemUser peer 不可用 | failed |
| bound_account_identity_missing | V2 启用时账号尚未回填 `telegram_user_id` | failed，提示重新连接/回填身份，不降级复制 |
| bound_account_already_bound | `telegram_user_id` 已归属另一条 BoundTgAccount | failed，拒绝重复绑定 |
| bot_username_missing | V2 已启用但未配置可由 user session 解析的产品 Bot username | failed，拒绝第一跳，不猜测 peer 或复用其他账号 access hash |

## 11. 数据要求

- BoundTgAccount 持久化授权账号自己的 `telegram_user_id`，供 marker sender 校验；该值来自授权 session 的 `get_me()`，不从名称或手机号推断，并对非空值建立全局唯一约束。授权完成先原子写入该值再激活账号；删除/解除原绑定后释放该值。重复账号必须先解除原绑定，不能共享或并行监听。
- NativeForwardBatch 持久化 owner、bound account、source peer、状态、expected_count、token、`bridge_deadline_at`、批次说明 bot message id、failure code/reason 和时间戳。
- RelayMessage 的入站幂等键为 `bound_tg_account_id + peer_id + source_message_id`；不同 PrivateChatPeer 相同的 message id 必须分别保留。
- NativeForwardItem 持久化 relay_message_id、唯一的 `batch_sequence`、所属 Bot 私聊 sender、第一跳返回的 `expected_bridge_message_id`、bridge_message_id、final bot_message_id、关联的 BotPushMessage 和状态。实际和预期 bridge id 均以 sender/private-chat scope 唯一。
- NativeForwardBridgeQuarantine 只持久化 sender Telegram user id、Bot message id、可选 marker token、failure code 和时间戳；不持久化原私聊正文或媒体。
- 最终 `bot_message_id` 仍按 `system_user_id + bot_message_id` 唯一；不得假设不同 Telegram 账号看到的私聊 message id 相同。
- V2 开关默认关闭。迁移后先由 listener 对 active/degraded 账号执行 `get_me()` 回填，处理重复归属，配置 `TG_V_CHAT_BOT_USERNAME`，再以完整率检查作为唯一启用前置条件；任何一项未完成都不得启用 V2。

## 12. 验收标准

1. 单条文字在当前 Bot 对话中显示原生“转发自”，回复后原 peer 收到回复。
2. 同一 peer 连续三条文字经一次批量转发到 Bot，对每条回复均命中正确原 peer。
3. 两个 peer 同时来信不会合并或串 marker。
4. 两个 BoundTgAccount 同时来信，批次说明和 ReplyMapping 归属正确。
5. photo、video、video_note、audio、voice、sticker 分别单条转发成功，消息形态、caption 和顺序不变。
6. 图片/视频混合相册保留顺序和 album grouping，不下载后重新上传媒体。
7. 同一批文字、图片、视频、音频混合消息逐条建立 ReplyMapping。
8. `name_only` 来源明确提示隐私边界，不伪造 username/id。
9. 受保护媒体返回 `native_forward_restricted`，没有复制消息和假 ReplyMapping。
10. 第二跳返回数量不足时整批为 uncertain，不自动补发。
11. 两条普通文本进入同一批次时分别得到 `batch_sequence=1,2`，不触发唯一键冲突；相册内部 sequence 不影响该顺序。
12. 第一跳返回数量不足、marker 已到但 item 未收齐、或 `bridge_deadline_at` 到期时，批次和已知 item 进入 uncertain，且没有消息误入普通 router。
13. 人为让 marker handler 延迟提交并同时注入 forwarded item/album 时，Bot 端仍按同一 sender 串行归入正确批次；同一 Telegram 账号重复绑定被拒绝。
14. 第二跳远端成功后模拟本地提交失败时，预创建的 BotPushMessage 保留为 uncertain，不生成假 ReplyMapping，也不自动重放。
15. `777000` 的真实官方登录消息在隔离测试账号上验证：原始 code 正文完整、双跳结果明确；若 Telegram 拒绝转发，返回持久化 failure code，不复制正文。
16. 默认 V2 开关关闭；只有存量 `telegram_user_id` 回填、唯一性检查和 `TG_V_CHAT_BOT_USERNAME` 配置完成后才可显式启用。关闭时继续既有 V1 路径，启用后单条失败不得降级复制。
17. 重启后 sealed 批次可恢复；sending、超时和 uncertain 批次不自动重放。
18. 本地 E3、GitHub Actions 发布和真实生产 E4 证据分别汇报。
19. 两个 BoundTgAccount 的 Bot 私聊使用相同 message id 时，两个 item 都正确归属各自 sender；任何不在第一跳返回 id 列表内的 forwarded message 只写 quarantine，绝不创建 ReplyMapping。
20. 新授权账号在 V2 listener 启动前已持久化真实 `telegram_user_id`；删除后同一真实账号可重新绑定；最终转发前账号不可用时批次持久化为 failed，不遗留 awaiting_bot。

## 13. 外部协议依据

- Telegram Bot API `forwardMessages` 支持一次转发 1-100 个递增 message id，保留相册分组，并会跳过不可转发或找不到的消息：<https://core.telegram.org/bots/api#forwardmessages>
- Telegram 原生转发头的 `from_id` 和 `from_name` 均为可选字段，因此必须区分 linked 与 name_only：<https://core.telegram.org/constructor/messageFwdHeader>
- Telethon `forward_messages` 需要目标、源 peer 和消息列表；`send_message` 复制消息不保留 forward header：<https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.forward_messages>
- 实现固定使用 Telethon 1.x；当前桥接拦截依赖的 `events.StopPropagation` 不适用于 Telethon 2.x 事件模型：<https://docs.telethon.dev/en/v2/developing/migration-guide.html>
