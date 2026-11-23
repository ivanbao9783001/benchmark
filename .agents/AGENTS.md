# AGENTS.md

本包是 AISBench / 昇腾模型部署与评测的技能包（skills）与配套 CLI。本文件只做导航，不复制契约；契约以各 `SKILL.md` 及其 `references/` 为准。

## 入口

- 先读 [skills/ascend-llm-router/SKILL.md](skills/ascend-llm-router/SKILL.md)，按其路由表选择阶段技能。
- 派单契约（机器派单与自然语言派单共用）：[skills/ascend-llm-router/references/top-input.md](skills/ascend-llm-router/references/top-input.md)；已填示例在同目录 `examples/`，按场景选文件，不跨场景手改。
- 阶段技能：`ascend-llm-plan` → `ascend-llm-deployment` → `ascend-llm-eval` → `ascend-llm-release`，目录均在 `skills/` 下。
- 派单方工具（非技能，不参与技能加载）：[dispatch-kit/README.md](dispatch-kit/README.md)，含环境判定、必给信息清单、校验命令与 prompt 模板。
- CLI 安装与命令面：[ascend_llm_cli/README.md](ascend_llm_cli/README.md)；补丁版本以 `deploy-eval-v2 --version` 实际输出为准。
- 人类导航：[README.md](README.md)；架构说明：[ascend_llm_cli/DESIGN.md](ascend_llm_cli/DESIGN.md)。

## 执行约束

- 以 `SKILL.md` 为执行依据，不用 README 或本文件替代阶段流程。
- 机器派单只对授权与目标类缺口一次性列全并向用户澄清；现场事实与派生项由执行方自行取证或按默认值补齐，也不边做边问。
- 授权只能收紧不能扩大：`nodes`、`devices`、`allowed_root`、`preempt_other_processes`、`create_work_root` 不解释为可处理授权外资源。
- 示例只示范结构，不照抄其中的 host、devices、路径与端口。
- 默认不修改 `skills/` 与 `ascend_llm_cli/` 下的技能、源码与 schema，除非用户明确要求。
- 不读取历史任务目录、归档或回收站；交付类产物写到 `deliverables.local_dir/<task.id>`（交付根），运行证据写到 `allowed_root/<task.id>`（节点运行根），不写仓库根或临时路径；缺省交付根 `<控制机当前目录>/deploy-runs` 属于约定位置，不受“不写仓库根”限制，按派生规则使用即可。
- 未完成 plan 冻结与 readiness 不进入部署；结果未知时恢复查询，不更换请求身份盲重跑。
