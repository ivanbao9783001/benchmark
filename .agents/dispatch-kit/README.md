# 派单工具（dispatch kit）

本目录是**派单层**，不是技能：不要放进 `skills/`，也不要期望它被自动加载。它解决“怎么把一次任务交给一个全新 subagent”，执行规则仍以 `skills/ascend-llm-router/SKILL.md` 为准。

- 派单方：人或主 Agent。
- 接单方：一个没有历史上下文的新 subagent。
- 唯一输入：一段 prompt + 一份 top_input。

## 第一步：先给环境结论（判错整单作废）

派单方必须在 prompt 或 top_input 里**直接写出结论**，不要让接单方从示例反推：

- `existing`：现场已有运行中的目标容器。给每节点 `container.id`（完整 64 位）、`container.image`（`sha256:` 摘要，`self` 节点可省略）、`container_work_root`、`network{ip,interface}`；并在当前所在容器的节点写 `container.self=true`。单机时唯一节点即 self，直接本地执行；双机 PD 只有当前节点写 self，另一节点经 SSH + docker。
- `owned`：明确授权 CLI 创建并在释放时删除容器。不写 `container`，给一个部署共用的 `deployment.image`（`sha256:` 摘要）。

`self` 不是第三种模式，只是 existing 的节点标记：恰好一个，且必须是 `control_node`。两者都没有 → 不发单，先补容器或创建授权。`owned` 与 `existing` 全节点一致，不得混用；`owned` 不写 `container`，也就没有 self。

## 第二步：再给任务形态

1. **要做什么**：只评测已有服务 → `eval_only`；只部署 → `deploy_only`；部署后评测 → `deploy_and_eval`。
2. **部署拓扑**（非 `eval_only`）：单机混部 → `colocated`；P/D 分离 → `pd`（单机或双机）。
3. **评测模式**（含评测时）：性能 → `perf`；精度 → `acc`（当前仅 GPQA-Diamond，缺省全量 198 题，可选 `num_prompts` 子集）。

环境 × 形态的组合查 `references/top-input.md` §2：有示例的按该场景 `example` + `playbook` 填；没有示例的（owned 的其余组合、单机 PD）按 `contract.md` 组装。`scenarios.json` 只是已有示例的索引，不是支持范围。

## 第三步：派单方必须给什么

按字段级别判定，不要替接单方组装 plan/bindings。**授权**与**需求**必须由派单方给出；**事实**可缺省，由接单方现场取证；**派生**由接单方按模板生成。接单方必须把事实、派生与保守缺省项写成具体值**写回 top_input** 后再校验——`validate-top-input` 不读缺省。

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| 模式结论、`deployment.image`（owned） | 派单方 | existing / owned；owned 另需创建与删除授权 |
| `nodes.<id>.container.id` | 派单方；`self` 节点自证 | 完整 64 位；非 `self` 节点不得由执行方按名称猜 |
| `container.image` | 现场发现 | `self` 可省略，记 `UNVERIFIED` |
| `container_work_root` | 现场 / 派生 | 容器内 `allowed_root` 的可写投影；唯一候选才采用，否则停止；目录允许不存在 |
| `network` | 现场发现（existing）/ 派单方（owned） | existing 为空时 `ip -o addr`，对端经 `docker exec`；多网卡按对端可达性选择并留证，无法确定则停；owned 无容器可探测，必须在 plan 前给出 |
| `machine_id` | 现场发现 | `/etc/machine-id` → `/run/machine-id` → `/var/lib/dbus/machine-id` |
| `host`、`python` | 现场发现 | 对端经 SSH 验证可达 |
| `allowed_root` | 派单方 | 授权边界，执行方不得自行设定、扩大或缩小 |
| `guard_root` | 派单方，可省 | 派单方未给时取 CLI 默认 `/var/lib/deploy-eval-v2/guard` 写回；宿主机全局，不得落在交付根、节点运行根或 `allowed_root` 内 |
| `action`、`topology`、`eval_mode` | 派单方 | 决定命令链 |
| `on_idle` | 派单方，可省 | 派单方未给时按 `keep_service` 写回；`deploy_only` 只能取该值 |
| `preempt_other_processes` | 派单方，可省 | 派单方未给时按 `false` 写回 |
| `create_work_root` | 派单方，可省 | 派单方未给时按 `false` 写回；只约束节点运行根之外的根，任务自身运行根与交付根照常创建 |
| 模型 id、`served_name`、`model_paths` | 派单方 | 每节点权重路径；`served_name` 可由模型名派生 |
| `devices` | 派单方 | 卡号；执行方用 `npu-smi` 核验可见性 |
| `ports`（http / kv）、`endpoint` | 派生 | 从空闲端口段分配后写入 top_input |
| `kv_transfer.connector` | 派单方 | PD 必填；执行方只核验两侧镜像都可用，不得自行选值 |
| `paths.proxy_program` | 现场 | proxy 节点上确认存在且可执行 |
| `tester` 归属及其 host、路径、`machine_id` | 派单方（双机）/ 派生（单机或明确同机） | 双机 PD 必须明确测试端，避免与被测服务同机；同机时派生，其余现场取证 |
| `evaluation.dataset` | 派单方 id；`split/scope` 派生 | acc 必填 |
| `control_node` | 派生 | existing 即 `self` 节点 |
| `deliverables.local_dir` | 派单方优先，可省 | 派单方未给时缺省 `<控制机当前目录>/deploy-runs` 并写回；必须可写，否则停止，不另选目录 |
| `constraints` | 派单方 | 不可越界项 |

派单方最少只需回答：卡、模型与权重路径、任务、数据集、容器归属（双机另加对端 IP、容器 id、SSH 访问）。其余按上表取证或派生，并在审阅摘要与报告 `limitations` 中留痕。

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
