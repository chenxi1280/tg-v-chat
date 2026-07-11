# 媒体转发正文前置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让私聊转发详情独立显示在媒体之前，媒体不再携带详情作为 caption。

**Architecture:** 仅调整 `TelethonBotGateway` 的 Bot 推送边界：媒体分支先发送格式化详情，再发送无 caption 的文件；服务层仍以媒体消息 ID 创建 ReplyMapping。文本分支和媒体持久化生命周期不变。

**Tech Stack:** Python 3.11, Telethon, pytest。

---

### Task 1: 调整 Bot 媒体推送顺序

**Files:**
- Modify: `tests/test_bot_media_push.py`
- Modify: `src/tg_v_chat/telegram/telethon_clients/gateway.py`
- Modify: `docs/product/tg-private-relay-v1.md`
- Modify: `docs/index/dataflow.md`

- [x] **Step 1: 写入失败回归测试**

```python
assert client.messages[1][1].endswith("内容：caption")
assert client.files[0][2] is None
```

- [x] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src <venv>/bin/python -m pytest tests/test_bot_media_push.py::test_gateway_uses_send_message_for_text_and_send_file_for_media -q`

Expected: FAIL，因为当前媒体分支只调用 `send_file(..., caption=详情)`。

- [x] **Step 3: 最小化实现**

```python
await self._client.send_message(system_user_id, _format_push_message(message))
sent = await self._client.send_file(system_user_id, artifact_path)
```

- [x] **Step 4: 验证单媒体、相册与完整测试集**

Run: `PYTHONPATH=src <venv>/bin/python -m pytest tests/test_bot_media_push.py -q`，再运行完整 `pytest -q`。

Expected: 单媒体、相册、持久化回归和完整测试集均通过。
