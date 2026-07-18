# 移除原生转发跨会话 ID 兼容路径

> **执行方式：** 当前线程直接实施。用户已明确要求直接升级，不保留旧 ID 账本或兼容逻辑。

## 目标

彻底移除第一跳发送端的 `marker_message_id` / `bridge_message_ids` 持久化和
`expected_bridge_message_id` 字段。第一跳只确认 Telegram RPC 返回的条数；Bot 私聊
收到的 marker id 与 `bridge_message_id` 是唯一的持久化消息标识。

## 成功标准

1. `FirstHopForwardResult` 只暴露 `forwarded_count`，不携带发送端消息 id。
2. Bot marker 在第一跳 RPC 返回前到达时，批次保持 `awaiting_bot`，不会因两个私聊的
   message id 不相等变成 `bridge_marker_mismatch`。
3. `NativeForwardItemModel`、仓储和 PostgreSQL schema 均没有
   `expected_bridge_message_id` 及其唯一约束。
4. 产品文档和数据流只描述 Bot 私聊侧的 canonical id。
5. 目标测试、完整测试、Alembic upgrade、compose config、GitHub Actions 与生产运行时检查均通过。

## 实施步骤

1. 在 `tests/test_native_forward_collector.py` 写回归测试，模拟 Bot marker 在 user-session
   第一跳返回前写入 `awaiting_bot`；验证新的 count-only 结果不会覆盖或比较 Bot 侧 marker id。
2. 在 `tests/test_native_forward_schema.py` 和 `tests/test_user_session_native_forward.py` 写/调整
   契约测试，要求不再存在旧字段、旧仓储写入接口和发送端 id 结果。
3. 修改 `src/tg_v_chat/domain.py`、`src/tg_v_chat/services/native_forward.py` 和
   `src/tg_v_chat/telegram/private_listener/native_forward.py`，以 `forwarded_count` 进行第一跳
   完整性判断，不再写入第一跳发送端的 marker/item id。
4. 修改 `src/tg_v_chat/storage/models.py` 与
   `src/tg_v_chat/storage/repositories/native_forward.py`，删除旧 item 字段、唯一约束和
   `record_first_hop_result`；保留 `first_hop_marker_message_id`，但它只记录 Bot 私聊实际 marker id。
5. 新增 Alembic migration，删除 `uq_native_forward_item_expected_bridge_message` 和
   `expected_bridge_message_id`。不为旧应用版本保留 runtime compatibility。
6. 更新 `docs/product/native-forward-relay-v2.md`、`docs/index/dataflow.md` 与相关文档契约测试。
7. 运行定向与完整验证，提交 `master`、快进 `release`、触发 GitHub Actions，并核对生产镜像、迁移
   head、容器健康和新增 bridge 错误聚合。

## 发布风险与处理

发布脚本会先执行新镜像的 Alembic migration，再用新镜像重建 bot/listener/worker。旧容器在该
短窗口内不再具有运行时兼容性；本次没有 active V2 batch，且用户要求直接升级。发布后不会自动
重放任何 `uncertain` 历史批次。
