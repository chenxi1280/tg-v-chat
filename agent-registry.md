# Agent Registry

| agent_key | thread_id | name | role | workspace | can_edit | notify_to | close_condition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| prod-diagnosis | 019f0a3a-4dbd-7c41-a4df-976cda0bfebf | 线上排查 Agent | 线上取证、影响范围、生产复核 | docs/incidents, docs/worklogs/prod-diagnosis.md | false | product | Incident Report 已投递 product；或 Production Verification Report 已回传；无法验证时写 blocked/unproven |
| product | 019f0a3a-923a-7690-9052-472aeaf618a6 | 管理 / 产品 Agent | Intake、Triage、PRD、验收标准、产品模型、数据流转索引 | docs/product, docs/design, docs/index/dataflow.md, docs/worklogs/product.md | false | dev, qa, prod-diagnosis | Product Design Complete 完整且 dev handoff 已真实投递；或产品验收结论已记录 |
| dev | 019f0a3a-bc28-7480-a612-28fa45d81fe6 | 执行 / 开发 Agent | 实现、修复、测试、代码结构索引 | repo, docs/index/project-structure.md, docs/worklogs/dev.md | true | product, qa | Development Complete 已记录，可行检查已运行，QA handoff 已真实投递 |
| qa | 019f0a3a-e90e-75e1-9d6e-bfbb104e22f2 | 验收 Agent | 验收、回归、问题复验 | docs/qa, docs/worklogs/qa.md | false | product, dev, prod-diagnosis | Validation Report 已记录，qa_pass 投递 product，failed 投递 dev |
| flow-supervisor | 019f0a3b-10ce-7ef2-a04a-7c083c463d09 | Flow Supervisor | 监督 handoff、ACK、超时重投、断链恢复 | agent-status-board.md, docs/worklogs | false | product, dev, qa, prod-diagnosis | 看板无 pending/timeout 断链，或 blocked 项已写清原因和下一步 |

## Thread Initialization

- project_id: `/Users/xida/PycharmProjects/tg-v-chat`
- path_correction: original handoff path `/Users/xida/PycharmProjects/tg-v-caht` was a typo; actual project path is `/Users/xida/PycharmProjects/tg-v-chat`.
- protocol_doc: `/Users/xida/codexProject/codexwork/Codex多Agent协作机制与项目落地手册.md`
- initialized_at: `2026-06-28`
