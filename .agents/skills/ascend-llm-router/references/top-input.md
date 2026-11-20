# 派单契约（top_input）

top_input 是任务的唯一入口：派单方一次性给出授权与目标，subagent 据此完成 plan → 部署 → 评测 → 释放。它不是 task.json，也不替代冻结产物；agent 仍须自行组装 plan、绑定现场并冻结。prompt 只负责启动并指向本文件，不承载授权与现场事实。

## 起点

已填示例是起点，不提供占位符骨架；未覆盖的场景以下文“场景矩阵”为准。文件名即场景 `<拓扑>-<容器模式>-<动作>-<评测模式>`（`eval_only` 例外；`release_only` 是同任务续接，不提供独立示例）：

| 示例 | 场景 |
| --- | --- |
| [colocated-existing-deploy-eval-perf.json](examples/colocated-existing-deploy-eval-perf.json) | 借用已有容器的单机混部 + 性能；测试端在容器内 |
| [colocated-existing-deploy-eval-acc.json](examples/colocated-existing-deploy-eval-acc.json) | 借用已有容器的单机混部 + 精度；GPQA-Diamond |
| [colocated-existing-deploy-only.json](examples/colocated-existing-deploy-only.json) | 借用已有容器的单机混部，只部署并保留服务 |
| [pd-dual-host-existing-deploy-only.json](examples/pd-dual-host-existing-deploy-only.json) | 借用已有容器的双机 PD，只部署并保留服务 |
| [eval-only-external-acc.json](examples/eval-only-external-acc.json) | 接外部已有服务的精度评测；测试端在借用容器内 |

`deploy_only` 结束后服务保留；后续释放按“仅释放（同任务续接）”执行，不新起任务。

**只照抄结构，不照抄授权与现场值**：示例中的 `host`、`devices`、路径、端口都是示意值（`192.0.2.0/24` 为文档保留网段）。授权与现场值只能来自派单方输入或现场探测，从示例复制视为伪造授权。

## 入口

- 机器派单：提交按示例填写的 top_input；必填缺失即拒绝，不进入 plan。
- 自然语言派单：由当前 Agent 归一化为同等内容的 top_input，并一次性确认授权字段；不得从历史产物补全。
- `control.json` 不作为独立入口；由 `nodes` 段生成并保存在任务目录，字段逐项一致。

## 任务目录

所有本地产物固定落在任务根内，不写仓库根、当前目录或任意临时路径：

```text
<deliverables.local_dir>/<task.id>/
  control.json
  requests/
  contracts/
  runtime/
  frozen/
  review-draft/
  evidence/
  agent-output/
```

CLI 的 `destination`（`package-runtime`、`draft-review`、`freeze-inputs`、`export-contracts`）必须指向该根下的新文件或新目录；`control.json` 固定为 `<root>/control.json`，`--config` 使用该路径。

## 结构

三层实体，colocated 是 pd 的退化情形：

```text
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
| `task.control_node` | 授权 | 恒填 | 承载任务根与 runtime、并在 `control.json` 中登记为 tasks 条目的节点，必须是 `nodes` 的键；多节点时通过它向其余节点下发。deploy-eval-v2 由控制工作站执行并经 SSH 指向该节点，不等同于“必须登录该节点跑 CLI” |
| `nodes.<id>` | 授权 | 恒填 | 键名即 bindings 节点标识，`^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`；`eval_only` 时该节点即测试机 |
| `nodes.<id>.host` | 授权 | 恒填 | SSH 目标 |
| `nodes.<id>.allowed_root` | 授权 | 恒填 | 可写根；同时投影为 `control.json` 的 `allowed_root` 与 bindings 的 `work_dir` |
| `nodes.<id>.python` | 授权 | 含部署的动作 | 该节点发布/启动用解释器；缺省 `/usr/bin/python3`。必须能用它成功 `from jsonschema import Draft202012Validator`（jsonschema>=4），否则远端 runtime 无法启动；不满足时改用现场已核验的 venv。远端运维通道固定用 `/usr/bin/python3`，不可替换 |
| `nodes.<id>.guard_root` | 事实 | 含部署的动作 | 每节点独立；投影为 bindings 的 `guard_root`；`eval_only`/`release_only` 不使用 |
| `nodes.<id>.model_paths` | 事实 | 含部署的动作 | 模型 id → 该节点路径；**按节点**给，PD 两侧可以不同；existing 模式下填容器内可见路径 |
| `nodes.<id>.network` | 事实 | `existing_container` 与 PD 必填，owned 建议 | `{ip, interface}`；existing 模式下 HCCL 与监听地址依赖它，不得留 `null` 现场猜；owned 下为空才现场探测并留证 |
| `nodes.<id>.paths` | 事实 | proxy 所在节点必填 | 供 bindings 的 `paths`；proxy 节点必须有 `proxy_program` |
| `nodes.<id>.container` | 事实 | 借用已有容器时必填 | `{id, image}`：运行中的容器完整 64 位 ID 与 `sha256:` 镜像摘要；只有 `existing_container` 使用。`NetworkMode`、NPU 可见性由 `docker inspect` 核验，不另设字段 |
| `nodes.<id>.container_work_root` | 事实 | 借用已有容器时必填 | 容器内可写根，POSIX 绝对路径且不含 `:`；脚本与日志位于 `<container_work_root>/<task.id>` |
| `nodes.<id>.authorization` | 授权 | 含部署的动作 | `{preempt_other_processes, create_work_root}`，按主机生效。**不是 CLI 字段**：CLI 无同名参数，它只授权 agent 的现场动作（是否可结束他人在跑的进程、是否可创建可写根），不进入 plan/bindings |
| `deployment.id` | 需求 | 含部署的动作 | bindings 以 `nodes.<n>.deployments[<deployment.id>]` 索引；与 `task.id` 相同即可 |
| `deployment.topology` | 需求 | 含部署的动作 | `colocated` / `pd` |
| `deployment.model` | 需求 | 含部署的动作 | `id` 用于取 `model_paths`，匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（点号等字符不合法，需另给 `served_name`）；`served_name` 传给 vLLM，可保留原始模型名 |
| `deployment.image` | 事实 | 仅 owned，owned 必填 | owned 时填镜像摘要，一个部署一个镜像，所有节点共用；`existing_container` 时**省略**，镜像来自 `nodes.<id>.container.image` |
| `deployment.kv_transfer.connector` | 需求 | `topology=pd` | 全连接统一 connector |
| `deployment.instances[]` | 需求 | 含部署的动作 | `{id, role, node, devices, args, ports}`；`proxy` 没有 `devices` |
| `deployment.endpoint` | 需求 | 含部署的动作 | `{instance, port}`；colocated 指向 `mixed`，pd 必须指向 `proxy` |
| `deployment.instances[].ports` | 事实 | 可选 | 值是**真实端口基址**，原样进入 bindings；top_input 只写 `{http}`（PD 再加 `kv`）。plan 同名位置写的是**跨度宽度**（模板值 1，schema 为 `1..256`），不参与部署；`hccl` 不属于 top_input，由 agent 在 plan 中补键，真实值由 agent 在 bindings 分配，见派生表 |
| `deployment.instances[].args` | 覆盖 | 可选 | 省略即使用启动档模板默认值；显式给出时按实例合并并进审阅摘要 |
| `evaluation.tester` | 需求 | action 含评测 | `{host, work_root, python, aisbench, model_path, machine_id, host_python, datasets}`，与 bindings 的 testers 同形；`machine_id` 虽是可选 schema 字段，但 check-ready 会硬性拒绝缺失值，必须现场取证（`/etc/machine-id`）；`environment` 为可选的环境变量映射，作用于 preflight、AISBench 与 postflight 子进程；容器内测试再加 `container={id,image}` 与 `container_work_root`。同机测试时 `host`/`work_root`/`model_path`/`container`/`container_work_root` 可省略，按派生表补齐 |
| `evaluation.dataset` | 需求 | `eval_mode=acc` | `{id, split, scope}`；`perf` 省略，不写 `null` |
| `evaluation.service` | 需求 | `action=eval_only` | `{service_id?, endpoint, model, health_path?}`；key 名是 `service_id`，写 `id` 会被静默忽略。省略时由 CLI 按 endpoint+model 派生；其余 action 禁用 |
| `evaluation.params` | 覆盖 | 可选 | **不是 CLI 字段**：`prepare-inputs` 不读取它，agent 必须把这些覆盖物化进 `cases[]`（键名须为该 case 已声明字段），并在审阅摘要中标为用户显式值 |
| `deliverables.local_dir` | 需求 | 恒填 | 控制机上的**绝对路径父目录**；实际任务根为 `local_dir/task.id`，见“任务目录” |
| `constraints` | 需求 | 恒填 | 只写不作为授权的护栏 |

级别含义：**需求**（要什么）、**授权**（允许动什么，不得扩大）、**事实**（派单方已确认的现场值，填了即用，只核验存在性与冲突）、**覆盖**（会改变模板默认值，必须进审阅摘要）。

## 场景矩阵

`task.action` 已把“是否部署 / 是否评测 / 是否释放”组合进一个枚举，不另设布尔位。容器模式（owned/existing）与 action、拓扑正交，由 `nodes.<id>.container` 是否存在决定。

“准备链”是含部署或独立评测动作的固定闭环，不可跳过其中任何一步：
`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。`deploy_and_eval` 自行组装 plan/bindings 后直接进入该链；`deploy_only` 与 `eval_only` 先用 `prepare-inputs`（stage 分别为 deploy/eval）生成 plan/bindings，再进入同一条链。字段与产物见 plan 技能（`references/templates.md`、`references/plan-contract.md`）和 CLI 契约。

| 场景 | `action` | `topology` | CLI 入口 | nodes / instances | 额外必填 |
| --- | --- | --- | --- | --- | --- |
| 单机混部 + 评测 | `deploy_and_eval` | `colocated` | 准备链 → `deploy` → `eval` | 1 / 1 × `mixed` | `eval_mode`；existing 还需 `network`、`container`、`container_work_root` |
| 单机 PD + 评测 | `deploy_and_eval` | `pd` | 准备链 → `deploy` → `eval` | 1 / `prefill` + `decode` + `proxy` | `kv_transfer.connector`、proxy 节点 `paths.proxy_program`、`network` |
| 双机 PD + 评测 | `deploy_and_eval` | `pd` | 准备链 → `deploy` → `eval` | 2 / 两侧模型实例 + `proxy` | 同上，外加两侧 `network` 与各自 `model_paths` |
| 单机混部只部署 | `deploy_only` | `colocated` | `prepare-inputs(stage=deploy)` → 准备链 → `deploy` | 1 / 1 × `mixed` | `on_idle=keep_service` |
| 双机 PD 只部署 | `deploy_only` | `pd` | `prepare-inputs(stage=deploy)` → 准备链 → `deploy` | 2 / 两侧模型实例 + `proxy` | 同双机 PD，外加 `on_idle=keep_service` |
| 外部已有服务测试 | `eval_only` | 无部署 | `prepare-inputs(stage=eval)` → 准备链 → `eval` | 1（控制端兼测试机） | `evaluation.service` |
| 仅释放（同任务续接） | `release_only` | 无部署 | `release` | 与部署任务一致 | 复用原 `task.id`、交付根、runtime 与 nonce；不是新任务，不提供独立示例 |

`release_only` 不读 plan/deployment/eval 技能，也不新建任务目录；它是对原部署任务的续接释放。其余 action 都必须先完成 plan 的冻结与 readiness。`deploy_and_eval` 不经过 `prepare-inputs`。

PD 的连接由 agent 派生，不在 top_input 表达：`kv_transfer` 覆盖所有 P/D 对，`routing` 只有一张表。

## 容器模式

`nodes.<id>` 是否带 `container` 决定本任务用哪种运行时，两者不能混用：

| 模式 | top_input 形态 | plan | bindings | 生命周期 |
| --- | --- | --- | --- | --- |
| `owned_container` | 不写 `container`；`deployment.image` 可填镜像摘要 | `runtime={image, container}` | 写 `mounts`；不写 `container` | CLI 创建、启动并在释放时删除容器 |
| `existing_container` | 每节点写 `container={id, image}` 与 `container_work_root`；`network` 必填 | `runtime={"mode":"existing_container"}`，不写 `image`/`container` | 只写 `container` 与 `container_work_root`，不写 `mounts` | 只 inspect/cp/exec；不创建、不启动、不停止、不删除容器 |

借用容器由派单方给出（`nodes.<id>.container`），不得自行创建替代容器。若派单方未给容器而现场存在多个候选，停止并向派单方回报，不按名称猜测。existing 模式下 `model_paths`、`paths`、`network` 等仍按容器内可见性填写；脚本与日志位于 `<container_work_root>/<task.id>`，宿主任务目录只保留状态与可回传产物。工作根目录可能已存在，按 `task.id` 子目录隔离，不复用或读取其他任务内容。

## 派生

以下值 top_input 不写，由 agent 依据结构推导；推导结果进审阅摘要。

| 派生项 | 来源 |
| --- | --- |
| `tensor-parallel-size` | `len(instances[i].devices)`；`args` 中出现即视为冲突 |
| `plan.instances[].ports` 的 `hccl` 键 | mixed/prefill/decode 自动补键，值写跨度宽度 `1`；真实端口只写在 bindings，plan 的端口值不参与部署 |
| HCCL 基端口 | bindings 默认 `64023`，被占用时顺延；`launch.env.HCCL_IF_BASE_PORT` 必须等于 bindings 的 `hccl` 值 |
| `connections.kv_transfer` | 所有 (prefill, decode) 两两配对，connector 取 deployment 级 |
| `connections.routing` | 唯一 proxy + 全部 prefill + 全部 decode |
| `launch.kv_transfer_config` | `kv_role` 按角色、`kv_port` 取 `ports.kv`、`engine_id` = `deployment.id-instance.id` |
| `launch.program/adapter/preserve_reference/checks` | 按角色固定 |
| `bindings.nodes.<n>.deployments[].network/ports` | `nodes.network` + `instances[].ports` + 派生 `hccl` |
| `bindings.testers.<id>` | `evaluation.tester` 逐字段投影；同机缺省：`host`←`nodes[control_node].host`、`work_root`←`allowed_root`、`model_path`←`model_paths[model.id]`、`container`←`nodes[control_node].container`、`container_work_root`←`<container_work_root>/<task.id>-tester`、`host_python`←`/usr/bin/python3` |
| 容器网络模式与 NPU 可见性 | `docker inspect` 的 `NetworkMode` 与 Devices；host 模式才可直接复用宿主 IP，其余模式必须核验容器地址与 NPU 直通 |
| 任务目录 `root` | 约定为 `allowed_root/task.id`；CLI 不推导，必须在 `control.json` 的 tasks 条目与定位该任务的 `nodes` 条目中显式写入同一值 |
| `policy.on_idle` | `task.on_idle` |
| `control.json` 的 tasks 条目 | `nodes[control_node]` 的 `host` / `root` / `allowed_root` / `python`，并在其 `nodes` 下补齐其余节点的同名四项 |
| `control.json` 的 `tester` 目标 | `evaluation.tester` 的 host、`work_root/task.id`、`python`；键名固定为 `host` / `root` / `python` |
| `bindings.nodes.<n>.machine_id` 与 `bindings.testers.<id>.machine_id` | 现场取证宿主 `/etc/machine-id`；两者都是 check-ready 硬门槛，节点缺失返回 `NODE_MACHINE_ID_REQUIRED`。容器内 `/etc/machine-id` 可能为空，借用容器时按宿主值取证并在审阅证据中记录 |
| `bindings.nodes.<n>.work_dir` | `nodes.<n>.allowed_root` |
| `deployment.runtime.mode` | 任一节点有 `container` ⇒ `existing_container`，否则 `owned_container` |
| `deployment.runtime` | owned：`{image, container}`；existing：只写 `{mode:"existing_container"}` |
| `bindings.nodes.<n>.container` / `container_work_root` | `nodes.<n>.container` / `nodes.<n>.container_work_root` 逐字段投影；existing 模式不写 `mounts` |
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
7. top_input 的实例 `ports` 名称集按角色固定：`mixed` 为 `{http}`，`prefill`/`decode` 为 `{http, kv}`，`proxy` 为 `{http}`；`hccl` 不属于 top_input，只在 plan/bindings 中出现。
8. `devices` 元素为非负整数且互不重复；`proxy` 没有 `devices`。
9. `args` 不得包含 `host`、`port`、`served-model-name`、`kv-transfer-config`、`tensor-parallel-size`。
10. 每个实例的 `node` 必须在 `nodes` 中；除 `task.control_node` 外，`nodes` 的每个键都必须被某个实例使用。
11. 每个承载模型实例的节点都必须有 `model_paths[deployment.model.id]`。
12. `host` 去掉 user 后互不相同；不同节点不得指向同一台机器。
13. `guard_root` 不在任务目录内；文档级要求同时不在 `allowed_root` 内（CLI 只校验前者）。
14. PD 跨节点时两侧 `network.ip` 必须互相可达、connector 必须在两侧镜像中都可用；否则在审阅阶段停止。
15. `on_idle` 与 action 对齐：`deploy_only` 必须 `keep_service`；`deploy_and_eval` 二选一；`eval_only`、`release_only` 不得出现该字段。
16. `eval_only` 的 `evaluation.tester.host` 必须等于 `nodes[control_node].host`，且 `evaluation.service` 必填 `endpoint` 与 `model`。
17. `release_only` 是同一部署任务的续接入口，不是新任务：必须复用原 `task.id`、交付根、原 runtime 与 nonce，`nodes` 与部署时的冻结绑定一致（existing 模式含 `container`、`container_work_root`）；服务须为 READY/EVALUATED 且 `service.ownership=owned`（owned 与 existing 容器都满足），不得借释放改写目标。原任务 runtime 已不存在时停止回报，不新建任务释放。
18. 容器模式全节点一致：已有容器时每个节点都必须有 `container` 与 `container_work_root`，不得与 owned 混用；owned 模式下出现这两个字段即拒绝。
19. `container.id` 匹配 `^[a-f0-9]{64}$`，`container.image` 匹配 `^sha256:[a-f0-9]{64}$`；冻结与部署前用 `docker inspect` 核对 ID、镜像与 `Running=true`，任一不符即停止。
20. `container_work_root` 为容器内绝对路径，不含 `:` 与 `..`，且在容器内可写、不与已有服务或他人目录重叠；宿主 `allowed_root` 只保存任务状态与可回传产物。
21. existing 模式下 `devices` 是宿主物理卡号，必须确认容器内确实可见这些 NPU（`npu-smi info` 或 `/dev/davinciN`）；不可把宿主卡号当成容器内逻辑编号，也不得因此假定容器可被重建。
22. `nodes.<id>.python` 必须现场验证 `from jsonschema import Draft202012Validator` 成功；只有 `jsonschema` 3.x 的解释器不能用作发布/启动解释器。解释器不满足时先向派单方回报并给出已核验的候选，不自行安装依赖。
23. 同机 tester 使用派生值时，派生结果必须与节点事实一致；`host_python` 缺省 `/usr/bin/python3`，显式给出时必须存在且可执行。容器内 tester 的 `container` 必须与所属节点同一 ID/镜像。

## 示例

| 文件 | action | topology | eval_mode | on_idle | 演示要点 |
| --- | --- | --- | --- | --- | --- |
| [colocated-existing-deploy-eval-perf.json](examples/colocated-existing-deploy-eval-perf.json) | `deploy_and_eval` | `colocated` | `perf` | `release_owned` | 借用已有容器；测试端在容器内；`network`、`container`、`container_work_root`、`host_python` 完整 |
| [colocated-existing-deploy-eval-acc.json](examples/colocated-existing-deploy-eval-acc.json) | `deploy_and_eval` | `colocated` | `acc` | `release_owned` | 同上，加 GPQA 数据集与 `evaluation.dataset` |
| [colocated-existing-deploy-only.json](examples/colocated-existing-deploy-only.json) | `deploy_only` | `colocated` | — | `keep_service` | 借用已有容器；只部署保留；无 `evaluation` |
| [pd-dual-host-existing-deploy-only.json](examples/pd-dual-host-existing-deploy-only.json) | `deploy_only` | `pd` | — | `keep_service` | 两台机器各自 `container` 与 `network`；proxy 落在 `control_node`；`kv_transfer` 与 `paths.proxy_program` 完整 |
| [eval-only-external-acc.json](examples/eval-only-external-acc.json) | `eval_only` | 无部署 | `acc` | 禁用 | 接外部已有服务；测试端在借用容器内，`tester.container` 与 `container_work_root` 完整 |

## 规则

1. 缺失必填项时一次性列出全部缺失字段向用户澄清，不边做边问。
2. `nodes` 段同时投影到两处：`control.json` 的 tasks 条目（含其 `nodes` 与 `tester` 目标）与 bindings 的节点表。不重新推导 `host` / `allowed_root`；`root` 采用约定值 `allowed_root/task.id`，并逐项显式写入这两处，不设覆盖位。
3. 事实类字段非空即视为派单方已确认：只核验存在性与冲突，不重新选值；核验失败时停止回报，不自行替换。为空时才现场发现并留证；existing 模式下 `network` 不得为空。
4. `nodes.<n>.guard_root` 直接投影为 bindings 的 `guard_root`，不再自行发现；仍须核验不在任务目录或 `allowed_root` 内、未被其他任务占用（`claims/`、`probes/`）。目录不存在不是阻塞项，发布阶段会创建并做写探测。
5. `eval_mode=acc` 且 `evaluation.dataset` 缺失时停止，不得自选数据集。
6. 不写 case 参数（`concurrency`、`formal_requests`、`timeout_seconds` 等）；用户明确指定时写入 `evaluation.params`，键名必须与模板 case 一致，并在交付中标为“用户显式值”。
7. 不读取历史任务目录、归档或回收站，不从历史产物复制配置。
8. 不得因观测不足放大负载：不能为了提高可见性提高并发、加长输出、增加请求数或延长稳态窗口；负载只来自模板默认值与 `evaluation.params`。
9. 授权字段只能收紧不能扩大：`nodes`、`devices`、`allowed_root`、`preempt_other_processes`、`create_work_root` 只界定下限，不得解释为“可以顺带处理授权外资源”；需要扩大时回报主 Agent。
10. 交付目录只增不改：不覆盖、不改写既有原始输出；`eval_only` 与 `release_only` 是只读入口，不得移动或重写已有数据。

## 交付

按 `task.id` 在 `deliverables.local_dir` 下保存请求、原始输出、证据与问题清单；最终分别报告执行、证据完整性、有效性和清理四项状态。报告须显式给出 `limitations`（缺失证据、覆盖不足、不可比窗口）与 `confidence`（观测事实/可能原因/待验证假设）；部分证据不得表述为完整成功。
