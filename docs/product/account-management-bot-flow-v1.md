# Account Management Bot Flow V1 PRD

- message_id: product-design-account-management-bot-flow-v1
- intake_id: intake-tg-account-management-001
- from_agent: product
- to_agent: dev
- related_version: account-management-bot-flow-v1
- project_path: `/Users/xida/PycharmProjects/tg-v-chat`
- level: L2
- evidence_level: E3
- design_status: complete
- ready_status: ready
- done_status: remediation_code_complete_e3_passed
- release_gate: pending_production_verification
- production_verification_required: true

## Intake Card

- source: user
- received_at: 2026-06-28
- raw_request: 当前 `/start` 后只回复在线状态，逻辑奇怪；应回复账号管理，并通过按钮引导用户完成要管理的 TG 账号绑定。需要重新设计操作逻辑，并编写 PRD、项目结构、数据流转和对应索引。
- suspected_type: product_redesign
- affected_surface: TG Bot private chat, account binding flow, account status management, auth challenge flow
- user_visible: true
- production_related: true
- owner_agent: product

## Triage Card

- proposed_level: L2
- cost_tier: standard_team
- ready_status: ready
- route: product -> dev -> qa -> product -> prod-diagnosis
- validation_required: true
- production_verification_required: true
- escalation_triggers: `/start` 无管理入口、绑定流程中断、验证码或 2FA 错误不可恢复、账号状态误报、session 授权成功但无法监听或代发。

## Product Decision

`/start` 和 `/admin` 的第一响应必须是“账号管理”首页，而不是纯状态文案。用户通过 Telegram inline keyboard 完成绑定、查看账号、处理授权状态和阅读中转说明；命令只作为快捷入口，不要求用户记忆隐藏命令。

## Requirement Coverage

| user_requirement | product_decision | bot_design | backend_design | dataflow_design | qa_acceptance | status |
| --- | --- | --- | --- | --- | --- | --- |
| `/start` 后回复管理账号 | `/start` 打开账号管理首页 | 文案标题为“账号管理”，展示账号数量与状态摘要 | Bot router 渲染 home view | BotCommand -> AccountManagementView | `/start` 返回首页和按钮 | covered |
| 有按钮引导 | 全部关键操作使用 inline keyboard | 绑定 TG 账号、我的账号、授权状态、中转说明、帮助 | callback_data 路由到明确 action | BotCallback -> ActionHandler | 点击按钮进入对应步骤 | covered |
| 绑定要管理的 TG 账号 | 绑定流程分步输入手机号、验证码、2FA | 每一步只问一个输入，并提供取消/返回首页 | AuthChallenge 持久化状态 | Phone -> Code -> Password -> SessionSlot | 手机号、验证码、2FA 成功和失败可测 | covered |
| 管理已绑定账号 | 我的账号展示接收账号名、`@username` 和状态 | 每个账号可查看详情、补授权、禁用、返回；手机号只在详情中脱敏展示 | BoundTgAccount + TgSessionSlot 读取 | AccountList -> AccountDetail | 多账号、空列表、状态异常可测 | covered |
| 错误要明确 | 不做沉默失败或假成功 | 错误消息保留在当前步骤并给出重试按钮 | service exception 映射为明确用户错误 | FailureEvent -> BotReply | 授权失败、超限、过期、取消可测 | covered |

## Scope

### In Scope

- `/start`、`/admin`、`/accounts`、`/bind` 都进入按钮式账号管理体验。
- Telegram inline keyboard 作为主要导航方式。
- 绑定主账号授权：手机号 -> Telegram 验证码 -> 可选 2FA -> 生成 primary session。
- 已绑定账号列表、账号详情、授权状态、禁用账号、重新授权。
- 备用授权入口：在账号详情中引导补充 standby_1、standby_2。
- 绑定过程支持取消、返回首页、重试当前步骤。
- 对用户输入手机号、验证码、2FA 密码做边界校验和明确失败展示。

### Out Of Scope

- Web 管理后台。
- 群聊、频道、话题消息中转。
- 自动养号、自动规避风控、绕过 Telegram 授权。
- 通过 Bot token 代替用户个人账号 session。
- 在 PRD 阶段直接变更生产代码或生产数据库。

## Bot UX Design

### Account Management Home

Entry points:

- `/start`
- `/admin`
- `/accounts`
- any callback action `account.home`

Home message:

```text
账号管理

已绑定账号：{active_count}/{max_count}
需要处理：{attention_count}

请选择要进行的操作。
```

Home buttons:

| button_text | callback_data | behavior |
| --- | --- | --- |
| 绑定 TG 账号 | `account.bind.start` | 开始绑定新的 Telegram 个人账号 |
| 我的账号 | `account.list` | 展示当前用户绑定的账号列表 |
| 授权状态 | `account.status` | 展示 active/degraded/reauth_required/disabled 汇总 |
| 中转说明 | `account.relay_help` | 说明收到私聊后如何 reply Bot 消息代发 |
| 帮助 | `account.help` | 展示命令快捷入口和常见失败原因 |

### Empty Account State

When no account exists, `/start` still shows the home view but changes body:

```text
账号管理

你还没有绑定要管理的 TG 账号。
绑定后，该账号收到的私聊会推送到这里，你回复 Bot 推送消息即可代发。
```

Primary button order in empty state:

1. 绑定 TG 账号
2. 中转说明
3. 帮助

### Bind Account Flow

Binding is a stateful wizard bound to `system_user_id`.

| step | bot prompt | accepted input | success transition | failure behavior |
| --- | --- | --- | --- | --- |
| `awaiting_phone` | 请输入要管理的 TG 账号手机号，需包含国家区号，例如 `+8613812345678` | phone text | create AuthChallenge, send Telegram code, move to `awaiting_code` | invalid phone stays in step |
| `awaiting_code` | 验证码已发送，请输入 Telegram 收到的验证码 | digits or spaced code | complete if no 2FA, or move to `awaiting_password` | wrong/expired code stays in step with retry |
| `awaiting_password` | 该账号开启了 2FA，请输入二次密码 | password text | persist encrypted session and mark account active | wrong password stays in step |
| `complete` | 绑定成功 | callback only | account detail | none |

Buttons shown during every step:

| button_text | callback_data | behavior |
| --- | --- | --- |
| 取消绑定 | `account.bind.cancel` | cancel challenge and return home |
| 返回首页 | `account.home` | leave wizard and show home |

Security rules:

- Bot must never log plaintext 2FA password.
- Bot should attempt to delete password messages after processing when Telegram permissions allow it; deletion failure must be logged but not hidden as success.
- Challenge ids are internal. User-facing messages must not expose database ids as secrets or proof of authorization.

### Account List

Account list message:

```text
我的账号

{index}. {display_name}（@{username}） - {status_label}
主授权：{primary_status}
备用授权：{standby_summary}
```

Buttons:

- one button per account: `account.detail:{account_id}`
- `绑定 TG 账号`
- `返回首页`

Display identity:

- Account list and buttons prioritize接收账号名 and `@username`; users should not need to remember phone numbers.
- `+8613812345678` displays in detail as `+86138****5678`.
- Full phone may be stored, but the Bot UI should avoid full exposure after binding.

### Account Detail

Detail message:

```text
账号详情

接收账号：{display_name}
用户名：@{username}
手机号：{phone_masked}
状态：{account_status}
主授权：{primary_status}
备用 1：{standby_1_status}
备用 2：{standby_2_status}
最近失败：{last_failure_or_none}
```

Buttons:

| button_text | callback_data | condition |
| --- | --- | --- |
| 授权缺失槽位 | `account.slot.bind:{account_id}:{slot}` | target slot has never been authorized |
| 重新授权槽位 | `account.slot.reauth:{account_id}:{slot}` | target slot is expired, revoked, or failed |
| 禁用账号 | `account.disable.confirm:{account_id}` | account is not disabled |
| 返回账号列表 | `account.list` | always |
| 返回首页 | `account.home` | always |

### Session Slot Authorization And Account Status

- primary, standby_1, and standby_2 are authorized independently；每个槽位必须使用对应 DeveloperAppSlot 完成自己的 phone/code/2FA 流程。
- The primary session must not be copied 到 standby_1 或 standby_2；补授权和重新授权只更新 callback 指定的 target slot。
- 缺失槽位统一使用 `account.slot.bind:{account_id}:{slot}`，已有槽位重新授权统一使用 `account.slot.reauth:{account_id}:{slot}`；`slot` 只允许 `primary`、`standby_1`、`standby_2`。
- `primary usable => active`。
- `primary unusable and any standby usable => degraded`。
- `all authorized slots unusable => reauth_required`。
- disabled and deleted accounts are not recomputed；禁用或删除是显式终态，不由 session health 自动覆盖。
- An unauthorized standby does not cause degradation；standby 从未授权时，只要 primary usable，账号仍为 active。

### Disable Confirmation

Disabling must use confirmation to prevent accidental loss of routing.

```text
确认禁用这个账号？

禁用后不会继续监听该账号私聊，也不会使用它代发。
历史中转记录保留。
```

Buttons:

- 确认禁用: `account.disable:{account_id}`
- 取消: `account.detail:{account_id}`

### Relay Help

Help content must be operational:

```text
中转说明

1. 先绑定要管理的 TG 个人账号。
2. 该账号收到私聊后，Bot 会把消息推送到这里。
3. 你必须直接回复 Bot 推送的那条消息。
4. Bot 会用对应绑定账号回复原私聊对象。

不要直接发普通消息代发；普通消息没有原始会话上下文。
```

## State Machine

| state | owner | enter_by | exit_by | persistence |
| --- | --- | --- | --- | --- |
| `home` | Bot UI | `/start`, `/admin`, callbacks | button action | no durable state |
| `account_list` | Bot UI | `account.list` | detail/home/bind | no durable state |
| `account_detail` | Bot UI | `account.detail` | reauth/disable/list/home | no durable state |
| `awaiting_phone` | Auth flow | `account.bind.start` | valid phone/cancel | Bot conversation state + AuthChallenge after phone accepted |
| `awaiting_code` | Auth flow | phone accepted | valid code/cancel/expire | AuthChallenge |
| `awaiting_password` | Auth flow | code requires 2FA | valid password/cancel/expire | AuthChallenge |
| `disabled` | Account service | disable confirmed | future re-enable design | BoundTgAccount |

## Backend / API / Worker Design

- affected_modules:
  - `tg_v_chat.bot.router`: route commands, callback queries, and text input by conversation state.
  - `tg_v_chat.bot.account_management`: render account management messages, inline keyboards, and binding state transitions.
  - `tg_v_chat.services.auth`: bind, code, password, challenge completion, and account limit enforcement.
  - `tg_v_chat.telegram.telethon_clients`: real Telethon authenticator for user sessions and Bot callback handling.
  - `tg_v_chat.storage.repositories`: account list/detail/status reads and challenge lifecycle.
- data_models:
  - existing: SystemUser, BoundTgAccount, TgSessionSlot, AuthChallenge.
  - updated: BoundTgAccount stores `display_name` and `username` for account recognition.
  - added: BotConversationState for per-SystemUser wizard state.
- migrations:
  - `0002_bot_conversation_states.py` adds `bot_conversation_states`.
- idempotency:
  - duplicate callback query must not create duplicate AuthChallenge.
  - repeated `/start` must only re-render home.
  - duplicate code/password submit for completed challenge must return current account detail, not create a second account.
- concurrency:
  - one active binding wizard per SystemUser.
  - account-level reauth and standby bind must serialize on BoundTgAccount.
- failure_handling:
  - show explicit errors for invalid phone, Telegram send-code failure, wrong code, expired code, 2FA required, wrong 2FA, account limit reached, and unavailable authenticator.
  - never return success until encrypted session is persisted and account/session state is committed.

## Dataflow Design

### Home Render

1. Bot receives `/start`, `/admin`, `/accounts`, or `account.home`.
2. Resolve SystemUser by Telegram sender id.
3. Read account count and attention count.
4. Render AccountManagementHome with inline keyboard.
5. No durable state is written.

### Bind New Account

1. User taps `绑定 TG 账号`.
2. Bot sets conversation state to `awaiting_phone`.
3. User sends phone number.
4. AuthService creates BoundTgAccount in `binding` state and AuthChallenge.
5. TelegramAuthenticator sends code through selected DeveloperAppSlot.
6. User submits code.
7. If 2FA is required, challenge moves to `awaiting_password`; otherwise encrypted primary session is persisted.
8. User submits 2FA password when required.
9. AuthService writes encrypted TgSessionSlot, stores display name / username, and marks BoundTgAccount active.
10. Bot renders success with account management navigation; account detail is available from `我的账号`.

### Account Management

1. User taps `我的账号`.
2. Bot reads BoundTgAccount rows scoped to SystemUser.
3. Bot reads TgSessionSlot status for each account.
4. Bot renders list with display name / username and account detail actions.
5. Disable/reauth actions write only after explicit user confirmation or valid auth completion.

## QA Acceptance

- `/start` with no accounts returns Account Management home with `绑定 TG 账号`, `中转说明`, `帮助`.
- `/start` with accounts returns bound count, attention count, and all home buttons.
- `/admin` opens the same Account Management home, not a plain status sentence.
- `绑定 TG 账号` starts phone prompt and shows cancel/home buttons.
- invalid phone returns explicit validation error and stays on phone step.
- valid phone creates one AuthChallenge and moves to code step.
- wrong code returns explicit error and stays on code step.
- code requiring 2FA moves to password step.
- wrong 2FA returns explicit error and stays on password step.
- successful 2FA persists encrypted session and renders account detail.
- tapping cancel cancels the active binding flow without marking account active.
- `我的账号` empty state and populated state both render correctly.
- disabling an account requires confirmation.
- duplicate callback and duplicate submit do not create duplicate account/session rows.

## Release And Production Verification

- release_gate_required: true
- production_verification_required: true
- E3 required before release: unit tests for router/view/state/auth flow; integration-like tests for callback/text flow against fake authenticator.
- E4 required after release: real Telegram `/start`, button clicks, phone code delivery, code submit, optional 2FA, account list, and account detail verified in production.

## Design Conclusion

- design_status: complete
- missing_inputs: none for V1 product design
- dev_handoff_ready: true
- implementation_status: remediation_in_progress_e4_unproven
- implementation_boundary: This PRD authorizes product and implementation planning for account management flow only. It does not authorize fake auth success, mock production verification, or silent fallbacks.
