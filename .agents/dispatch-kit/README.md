# 派单工具（dispatch kit）

本目录是**派单层**，不是技能：不要放进 `skills/`，也不要期望它被自动加载。它解决“怎么把一次任务交给一个全新 subagent”，执行规则仍以 `skills/ascend-llm-router/SKILL.md` 为准。

- 派单方：人或主 Agent。
- 接单方：一个没有历史上下文的新 subagent。
- 唯一输入：一段 prompt + 一份 top_input。

## 第一步：先给环境结论（判错整单作废）

派单方必须在 prompt 或 top_input 里**直接写出结论**，不要让接单方从示例反推：

- `existing`：现场已有运行中的目标容器。给每节点 `container.id`（完整 64 位）、`container.image`（`sha256:` 摘要）、`container_work_root`、`network{ip,interface}`。
- `owned`：明确授权 CLI 创建并在释放时删除容器。不写 `container`，给一个部署共用的 `deployment.image`（`sha256:` 摘要）。
- `self`：`existing` 且 CLI 就在该容器内执行。额外声明该节点是 `control_node` 且 `container.self=true`；路径与 `machine_id` 都按容器内给。

两者都没有 → 不发单，先补容器或创建授权。`owned` 与 `existing` 全节点一致，不得混用；`owned + self` 不存在。

## 第二步：再给任务形态

1. **要做什么**：只评测已有服务 → `eval_only`；只部署 → `deploy_only`；部署后评测 → `deploy_and_eval`。
2. **部署拓扑**（非 `eval_only`）：单机混部 → `colocated`；P/D 分离 → `pd`（单机或双机）。
3. **评测模式**（含评测时）：性能 → `perf`；精度 → `acc`（当前仅 GPQA-Diamond 全量 198 题）。

环境 × 形态的组合查 `references/top-input.md` §2：有示例的按该场景 `example` + `playbook` 填；没有示例的（owned 的其余组合、单机 PD）按 `contract.md` 组装。`scenarios.json` 只是已有示例的索引，不是支持范围。

## 第三步：派单方必须给什么（最小充分集）

只给接单方无法自行获得的事实与授权。plan/bindings、端口分配、请求文件等派生结构由接单方组装，不要替它写。

| 类别 | 必须给 | 不能省的原因 |
| --- | --- | --- |
| 环境 | 模式结论；existing 给容器 id / image / `container_work_root` / `network`，owned 给 `deployment.image` 与创建授权 | 决定骨架，判错整单作废 |
| 目标 | `action`、`topology`、`eval_mode`、`on_idle` | 决定命令链与是否释放 |
| 机器 | 节点 id、`host`、`allowed_root`、`python`、`guard_root` | 授权边界，不得扩大 |
| 现场动作 | `preempt_other_processes`、`create_work_root` | 结束他人进程或建目录需要明确许可 |
| 模型 | 模型 id + `served_name`、每节点 `model_paths`、实例 `devices` | 卡与模型路径不能猜 |
| 网络端口 | `network`、`ports`（`http` / `kv`）、`endpoint`；PD 另加 `kv_transfer.connector`、proxy 节点 `paths.proxy_program` | HCCL 与监听地址依赖真实值 |
| 评测 | `tester` 全套（含 `machine_id`、`datasets`）、`dataset`（acc）；`eval_only` 另给 `evaluation.service` | `machine_id` 与路径必须现场取证 |
| 交付与护栏 | `control_node`、`deliverables.local_dir`、`constraints` | 任务根与不可越界项 |

值必须可核验：容器 id 为完整 64 位、镜像为 `sha256:` 摘要、`machine_id` 来自 `/etc/machine-id`。缺失的当场一次性列全，不要边做边补，也不要拿示例值顶替。

## 第四步：校验

已安装 CLI 时：

```bash
deploy-eval-v2 validate-top-input --request <top_input.json>
```

未安装时按 [安装与前置](../skills/ascend-llm-router/references/installation.md) 安装并复验 `--version`，再执行校验。返回 `{valid, scenario, issues}`；退出码 2 表示仍有缺口。校验通过后 `scenario` 应与第一步的结论一致；没有示例的组合同样能通过校验，只是 `scenario` 不会对应某个 example 文件名。

## 第五步：发 prompt

照 [prompt.md](prompt.md) 填空后整段发给 subagent。要点：

- prompt 只负责启动、指路和交代授权；不重复 top_input 内容，也不附历史对话。
- 环境结论、top_input 路径、仓库根必须写成实际值。
- 不在 prompt 里写“先试试看”；校验失败就停，不进入 plan。

## 验收标准

一次通过：subagent 无需追问即可完成 `validate-top-input` → plan → 冻结 → readiness → 目标阶段，并交付四项状态（执行、证据完整性、有效性、清理）。任何一次返工都应先修 `skills/` 下的文档，而不是在 prompt 里补丁。
