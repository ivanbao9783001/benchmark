# 派单契约（top_input）

top_input 是任务的唯一入口：派单方一次性给出授权与目标，subagent 据此完成 plan → 部署 → 评测 → 释放。它不是 task.json，也不替代冻结产物；agent 仍须自行组装 plan、绑定现场并冻结。prompt 只负责启动并指向本文件，不承载授权与现场事实。

## 0. 先判定运行时模式（决定骨架，判错整单作废）

运行时只有两种：容器是否已存在、由谁管理，是授权与现场事实，不是风格选择。

| # | 问题 | 是 | 否 |
| --- | --- | --- | --- |
| Q1 | 现场是否已有本次要用的运行中容器，且容器 id 可确定（派单方给出，或 `self` 节点由执行方在容器内自证）？ | **existing**：**每个**节点写 `container={id,image}`、`container_work_root`，`network` 必填；`self` 节点可省略 `image` | 进入 Q2 |
| Q2 | 派单方是否明确授权 CLI 创建并在释放时删除容器？ | **owned**：不写 `container`，写 `deployment.image` | **停止**：不得自行创建，也不得伪造容器 |

existing 是**全容器内闭环**：所有节点（含 tester）的路径、解释器、身份、门卫与产物都在各自容器内，不用宿主路径或宿主 Python。`container.self` 只标出“控制进程当前在哪个容器、如何传输”，不是“容器内 vs 宿主”的开关：

- **单机**：唯一节点写 `container.self=true`，直接本地执行；不对自己的容器做 SSH、`docker inspect` 或 `docker exec`。
- **双机 PD**：只在当前所在的容器节点写 `container.self=true`；另一个节点不写，但它的控制面同样在容器内，只是经宿主 SSH + `docker exec` 进入；`node_root`、`container_root`、`python`、`guard_root`、`machine_id` 全部按容器内取值。必须恰好一个 self，且是 `control_node`。
- owned 全宿主，没有 self；existing 全容器，不得与 owned 混用，也不得出现“一个容器、一个宿主”的混合形态。
- `self` 节点没有 docker，镜像 digest 无法自证，`container.image` 可省略；给出但无法核验时证据记 `UNVERIFIED`，不得编造。非 self 节点由控制端经宿主 `docker inspect` 核验，仍须给出 `sha256:` 摘要。
- tester 与部署节点同规则：existing 的 tester 在容器内闭环；owned 的 tester 在宿主。
- 现场有多个候选容器而派单方未指定时，停止回报，不按名称猜；字段差异与生命周期见 [contract.md 容器模式](top-input/contract.md)。

## 0.5 派单最小集与自补边界

派单方只需回答业务事实，不需要了解契约字段。以下业务项缺失才算缺口：

| 业务问题 | 单机 | 双机 |
| --- | --- | --- |
| 用哪几张卡 | 必给 | 两侧必给 |
| 什么模型、权重在哪 | 必给 | 两侧必给 |
| 做什么（`action` / `topology` / `eval_mode`） | 必给 | 必给 |
| 数据集（acc） | 可省：acc 缺省 `gpqa`，可选 `gsm8k` | 同左；路径按 tester 所在侧取证 |
| 容器能否借用 | existing：必给，容器 id 可自证；owned：必给创建与删除授权 | existing：必给两侧容器归属，并给出对端 IP、对端容器 id 与 SSH 访问方式；owned：给 `deployment.image` 与创建/删除授权 |

以下字段由执行方取证或派生，派单方不必提供；其余字段仍按 [contract.md](top-input/contract.md) 的级别判定。

| 类别 | 字段 | 取证或派生方式 |
| --- | --- | --- |
| 现场事实 | `container.image`、`network`（existing 为空时）、`machine_id`、`python`、`aisbench`、`model_path`、`tester.datasets.<id>.path`、端口占用 | `machine_id` 用 `deploy-eval-v2 identity` 在实际执行位置取证（优先 `/etc/machine-id`，为空时 hostname 派生）；existing 的**所有**节点都在容器内取证：`self` 本地读取，其余节点经宿主 SSH + `docker exec`；owned 在宿主取证。owned 的 `network` 必须在 plan 前由派单方给出 |
| 派生 | `task.id`、`root`（`node_root=allowed_root/task.id`）、`task_root`（`deliverables.local_dir/task.id`）、bindings、端口分配、`container_work_root`、`deliverables.local_dir`、`guard_root`、`on_idle`、`authorization`、`dataset.split/scope`、case 参数 | [derivation.md](top-input/derivation.md) 与 plan 模板默认值 |

`container.id` 不属于现场事实：`self` 节点自证；非 `self` 节点必须由派单方给出完整 64 位 id，执行方按它 `docker inspect` 并核对，不按名称猜。

可保守缺省的授权项只有：容器只读使用（不创建、不停止、不删除）、`on_idle=keep_service`、`preempt_other_processes=false`、`create_work_root=false`（只约束节点运行根之外的根，任务自身目录照常创建）。`task.id` 属需求而非授权：派单方未给时执行方生成唯一值（`<action>-<UTC时间戳>`）并写回，生成值进审阅摘要。`allowed_root` 是授权边界，必须由派单方给出，执行方不得自行设定、扩大或缩小；`guard_root` 未给时用 CLI 默认 `/var/lib/deploy-eval-v2/guard`，不得派生到交付根、节点运行根或 `allowed_root` 内。PD 的 `kv_transfer.connector` 属于派单决策，执行方只核验两侧镜像可用。采用缺省时在报告 `limitations` 声明未获显式授权。“可省”只说明派单方不必给：执行方必须先把缺省值或派生结果写成**具体值**写回 top_input 再运行 `validate-top-input`（该命令不读缺省，字段缺失即按缺口报错），与 tester 缺省、端口分配的处理方式一致。

缺口处理上只有三类情况停止：业务关键项缺失；现场事实互相冲突（卡被占、路径不存在、数据集为空、多候选容器无法判定归属）；双机缺少对端 IP、容器 id 或 SSH 访问方式。其余停止条件见 [validation.md](top-input/validation.md)。

## 1. 再判定任务形态

`action` → `topology` → `eval_mode`，与运行时模式正交；`self` 不参与任务形态判定：

| 任务形态 | `action` | `topology` | `eval_mode` | 额外必填 |
| --- | --- | --- | --- | --- |
| 单机混部只部署 | `deploy_only` | `colocated` | — | `on_idle=keep_service` |
| 单机混部 + 评测 | `deploy_and_eval` | `colocated` | `perf` / `acc` | `on_idle`、`evaluation.tester` |
| 单机 PD 只部署 | `deploy_only` | `pd` | — | `kv_transfer.connector`、proxy 节点 `paths.proxy_program`、`network`（existing 必填） |
| 单机 PD + 评测 | `deploy_and_eval` | `pd` | `perf` / `acc` | 同上 |
| 双机 PD 只部署 | `deploy_only` | `pd` | — | 两侧 `network`、各自 `model_paths` |
| 双机 PD + 评测 | `deploy_and_eval` | `pd` | `perf` / `acc` | 同上 |
| 外部服务只评测 | `eval_only` | 无部署 | `perf` / `acc` | `evaluation.service`（示例仅 acc） |

## 2. 定位示例（已有示例的索引，不是支持范围）

先定运行时模式（§0）再定形态（§1），然后查下表。**表里没有的组合不等于不支持**：owned 的其余组合、单机 PD、双机 PD 评测均无示例，按 [contract.md](top-input/contract.md) 组装，不得因缺示例改判运行时模式。

| 任务形态 | owned | existing |
| --- | --- | --- |
| 单机混部只部署 | 契约组装 | [示例](examples/colocated-existing-deploy-only.json) + [playbook](playbooks/colocated-existing-deploy-only.md) |
| 单机混部 + 评测 | [acc](examples/colocated-owned-deploy-eval-acc.json) + [playbook](playbooks/colocated-owned-deploy-eval-acc.md)；perf 按契约组装 | [perf](examples/colocated-existing-deploy-eval-perf.json) / [acc](examples/colocated-existing-deploy-eval-acc.json) + playbook |
| 单机 PD 只部署 / + 评测 | 契约组装 | 契约组装 |
| 双机 PD 只部署 | 契约组装 | [示例](examples/pd-dual-host-existing-deploy-only.json) + [playbook](playbooks/pd-dual-host-existing-deploy-only.md)（当前容器节点写 `self`） |
| 双机 PD + 评测 | 契约组装 | 契约组装 |
| 外部服务只评测 | — | [示例](examples/eval-only-external-acc.json) + [playbook](playbooks/eval-only-external-acc.md) |

existing 的 `self` 是节点标记，不是独立示例：单机示例即唯一节点，双机 PD 示例只标一个节点。`scenarios.json` 与上表同源，场景 id 与示例文件名一一对应，`self` 不进入场景 id；每个示例都必须能通过 `validate-top-input`。`release_only` 是对原部署任务的续接释放，复用原 `task.id`、交付根、runtime 与 nonce，不新建交付根或运行根，不提供示例。

## 3. 读什么

1. 用 §0 定运行时模式，用 §1 定任务形态，用 §2 定位示例或确认需契约组装。
2. 有 example/playbook 时只读这一份；没有时读 [contract.md](top-input/contract.md) 对应小节与 plan 模板，不读其他场景的 playbook。
3. 以 example 为结构起点填写，字段含义按需查 contract.md 对应小节，不必通读。
4. 填完后按 playbook 或契约生成 plan/bindings；派生规则见 [derivation.md](top-input/derivation.md)。
5. 提交前核对 `required_facts` 与 [validation.md](top-input/validation.md) 的通用项。
6. 用 `deploy-eval-v2 validate-top-input --request <top_input.json>` 复核：返回 `{valid, scenario, issues}`，退出码 2 表示仍有缺口。

**只照抄结构，不照抄授权与现场值**：示例中的 `host`、`devices`、路径、端口都是示意值（`192.0.2.0/24` 为文档保留网段）。授权与现场值只能来自派单方输入或现场探测，从示例复制视为伪造授权。

## 4. 入口与产物

- 机器派单：提交按示例填写的 top_input；授权与目标类缺失即拒绝，不进入 plan。
- 自然语言派单：由当前 Agent 归一化为同等内容的 top_input，并一次性确认授权字段；不得从历史产物补全。
- 若你是派单方，只需收齐授权与目标（卡、模型、任务、容器结论、双机 SSH；acc 数据集可省，缺省 `gpqa`，也支持 `gsm8k`）；现场事实与派生项由接单方补齐，不要替它组装 plan/bindings。
- `control.json` 不作为独立入口；由 `nodes` 段生成并保存在交付根 `<deliverables.local_dir>/<task.id>/`，每个 tasks 条目同时写 `root`（节点运行根）与 `task_root`（交付根）。
- 交付根与节点运行根的结构、CLI 的 `destination` 约束与字段级别说明见 [contract.md](top-input/contract.md)。

## 下一步
- 字段与必填条件： [contract.md](top-input/contract.md)
- 派生表： [derivation.md](top-input/derivation.md)
- 校验清单与提交规则： [validation.md](top-input/validation.md)
