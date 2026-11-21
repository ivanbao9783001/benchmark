# Agent Skills

本目录收录用于 AISBench 相关工作流的 Agent skills，执行契约由各 `SKILL.md` 与其 `references/` 维护。

| Skill | 用途 | 使用入口 |
|---|---|---|
| [ascend-llm-router](skills/ascend-llm-router/SKILL.md) | 任务入口、意图识别与阶段交接 | [派单契约](skills/ascend-llm-router/references/top-input.md) |
| [ascend-llm-plan](skills/ascend-llm-plan/SKILL.md) | 现场绑定、审阅与输入冻结 | [字段契约](skills/ascend-llm-plan/references/plan-contract.md) |
| [ascend-llm-deployment](skills/ascend-llm-deployment/SKILL.md) | 部署、健康检查、冒烟与服务保留 | [部署技能](skills/ascend-llm-deployment/SKILL.md) |
| [ascend-llm-eval](skills/ascend-llm-eval/SKILL.md) | 原生性能评测、证据核验与独立评测收尾 | [评测技能](skills/ascend-llm-eval/SKILL.md) |
| [ascend-llm-release](skills/ascend-llm-release/SKILL.md) | 按资源归属核验并释放自有服务 | [释放技能](skills/ascend-llm-release/SKILL.md) |

五个技能共用 [ascend_llm_cli](ascend_llm_cli/README.md)，通过 `deploy-eval-v2` 的公开 JSON 契约协作；架构见 [DESIGN](ascend_llm_cli/DESIGN.md)。

## 加载与使用

1. 安装/核对 CLI：`deploy-eval-v2 --version`；未安装时在 `ascend_llm_cli/` 执行 `python -m pip install .`，或安装提供的 wheel。
2. 安装五个技能：`deploy-eval-v2 install-skills --request skills-install.json`，请求为 `{"source":"<repo>/.agents/skills","destination":"<Agent 技能目录>"}`。
3. 从 `ascend-llm-router` 进入任务。

检查命令、失败处理与远端依赖见 [安装与前置](skills/ascend-llm-router/references/installation.md)。`.agents` 是本仓库的组织目录，不保证被所有 Agent 自动发现。

## 目录约定

每个 skill 的 `SKILL.md` 描述触发条件与执行流程；按需读取的参考放在 `references/`，可复制模板放在 `templates/`。技能内不维护 Schema 副本，字段以已安装 CLI 的 `export-contracts` 为准。新增 skill 使用独立目录，并在上表添加入口。

公共层仅保留导航与跨 skill 的通用约定。日志、现场绑定、模型权重和任务产物保存在仓库外。
