# 派单契约（top_input）

top_input 是任务的唯一入口：派单方一次性给出授权与目标，subagent 据此完成 plan → 部署 → 评测 → 释放。它不是 task.json，也不替代冻结产物；agent 仍须自行组装 plan、绑定现场并冻结。prompt 只负责启动并指向本文件，不承载授权与现场事实。

## 0. 先判定环境模式（决定骨架，判错整单作废）

容器模式是授权与现场事实，不是风格选择。按顺序问，答不上就停：

| # | 问题 | 是 | 否 |
| --- | --- | --- | --- |
| Q1 | 现场是否已有本次要用的运行中容器，且派单方给了容器 id 与镜像摘要？ | **existing**：每节点写 `container={id,image}`、`container_work_root`，`network` 必填 | 进入 Q2 |
| Q2 | 派单方是否明确授权 CLI 创建并在释放时删除容器？ | **owned**：不写 `container`，写 `deployment.image` | **停止**：不得自行创建，也不得伪造容器 |
| Q3 | CLI 是否就在 Q1 那个容器内执行（存在 `/.dockerenv`、无法 inspect 自身、且是 `control_node`）？ | **self**：existing + 该节点 `container.self=true`，至多一个 | **existing**（外部经 SSH + docker exec） |

- owned 与 existing 全节点一致，混用即拒绝；`owned + self` 不存在，self 只是 existing 的执行通道。
- 现场有多个候选容器而派单方未指定时，停止回报，不按名称猜。
- 三种模式的字段差异与生命周期见 [contract.md 容器模式](top-input/contract.md)。

## 1. 再判定任务形态

`action` → `topology` → `eval_mode`，与容器模式正交：

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

先定环境（§0）再定形态（§1），然后查下表。**表里没有的组合不等于不支持**：owned 的其余组合、单机 PD、self 的 perf 均无示例，按 [contract.md](top-input/contract.md) 组装，不得因缺示例改判环境模式。

| 任务形态 | owned | existing | self |
| --- | --- | --- | --- |
| 单机混部只部署 | 契约组装 | [示例](examples/colocated-existing-deploy-only.json) + [playbook](playbooks/colocated-existing-deploy-only.md) | 契约组装 |
| 单机混部 + 评测 | [acc](examples/colocated-owned-deploy-eval-acc.json) + [playbook](playbooks/colocated-owned-deploy-eval-acc.md)；perf 按契约组装 | [perf](examples/colocated-existing-deploy-eval-perf.json) / [acc](examples/colocated-existing-deploy-eval-acc.json) + playbook | [acc](examples/colocated-existing-self-deploy-eval-acc.json) + [playbook](playbooks/colocated-existing-self-deploy-eval-acc.md)；perf 按契约组装 |
| 单机 PD 只部署 / + 评测 | 契约组装 | 契约组装 | 契约组装 |
| 双机 PD 只部署 | 契约组装 | [示例](examples/pd-dual-host-existing-deploy-only.json) + [playbook](playbooks/pd-dual-host-existing-deploy-only.md) | 契约组装 |
| 双机 PD + 评测 | 契约组装 | 契约组装 | 契约组装 |
| 外部服务只评测 | — | [示例](examples/eval-only-external-acc.json) + [playbook](playbooks/eval-only-external-acc.md) | — |

[scenarios.json](scenarios.json) 与上表同源，场景 id 与示例文件名一一对应；每个示例都必须能通过 `validate-top-input`。`release_only` 是对原部署任务的续接释放，复用原 `task.id`、交付根、runtime 与 nonce，不新建任务目录，不提供示例。

## 3. 读什么

1. 用 §0 定环境模式，用 §1 定任务形态，用 §2 定位示例或确认需契约组装。
2. 有 example/playbook 时只读这一份；没有时读 [contract.md](top-input/contract.md) 对应小节与 plan 模板，不读其他场景的 playbook。
3. 以 example 为结构起点填写，字段含义按需查 contract.md 对应小节，不必通读。
4. 填完后按 playbook 或契约生成 plan/bindings；派生规则见 [derivation.md](top-input/derivation.md)。
5. 提交前核对 `required_facts` 与 [validation.md](top-input/validation.md) 的通用项。
6. 用 `deploy-eval-v2 validate-top-input --request <top_input.json>` 复核：返回 `{valid, scenario, issues}`，退出码 2 表示仍有缺口。

**只照抄结构，不照抄授权与现场值**：示例中的 `host`、`devices`、路径、端口都是示意值（`192.0.2.0/24` 为文档保留网段）。授权与现场值只能来自派单方输入或现场探测，从示例复制视为伪造授权。

## 4. 入口与产物

- 机器派单：提交按示例填写的 top_input；必填缺失即拒绝，不进入 plan。
- 自然语言派单：由当前 Agent 归一化为同等内容的 top_input，并一次性确认授权字段；不得从历史产物补全。
- 若你是派单方，先按 [dispatch-kit](../../../dispatch-kit/README.md) 的最小充分集向用户收齐环境结论、任务形态、现场事实与授权，缺项一次列全；不要替 subagent 组装 plan/bindings。
- `control.json` 不作为独立入口；由 `nodes` 段生成并保存在 `<deliverables.local_dir>/<task.id>/`。
- 任务目录结构、CLI 的 `destination` 约束与字段级别说明见 [contract.md](top-input/contract.md)。

## 下一步

- 字段与必填条件： [contract.md](top-input/contract.md)
- 派生表： [derivation.md](top-input/derivation.md)
- 校验清单与提交规则： [validation.md](top-input/validation.md)
