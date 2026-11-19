# AGENTS.md

本仓库是 AISBench / 昇腾模型部署与评测的技能包（skills）与配套 CLI。本文件只做导航，不复制契约；契约以各 `SKILL.md` 及其 `references/` 为准。

## 入口

- 先读 [.agents/skills/ascend-llm-router/SKILL.md](.agents/skills/ascend-llm-router/SKILL.md)，按其路由表选择阶段技能。
- 机器派单唯一入口：[references/top-input.md](.agents/skills/ascend-llm-router/references/top-input.md)；已填示例在同目录 `examples/`，按场景选文件，不跨场景手改。
- 阶段技能：`ascend-llm-plan` → `ascend-llm-deployment` → `ascend-llm-eval` → `ascend-llm-release`，目录均在 `.agents/skills/` 下。
- CLI 安装与命令面：[.agents/ascend_llm_cli/README.md](.agents/ascend_llm_cli/README.md)；补丁版本以 `deploy-eval-v2 --version` 实际输出为准。
- 人类导航：[.agents/README.md](.agents/README.md)；架构说明：[DESIGN.md](.agents/ascend_llm_cli/DESIGN.md)。

## 执行约束

- 以 `SKILL.md` 为执行依据，不用 README 或本文件替代阶段流程。
- 机器派单缺失必填字段时一次性列出全部缺口向用户澄清，不边做边问。
- 授权只能收紧不能扩大：`nodes`、`devices`、`allowed_root`、`preempt_other_processes`、`create_work_root` 不解释为可处理授权外资源。
- 示例只示范结构，不照抄其中的 host、devices、路径与端口。
- 默认不修改 `.agents/skills/` 与 `.agents/ascend_llm_cli/` 下的技能、源码与 schema，除非用户明确要求。
- 不读取历史任务目录、归档或回收站；产物写到 `deliverables.local_dir/<task.id>`，不写仓库根或临时路径。
- 未完成 plan 冻结与 readiness 不进入部署；结果未知时恢复查询，不更换请求身份盲重跑。
