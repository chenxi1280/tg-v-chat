from __future__ import annotations

import re

STATE_AWAITING_PHONE = "awaiting_phone"
STATE_AWAITING_CODE = "awaiting_code"
STATE_AWAITING_PASSWORD = "awaiting_password"

ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_BINDING = "binding"
ACCOUNT_STATUS_DISABLED = "disabled"

AUTH_STATUS_CANCELLED = "cancelled"
AUTH_STATUS_EXPIRED = "expired"

PHONE_PATTERN = re.compile(r"^\+\d{8,15}$")

RELAY_HELP_TEXT = (
    "中转说明\n\n"
    "绑定账号收到私聊后，Bot 会把发送人、接收账号、接收用户名和内容推送到这里。"
    "请直接回复 Bot 推送的原消息进行代发。"
)
HELP_TEXT = "帮助\n\n使用按钮完成绑定和查看账号。绑定与中转失败会直接返回明确错误。"
PHONE_PROMPT_TEXT = "请输入要管理的 TG 账号手机号，需包含国家区号，例如 +8613812345678。"
