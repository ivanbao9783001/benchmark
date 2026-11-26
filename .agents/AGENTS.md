# AGENTS.md

本文件是 AISBench / 昇腾 LLM 部署与评测技能包的**唯一总入口**：先在这里判断场景、准备输入、确认交付，再按链接进入阶段契约。字段级细节不在本文件复制，以各 `references/` 为准。

> 路径基准：本文中所有相对路径（如 `skills/...`、`references/...`）均相对**本文件所在目录**，即 `.agents/`。从仓库或交付根目录执行命令时，需自行补上前缀（容器内为 `runtime/.agents/`）。

## 1. 这是什么

一套“部署 / 评测 / 释放”技能包，加一个受信 CLI（`deploy-eval-v2`）。三个维度组合出任务形态：

- **容器模式**：`owned_container`（CLI 创建、启动、释放时删除）或 `existing_container`（借用已有容器，只 inspect / cp / exec，不建、不停、不删）。全任务级，不能混用。
- **拓扑**：`colocated`（单实例混部）或 `pd`（prefill / decode / proxy，可单机可双机）。
- **动作**：`deploy_and_eval`、`deploy_only`、`eval_only`、`release_only`。

前置条件：目标节点为 Linux + 昇腾 NPU；`existing` 需要宿主 SSH 与 docker CLI，`owned` 需要宿主可执行 python；评测需要 tester 侧已安装 AISBench。控制面位置随模式而定：`owned` 在各节点宿主，`existing` 在各自容器内（self 节点本地执行，其余经 SSH + `docker exec`）。

## 2. 你是哪类读者

| 你的角色 | 先读 | 然后 |
| --- | --- | --- |
| 人类派单方 | §3 场景表、§4 输入、§5 输出 | 用 `dispatch-kit/` 的模板填 `top_input` |
| 派单 agent | §3、§4、§5 | 读 `skills/ascend-llm-router/SKILL.md`，校验输入 |
| 执行 agent | §6 流水线、§5 输出 | 按 router 进入阶段技能；监控只读 `task-summary` 与 `get-status` |
| 只想释放资源 | §3 的 `release_only` 行 | 读 `skills/ascend-llm-release/SKILL.md`，复用原任务 |

## 3. 场景表

通用必给（所有含部署的动作）：`action`、`control_node`（owned 由派单方指定，existing 必为 `self` 节点）、`nodes` 的 `host` 与 `allowed_root`、`constraints`、模型与设备、实例；`task.id` 派单方给，未给时由执行方生成唯一值并写回；`deliverables.local_dir` 可缺省为 `<控制机当前目录>/deploy-runs`。`existing` 还要有每个节点的 `container`、`container_work_root`、`network`（`network` 可由执行方现场取证）；`owned` 要给 `deployment.image`。端口可派生，但必须在 `validate-top-input` 前写回。下表只列**额外必填**。

| 场景 | action / topology | 额外必填 | 你最终拿到 |
| --- | --- | --- | --- |
| 单机混部 + 评测 | `deploy_and_eval` / `colocated` | `eval_mode`；existing 的容器三项 | 服务 + perf/acc 结果与有效性 + 清理回执 + `task-summary` |
| 单机 PD + 评测 | `deploy_and_eval` / `pd` | `kv_transfer.connector`（随镜像 tag）、proxy 节点 `paths.proxy_program` | 同上 + PD 启动与 KV 证据 |
| 双机 PD + 评测 | `deploy_and_eval` / `pd` | 同上，外加两侧 `network` 与各自 `model_paths` | 同上 + 两侧绑定与跨机 KV 证据 |
| 单机混部只部署 | `deploy_only` / `colocated` | `on_idle=keep_service` | 保留服务 + 健康/冒烟证据 + `task-summary`（无评测） |
| 单机 PD 只部署 | `deploy_only` / `pd` | `on_idle=keep_service`、connector、proxy `paths` | 同上 + PD 证据 |
| 双机 PD 只部署 | `deploy_only` / `pd` | 同上，外加两侧 `network` 与 `model_paths` | 同上 + 跨机 KV 证据 |
| 外部已有服务测试 | `eval_only` / 无部署 | `evaluation.service`、tester 全部字段 | 评测结果与有效性；不创建服务 |
| 仅释放（同任务续接） | `release_only` / 无部署 | 复用原 `task.id`、交付根、runtime 与 nonce | 清理回执与 claims 释放；不是新任务 |

场景与入口的完整对应见 [contract.md](skills/ascend-llm-router/references/top-input/contract.md#action-与拓扑约束)；可复制示例在 [references/examples/](skills/ascend-llm-router/references/examples/)。PD 场景另需 `network`：existing 可由执行方现场取证，owned 必须由派单方给出。

## 4. 输入：四层与缺失处理

| 级别 | 含义 | 缺失时 |
| --- | --- | --- |
| 需求 | 要什么（action、拓扑、模型、connector、`on_idle`） | **停**，向派单方澄清 |
| 授权 | 允许动什么（节点、设备、`allowed_root`、`preempt_other_processes`、`create_work_root`） | **停**；只收紧不扩大。仅以下几项可保守缺省并记 `limitations`：容器只读借用、`on_idle=keep_service`、两项授权 `false` |
| 事实 | 现场值（`machine_id`、`python`、`network`、`model_paths`、`container`、`device_id_map`） | 执行方**取证或核验**；探不到就停，不写推测值 |
| 覆盖 | 显式覆盖模板（`args`、评测参数） | 用模板默认值；给了就必须进审阅摘要 |

优先级：**用户显式值 > 需求文档明确值 > 模板默认值**。派生项（`ports`、`hccl`、`served_name`、`container_root`、`node_root`、未给时的 `task.id`）由执行方计算并写回，不由派单方拍脑袋。

几个容易踩的点：

- `deployment.model.id` 不能含点号；`served_name` 可省略，省略即等于 `id`；`compiled.json` 回显 `model` 与 `model_id`。
- PD 的 `kv_transfer.connector` 必填，必须和镜像 tag 一起给出；执行方只做只读确认，确认不了记 `UNVERIFIED`，不得自行选值或改写参数。
- `existing` 的 `container.self=true` 恰好一个，且必须是 `control_node`；混合 owned/existing 直接拒绝。
- `device_id_map` 只在借用容器且物理卡与逻辑号不一致时给，必须来自 `npu-smi info -m`，不能按 `--device` 顺序推断。
- `network`、`container_work_root`、`guard_root` 在 `existing` 下是容器内路径；`owned` 下是宿主路径。
- 端口由执行方分配具体值并在 `validate-top-input` 前写回；`hccl` 不属于 `top_input`。

字段语义与必填条件：[contract.md](skills/ascend-llm-router/references/top-input/contract.md)；入口规则与模式判定：[top-input.md](skills/ascend-llm-router/references/top-input.md)；校验规则：[validation.md](skills/ascend-llm-router/references/top-input/validation.md)。

## 5. 输出：你最终拿到什么

交付根（控制机唯一，`<deliverables.local_dir>/<task.id>/`）：

```text
control.json  top_input.json  runtime.zip  frozen/  review-draft/
evidence/  results/  FINAL-REPORT.md  contracts/  requests/  agent-output/
```

节点运行根（每节点一份，`<allowed_root>/<task.id>/`）放 runtime、状态、日志与原始结果；`existing` 另有容器运行根 `<container_work_root>/<task.id>/`。`task_root/results` 只放摘要与引用，原始日志通过 `read-result` 读取；每个操作结束后由执行方在 `task_root/results/<operation_id>/stage-result.json` 写摘要副本（节点、`node_root`、`artifact_ref`；`sha256` 取 `node_root` 原始产物的校验值），`task-summary` 的 `operations` 由此派生，缺失即为空。

统一全景命令（只读派生，不改状态）：

```text
deploy-eval-v2 --config <task_root>/control.json task-summary --task-ref <task.id>
```

它给出 `task-summary/1`：`task`（模型、`model_id`、connector、节点、`device_map`）、`state`（`get-status` / `unavailable`）、`phases`（固定阶段名与来源）、`operations`（deploy/eval/release 摘要）、`service`、`evaluation`、`cleanup`、`limitations`。

四项结论缺一不可：

1. **执行**：服务是否就绪、评测是否跑完；
2. **证据完整性**：健康、冒烟、PD/KV、评测证据是否齐；
3. **有效性**：perf 或 acc 的 `*_validity`，部分证据只能是 `PARTIAL` / `UNVERIFIED`；
4. **清理**：`cleanup_status` 与 claims 是否释放（`on_idle=keep_service` 时明确“未释放”）。

监控与验收只读 `task-summary` + `get-status` + `progress.log`，**不要用对话输出或模型 token 流判断进展**。详细命令面见 [cli-contract.md](skills/ascend-llm-router/references/cli-contract.md)。

## 6. 执行流水线

```text
prepare-inputs（deploy_only / eval_only）
  → package-runtime → draft-review → freeze-inputs → check-ready
  → publish-task → start-task → deploy →（eval）→ release → stop-runtime
```

`deploy_and_eval` 自行组装 plan/bindings，不经过 `prepare-inputs`。未完成冻结与 readiness 不进入部署；结果未知时恢复查询，不更换请求身份盲重跑。每个阶段完成（含失败）向 `task_root/progress.log` 追加：

```text
PHASE <阶段> <rc> <耗时秒> <UTC 时间>
```

## 7. 权威文件与硬约束

| 主题 | 权威 |
| --- | --- |
| 入口规则、模式判定 | [top-input.md](skills/ascend-llm-router/references/top-input.md) |
| 字段语义、必填条件、场景表 | [contract.md](skills/ascend-llm-router/references/top-input/contract.md) |
| 校验规则与错误码 | [validation.md](skills/ascend-llm-router/references/top-input/validation.md) |
| 冻结与 plan 字段 | `skills/ascend-llm-plan/references/` |
| CLI 命令、状态与输出预算 | [cli-contract.md](skills/ascend-llm-router/references/cli-contract.md) |
| 派单模板与 prompt | [dispatch-kit/README.md](dispatch-kit/README.md) |
| 安装与前置 | [README.md](README.md)、[installation.md](skills/ascend-llm-router/references/installation.md) |

硬约束：

- 以 `SKILL.md` 为执行依据；本文件与 README 不替代阶段流程。
- 示例只示范结构，不照抄 host、devices、路径、端口与镜像值。
- `existing` 不创建、不启动、不停止、不删除容器；`owned` 才由 CLI 管生命周期。
- 授权只能收紧：`nodes`、`devices`、`allowed_root`、`preempt_other_processes`、`create_work_root` 不解释为可处理授权外资源。
- 交付根写 `deliverables.local_dir/<task.id>`，运行证据写 `allowed_root/<task.id>`；不写仓库根或临时路径（缺省交付根 `<控制机当前目录>/deploy-runs` 除外）。
- 默认不修改 `skills/` 与 `ascend_llm_cli/`，除非用户明确要求。
- 不读取历史任务目录、归档或回收站。
