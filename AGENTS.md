1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


# tg-v-chat Agent Rules

本项目是 `tg-v-chat`，真实项目目录为 `/Users/xida/PycharmProjects/tg-v-chat`。
历史文档中出现的 `/Users/xida/PycharmProjects/tg-v-caht` 是拼写错误。

## 项目真相源

- 产品口径：`docs/product/product-index.md`、`docs/product/tg-private-relay-v1.md`、`docs/product/account-management-bot-flow-v1.md`。
- 数据流转：`docs/index/dataflow.md`。
- 代码结构：`docs/index/project-structure.md`。
- QA 验收：`docs/qa/validation-plan.md`。
- 生产发布：`.github/workflows/deploy-production.yml`、`docker-compose.server.yml`、`docs/product/deploy-postgres-release-plan.md`。
- 事故记录：`docs/incidents/README.md`。

## 当前协作模式

- 本仓库当前不启用强制多 Agent 流程。
- 接到需求、Bug、线上问题或排障请求时，Codex 直接在当前线程完成读取、修改、测试和汇报。
- 不要默认要求 Intake Card、product/dev/qa/prod-diagnosis 线程投递、`agent-status-board.md` 更新或 `docs/05-implementation/multi-agent-practice/` 读取。
- `docs/templates/` 和 `docs/worklogs/` 仅作为历史模板或手工记录参考；只有用户明确要求多 Agent / handoff / worklog 时才使用。
- 不要因为缺少多 Agent 投递记录而阻塞普通开发、修复、审查或发布验证。

## 产品、代码和索引

- 需求、流程、验收标准或数据流转变化时，先同步对应 `docs/product/` 文档和 `docs/index/dataflow.md`，再改代码。
- 代码入口、模块边界、API、worker、模型、迁移、部署入口变化时，同步更新 `docs/index/project-structure.md`。
- 账号管理、私聊中转、绑定授权、ReplyMapping、Session failover 等行为必须对齐 `docs/product/product-index.md` 中的业务对象和验收口径。
- 生产运行必须使用 PostgreSQL；不要把 SQLite 测试路径当成生产证据。

## 发布和验证

- 生产发布路径默认是 `master -> release -> GitHub Actions Deploy Production`。
- 影响生产的任务必须区分本地 E3 验收、GitHub Actions 发布结果和真实生产 E4 证据。
- 单测优先使用 `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'`，避免测试卡住。
- 常用验证入口包括 `python3 -m pytest`、`python3 -m compileall -q src tests`、Alembic migration 检查和 `docker compose --env-file .env.example -f docker-compose.server.yml config`。
- 不允许 silent fallback、mock success 或未经验证的完成声明。

