# 派单契约（top_input）

top_input 是任务的唯一入口：派单方一次性给出授权与目标，subagent 据此完成 plan → 部署 → 评测 → 释放。它不是 task.json，也不替代冻结产物；agent 仍须自行组装 plan、绑定现场并冻结。

模板见 [top-input.json](../templates/top-input.json)（单机 colocated 骨架）；已填好的示例见 [examples](examples/colocated-deploy-eval-perf.json)，文件名即 `<拓扑>-<动作>[-<评测模式>]`。所有 `{{...}}` 必须由派单方替换，保留占位符视为派单未完成。PD 不要从 colocated 模板手改：单机 PD 以 [examples/pd-single-host-deploy-eval-perf.json](examples/pd-single-host-deploy-eval-perf.json) 起步，双机 PD 以 [examples/pd-dual-host-deploy-eval-perf.json](examples/pd-dual-host-deploy-eval-perf.json) 起步。

## 入口

- 机器派单：提交按模板或示例填写的 top_input；必填缺失即拒绝，不进入 plan。
- 自然语言派单：由当前 Agent 归一化为同等内容的 top_input，并一次性确认授权字段；不得从历史产物补全。
- `control.json` 不作为独立入口；由 `nodes` 段生成并保存在任务目录，字段逐项一致。

## 结构

三层实体，colocated 是 pd 的退化情形：

```
task        → 这次任务是什么
nodes{}     → 哪些机器、各自授权什么
deployment  → 什么拓扑、几个实例、各在哪台机器、各用哪些卡
evaluation  → 在哪评测、评什么、哪些参数被覆盖
```

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | 需求 | 恒填 | 固定 `top-input/2` |
| `task.id` | 需求 | 恒填 | 任务身份；`root` 末段、`control.json` 的 tasks 键、交付目录名都由它派生 |
| `task.action` | 需求 | 恒填 | `deploy_and_eval` / `deploy_only` / `eval_only` / `release_only` |
| `task.eval_mode` | 需求 | action 含评测 | `perf` / `acc` |
| `task.on_idle` | 需求 | 含部署的动作 | 对齐 CLI 的 `policy.on_idle`：`release_owned` / `keep_service`；`deploy_only` 只能是 `keep_service`；`eval_only`、`release_only` 禁用 |
| `task.control_node` | 授权 | 恒填 | 运行 deploy-eval-v2、生成 `control.json` 的节点，必须是 `nodes` 的键；多节点时通过它向其余节点下发 |
| `nodes.<id>` | 授权 | 恒填 | 键名即 bindings 节点标识，`^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`；`eval_only` 时该节点即测试机 |
| `nodes.<id>.host` | 授权 | 恒填 | SSH 目标 |
| `nodes.<id>.allowed_root` | 授权 | 恒填 | 可写根；同时投影为 `control.json` 的 `allowed_root` 与 bindings 的 `work_dir` |
| `nodes.<id>.python` | 授权 | 含部署的动作 | 该节点发布/启动用解释器；缺省 `/usr/bin/python3`。远端运维通道固定用 `/usr/bin/python3`，不可替换 |
| `nodes.<id>.guard_root` | 事实 | 含部署的动作 | 每节点独立；投影为 bindings 的 `guard_root`；`eval_only`/`release_only` 不使用 |
| `nodes.<id>.model_paths` | 事实 | 含部署的动作 | 模型 id → 该节点路径；**按节点**给，PD 两侧可以不同 |
| `nodes.<id>.network` | 事实 | PD 建议 | `{ip, interface}`；`null` 时现场探测并留证 |
| `nodes.<id>.paths` | 事实 | proxy 所在节点必填 | 供 bindings 的 `paths`；proxy 节点必须有 `proxy_program` |
| `nodes.<id>.authorization` | 授权 | 含部署的动作 | `{preempt_other_processes, create_work_root}`，按主机生效。**不是 CLI 字段**：CLI 无同名参数，它只授权 agent 的现场动作（是否可结束他人在跑的进程、是否可创建可写根），不进入 plan/bindings |
| `deployment.id` | 需求 | 含部署的动作 | bindings 以 `nodes.<n>.deployments[<deployment.id>]` 索引；与 `task.id` 相同即可 |
| `deployment.topology` | 需求 | 含部署的动作 | `colocated` / `pd` |
| `deployment.model` | 需求 | 含部署的动作 | `id` 用于取 `model_paths`；`served_name` 传给 vLLM |
| `deployment.image` | 事实 | 可选 | 一个部署只有一个镜像，所有节点共用；填了须逐节点核验为同一 image id |
| `deployment.kv_transfer.connector` | 需求 | `topology=pd` | 全连接统一 connector |
| `deployment.instances[]` | 需求 | 含部署的动作 | `{id, role, node, devices, args, ports}`；`proxy` 没有 `devices` |
| `deployment.endpoint` | 需求 | 含部署的动作 | `{instance, port}`；colocated 指向 `mixed`，pd 必须指向 `proxy` |
| `deployment.instances[].ports` | 事实 | 可选 | 该实例完整的 `{名称: 整数}`；名称集不能增删，只能改值 |
| `evaluation.tester` | 需求 | action 含评测 | `{host, work_root, python, aisbench, model_path, datasets}`，与 bindings 的 testers 同形 |
| `evaluation.dataset` | 需求 | `eval_mode=acc` | `{id, split, scope}`；`perf` 必须为 `null` |
| `evaluation.service` | 需求 | `action=eval_only` | `{id, endpoint, model}`；`id` 缺省由 CLI 按 endpoint+model 派生；其余 action 禁用 |
| `evaluation.params` | 覆盖 | 可选 | 键名必须是模板 case 已声明的字段，否则拒绝 |
| `deliverables.local_dir` | 需求 | 恒填 | **父目录**；实际目录为 `local_dir/task.id` |
| `constraints` | 需求 | 恒填 | 只写不作为授权的护栏 |

级别含义：**需求**（要什么）、**授权**（允许动什么，不得扩大）、**事实**（派单方已确认的现场值，填了即用，只核验存在性与冲突）、**覆盖**（会改变模板默认值，必须进审阅摘要）。

## 占位符

| 占位符 | 替换为 |
| --- | --- |
| `{{task.id}}` | 任务标识 |
| `{{node.id}}` | 部署节点标识，同时作为 `nodes` 键 |
| `{{node.host}}` | 该节点 SSH 目标 |
| `{{node.allowed_root}}` | 该节点可写根 |
| `{{node.python}}` | 该节点框架解释器 |
| `{{node.guard_root}}` | 该节点 guard 根目录 |
| `{{model.id}}` | 模型标识，同时作为 `model_paths` 键 |
| `{{node.model_path}}` | 该节点上的模型权重路径 |
| `{{model.served_name}}` | 服务模型名 |
| `{{device.0}}` `{{device.1}}` | 授权卡编号；替换后必须是 JSON 数字 |
| `{{tester.host}}` | 测试机 SSH 目标 |
| `{{tester.work_root}}` | 测试机工作根 |
| `{{tester.python}}` | AISBench 解释器 |
| `{{tester.aisbench}}` | AISBench 可执行文件 |
| `{{tester.model_path}}` | 测试机看到的模型路径 |
| `{{deliverables.local_dir}}` | 本地产物父目录 |

## 目标矩阵

`task.action` 已把“是否部署 / 是否评测 / 是否释放”组合进一个枚举，不另设布尔位。

| action | 先从哪进入 | CLI 入口 | 是否新部署 | 是否评测 | `task.on_idle` |
| --- | --- | --- | --- | --- | --- |
| `deploy_and_eval` | plan | `freeze-inputs` → `deploy` → `eval` | 是 | 由 `eval_mode` 决定 | `release_owned` 或 `keep_service` |
| `deploy_only` | plan | `prepare-inputs(stage=deploy)` → `deploy` | 是 | 否 | **必须** `keep_service` |
| `eval_only` | plan | `prepare-inputs(stage=eval)` → `eval` | 否（接入已有服务） | 是 | 禁用；CLI 自动 `keep_external_service` |
| `release_only` | release | `release` | 否 | 否 | 禁用；只释放 `task.id` 自有资源 |

`release_only` 不读 plan/deployment/eval 技能；其余三种都必须先完成 plan 的冻结与 readiness。

## 拓扑矩阵

| 形态 | `nodes` | `instances` | 端点 | 额外必填 |
| --- | --- | --- | --- | --- |
| 单机 colocated | 1 | 1 × `mixed` | `mixed` 实例 | 无 |
| 单机 PD | 1 | `prefill` + `decode` + `proxy` | `proxy` | `kv_transfer.connector`、proxy 节点 `paths.proxy_program` |
| 双机 PD | 2 | `prefill`（节点 p）+ `decode`（节点 d）+ `proxy`（任一节点） | `proxy` | 同上，外加两侧 `network` 与各自 `model_paths` |

PD 的连接由 agent 派生，不在 top_input 表达：`kv_transfer` 覆盖所有 P/D 对，`routing` 只有一张表。

## 术语区分

- **任务 id**：`task.id`，代表一次部署/评测的生命周期，决定 `root`、tasks 键与交付目录名。
- **评测用例 `case_id`**：plan 模板中的字段（如 `gpqa_diamond_acc`），代表评测用例身份，由模板决定；top_input 不指定，也不得用 `task.id` 覆盖。
- **两个解释器**：`nodes.<n>.python` 只跑 deploy-eval-v2；`evaluation.tester.python` 跑 AISBench。两者通常是不同 venv，不得互相替代。

## 派生

以下值 top_input 不写，由 agent 依据结构推导；推导结果进审阅摘要。

| 派生项 | 来源 |
| --- | --- |
| `tensor-parallel-size` | `len(instances[i].devices)`；`args` 中出现即视为冲突 |
| `connections.kv_transfer` | 所有 (prefill, decode) 两两配对，connector 取 deployment 级 |
| `connections.routing` | 唯一 proxy + 全部 prefill + 全部 decode |
| `launch.kv_transfer_config` | `kv_role` 按角色、`kv_port` 取 `ports.kv`、`engine_id` = `deployment.id-instance.id` |
| `launch.program/adapter/preserve_reference/checks` | 按角色固定 |
| `bindings.nodes.<n>.deployments[].network/ports` | `nodes.network` + `instances[].ports` |
| `bindings.testers.<id>` | `evaluation.tester` 逐字段投影 |
| 任务目录 `root` | 恒为 `allowed_root/task.id`（CLI 固定，不可单独指定） |
| `policy.on_idle` | `task.on_idle` |
| `control.json` 的 tasks 条目 | `nodes[control_node]` 的 `host` / `root` / `allowed_root` / `python`，并在其 `nodes` 下补齐其余节点的同名四项 |
| `control.json` 的 `tester` 目标 | `evaluation.tester` 的 host、`work_root/task.id`、`python` |
| `bindings.nodes.<n>.work_dir` | `nodes.<n>.allowed_root` |
| 其余节点绑定 | `nodes` 逐节点投影为 bindings 的 `nodes`，不经 `control.json` |
| 交付目录 | `deliverables.local_dir/task.id` |

## 校验

提交前逐条核对，任一不满足即停止并向派单方回报，不自行修正：

1. `task.id` 匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`；各节点 `root` 固定为 `allowed_root/task.id`，交付目录名、tasks 键与它一致。
2. `colocated` ⇒ 恰好 1 个 `mixed`、无其它角色、无 `kv_transfer`、endpoint 指向它。
3. `pd` ⇒ 至少 1 个 `prefill`、1 个 `decode`、**恰好 1 个 `proxy`**、无 `mixed`、endpoint 指向 proxy。
4. 所有 `prefill`/`decode` 的设备数必须相同；否则 KV 并行签名不一致，CLI 会拒绝。
5. 同一节点上各实例的 `devices` 互不相交；合计等于该节点容器拿到的 NPU 数。
6. 同一节点上各实例的端口值互不重叠。
7. 实例 `ports` 名称集按角色固定：`mixed` 为 `{http}`，`prefill`/`decode` 为 `{http, kv}`，`proxy` 为 `{http}`。
8. `devices` 元素为非负整数且互不重复；`proxy` 没有 `devices`。
9. `args` 不得包含 `host`、`port`、`served-model-name`、`kv-transfer-config`、`tensor-parallel-size`。
10. 每个实例的 `node` 必须在 `nodes` 中；除 `task.control_node` 外，`nodes` 的每个键都必须被某个实例使用。
11. 每个承载模型实例的节点都必须有 `model_paths[deployment.model.id]`。
12. `host` 去掉 user 后互不相同；不同节点不得指向同一台机器。
13. `guard_root` 不在任务目录内；文档级要求同时不在 `allowed_root` 内（CLI 只校验前者）。
14. PD 跨节点时两侧 `network.ip` 必须互相可达、connector 必须在两侧镜像中都可用；否则在审阅阶段停止。
15. `on_idle` 与 action 对齐：`deploy_only` 必须 `keep_service`；`deploy_and_eval` 二选一；`eval_only`、`release_only` 不得出现该字段。
16. `eval_only` 的 `evaluation.tester.host` 必须等于 `nodes[control_node].host`，且 `evaluation.service` 必填 `endpoint` 与 `model`。
17. `release_only` 只能用于本 CLI 已部署、状态为 READY/EVALUATED 的 owned 服务；`nodes` 必须与部署时的冻结绑定一致，不得借释放改写目标。

## 示例

| 文件 | action | topology | eval_mode | on_idle | 演示要点 |
| --- | --- | --- | --- | --- | --- |
| [colocated-deploy-eval-perf.json](examples/colocated-deploy-eval-perf.json) | `deploy_and_eval` | `colocated` | `perf` | `release_owned` | 最小可用；`dataset: null`，`ports` 只有 `http` |
| [colocated-deploy-only.json](examples/colocated-deploy-only.json) | `deploy_only` | `colocated` | — | `keep_service` | 只部署并保留服务；无 `evaluation`，走 `prepare-inputs(stage=deploy)` |
| [pd-single-host-deploy-eval-perf.json](examples/pd-single-host-deploy-eval-perf.json) | `deploy_and_eval` | `pd` | `perf` | `release_owned` | 单机切卡、同机端口不重叠、`kv` 端口 |
| [pd-dual-host-deploy-eval-perf.json](examples/pd-dual-host-deploy-eval-perf.json) | `deploy_and_eval` | `pd` | `perf` | `release_owned` | 每节点各自 `model_paths`/`network`/`guard_root`、跨机 `kv`、endpoint 指向 proxy |
| [eval-only-external-acc.json](examples/eval-only-external-acc.json) | `eval_only` | 无部署 | `acc` | 禁用 | 接外部已有服务；单节点仅作控制端与测试机 |
| [release-only.json](examples/release-only.json) | `release_only` | 无部署 | — | 禁用 | 只有 `task.id` 与节点位置，仅释放自有服务 |

**只照抄结构，不照抄授权与现场值**：示例中的 `host`、`devices`、路径、端口都是示意值（`192.0.2.0/24` 为文档保留网段）。授权与现场值只能来自派单方输入或现场探测，从示例复制视为伪造授权。

## 规则

1. 缺失必填项时一次性列出全部缺失字段向用户澄清，不边做边问。
2. `nodes` 段同时投影到两处：`control.json` 的 tasks 条目（含其 `nodes` 与 `tester` 目标）与 bindings 的节点表。不重新推导 `host` / `allowed_root`；`root` 一律由 CLI 固定为 `allowed_root/task.id`，不设覆盖位。
3. 事实类字段非空即视为派单方已确认：只核验存在性与冲突，不重新选值；核验失败时停止回报，不自行替换。为空时才现场发现并留证。
4. `nodes.<n>.guard_root` 直接投影为 bindings 的 `guard_root`，不再自行发现；仍须核验不在任务目录或 `allowed_root` 内、未被其他任务占用（`claims/`、`probes/`）。目录不存在不是阻塞项，发布阶段会创建并做写探测。
5. `eval_mode=acc` 且 `evaluation.dataset` 为 `null` 时停止，不得自选数据集。
6. 不写 case 参数（`concurrency`、`formal_requests`、`timeout_seconds` 等）；用户明确指定时写入 `evaluation.params`，键名必须与模板 case 一致，并在交付中标为“用户显式值”。
7. 不读取历史任务目录、归档或回收站，不从历史产物复制配置。
8. 不得因观测不足放大负载：不能为了提高可见性提高并发、加长输出、增加请求数或延长稳态窗口；负载只来自模板默认值与 `evaluation.params`。
9. 授权字段只能收紧不能扩大：`nodes`、`devices`、`allowed_root`、`preempt_other_processes`、`create_work_root` 只界定下限，不得解释为“可以顺带处理授权外资源”；需要扩大时回报主 Agent。
10. 交付目录只增不改：不覆盖、不改写既有原始输出；`eval_only` 与 `release_only` 是只读入口，不得移动或重写已有数据。

## 交付

按 `task.id` 在 `deliverables.local_dir` 下保存请求、原始输出、证据与问题清单；最终分别报告执行、证据完整性、有效性和清理四项状态。报告须显式给出 `limitations`（缺失证据、覆盖不足、不可比窗口）与 `confidence`（观测事实/可能原因/待验证假设）；部分证据不得表述为完整成功。
