# 媒体转发正文前置设计

## 目标

将入站私聊媒体的转发详情作为独立 Bot 文本消息先发送，再发送无 caption 的媒体，使媒体始终位于详情之后。

## 范围

- 覆盖单张图片和贴纸，以及相册。
- 文本消息保持单条发送不变。
- ReplyMapping 继续绑定媒体消息 ID；详情文本不创建映射。

## 数据流

媒体推送依次执行：`send_message(转发详情)`，再执行 `send_file(媒体, caption=None)`。相册仍保持原有文件排序，并仅在文件批次发送完成后返回各媒体消息 ID。

若媒体发送失败，详情文本不会被误写为已完成的媒体推送或 ReplyMapping；既有 durable dispatch 失败语义保持不变。

## 验收

- 单媒体：客户端调用顺序为 `send_message` 后 `send_file`，且 `caption is None`。
- 相册：客户端调用顺序相同，文件列表保持原始顺序且无 caption。
- 文本：仍只调用 `send_message` 一次。
