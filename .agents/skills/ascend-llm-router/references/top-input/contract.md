# top_input 字段契约

字段级别（**需求** / **授权** / **事实** / **覆盖**）与入口规则见 [../top-input.md](../top-input.md)。本文件只回答“这个字段写什么、什么时候必填”。

## task

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | 需求 | 恒填 | 固定 `top-input/2` |
| `task.id` | 需求 | 恒填 | 任务身份；`root` 末段、`control.json` 的 tasks 键、交付目录名都由它派生 |
| `task.action` | 需求 | 恒填 | `deploy_and_eval` / `deploy_only` / `eval_only` / `release_only` |
| `task.eval_mode` | 需求 | action 含评测 | `perf` / `acc` |
| `task.on_idle` | 需求 | 含部署的动作 | 对齐 CLI 的 `policy.on_idle`：`release_owned` / `keep_service`；`deploy_only` 只能是 `keep_service`；`eval_only`、`release_only` 禁用 |
| `task.control_node` | 授权 | 恒填 | 承载任务根与 runtime、并在 `control.json` 中登记为 tasks 条目的节点，必须是 `nodes` 的键；多节点时通过它向其余节点下发。existing 时该节点就是 `container.self=true` 的所在容器（单机即唯一节点，双机 PD 即当前容器），CLI 本地执行；owned 时 CLI 在工作站经 SSH 指向该节点宿主并创建容器 |

## nodes

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `nodes.<id>` | 授权 | 恒填 | 键名即 bindings 节点标识，`^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`；`eval_only` 时该节点即测试机 |
| `nodes.<id>.host` | 授权 | 恒填 | SSH 目标；`container.self=true` 的节点只作登记，不走 SSH |
| `nodes.<id>.allowed_root` | 授权 | 恒填 | 可写根；同时投影为 `control.json` 的 `allowed_root` 与 bindings 的 `work_dir` |
| `nodes.<id>.python` | 授权 | 含部署的动作 | 该节点发布/启动用解释器；缺省 `/usr/bin/python3`。必须能用它成功 `from jsonschema import Draft202012Validator`（jsonschema>=4），否则远端 runtime 无法启动；不满足时改用现场已核验的 venv。远端运维通道固定用 `/usr/bin/python3`，不可替换 |
| `nodes.<id>.guard_root` | 事实 | 含部署的动作 | 每节点独立；投影为 bindings 的 `guard_root`；`eval_only`/`release_only` 不使用 |
| `nodes.<id>.model_paths` | 事实 | 含部署的动作 | 模型 id → 该节点路径；**按节点**给，PD 两侧可以不同；existing 模式下填容器内可见路径 |
| `nodes.<id>.network` | 事实 | `existing_container` 必填；PD 跨机两侧须互通 | `{ip, interface}`；existing 模式下 HCCL 与监听地址依赖它，不得留 `null` 现场猜；owned 下未给才现场探测并留证（PD 同样适用） |
| `nodes.<id>.paths` | 事实 | proxy 所在节点必填 | 供 bindings 的 `paths`；proxy 节点必须有 `proxy_program` |
| `nodes.<id>.container` | 事实 | 借用已有容器时必填 | `{id, image}`，当前所在容器再加 `self: true`：运行中的容器完整 64 位 ID；`image` 为 `sha256:` 镜像摘要，`self=true` 时可省略（容器内无 docker，无法核验）；只有 `existing_container` 使用。`NetworkMode`、NPU 可见性：`self` 节点本地核对，其余节点由 `docker inspect` 核验，不另设字段 |
| `nodes.<id>.container.self` | 授权 | `existing_container` 必填 | 只能写 `true`：标记控制进程当前所在的容器。该节点必须是 `task.control_node`，全任务恰好一个（单机即唯一节点，双机 PD 即当前所在节点）。该节点与控制进程同容器，本地执行，不经 SSH 与 docker；其余 existing 节点仍经 SSH + docker。`id` 必须自证；`image` 可省略，给出但无法核验时证据记 `UNVERIFIED` |
| `nodes.<id>.container_work_root` | 事实 | 借用已有容器时必填 | 容器内可写根，POSIX 绝对路径且不含 `:`；脚本与日志位于 `<container_work_root>/<task.id>` |
| `nodes.<id>.authorization` | 授权 | 含部署的动作 | `{preempt_other_processes, create_work_root}`，按主机生效。**不是 CLI 字段**：CLI 无同名参数，它只授权 agent 的现场动作（是否可结束他人在跑的进程、是否可创建可写根），不进入 plan/bindings。派单给出 `deliverables.local_dir` 即视为允许创建 `local_dir/<task.id>` 及其任务子目录；仍不得创建授权外的根 |

## deployment

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `deployment.id` | 需求 | 含部署的动作 | bindings 以 `nodes.<n>.deployments[<deployment.id>]` 索引；与 `task.id` 相同即可 |
| `deployment.topology` | 需求 | 含部署的动作 | `colocated` / `pd` |
| `deployment.model` | 需求 | 含部署的动作 | `id` 用于取 `model_paths`，匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（点号等字符不合法，需另给 `served_name`）；`served_name` 传给 vLLM，可保留原始模型名 |
| `deployment.image` | 事实 | 仅 owned，owned 必填 | owned 时填镜像摘要，一个部署一个镜像，所有节点共用；`existing_container` 时**省略**，镜像来自 `nodes.<id>.container.image` |
| `deployment.kv_transfer.connector` | 需求 | `topology=pd` | 全连接统一 connector |
| `deployment.instances[]` | 需求 | 含部署的动作 | `{id, role, node, devices, args, ports}`；`proxy` 没有 `devices` |
| `deployment.endpoint` | 需求 | 含部署的动作 | `{instance, port}`；colocated 指向 `mixed`，pd 必须指向 `proxy` |
| `deployment.instances[].ports` | 事实 | 可选 | 值是**真实端口基址**，原样进入 bindings；top_input 只写 `{http}`（PD 再加 `kv`）。plan 同名位置写的是**跨度宽度**（模板值 1，schema 为 `1..256`），不参与部署；`hccl` 不属于 top_input，由 agent 在 plan 中补键，真实值由 agent 在 bindings 分配，见派生表 |
| `deployment.instances[].args` | 覆盖 | 可选 | 省略即使用启动档模板默认值；显式给出时按实例合并并进审阅摘要 |

## evaluation

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `evaluation.tester` | 需求 | action 含评测 | `{host, work_root, python, aisbench, model_path, machine_id, host_python, datasets}`，与 bindings 的 testers 同形；`machine_id` 虽是可选 schema 字段，但 check-ready 会硬性拒绝缺失值，必须现场取证（`/etc/machine-id`）；`environment` 为可选的环境变量映射，作用于 preflight、AISBench 与 postflight 子进程；容器内测试再加 `container={id,image?}` 与 `container_work_root`；`self` 节点可省略 `image`，非 self 节点仍须给出摘要。同机测试的缺省值可先按派生表补齐，但补齐结果必须显式写回 top_input：`host`/`work_root`/`python`/`aisbench`/`model_path`/`datasets` 任一缺失都会被 `validate-top-input` 拒绝；测试在容器内时 `container` 与 `container_work_root` 也必须显式写出（同机也一样），给出 `container` 而未给 `container_work_root` 即拒绝 |
| `evaluation.dataset` | 需求 | `eval_mode=acc` | `{id, split, scope}`；`perf` 省略，不写 `null` |
| `evaluation.service` | 需求 | `action=eval_only` | `{service_id?, endpoint, model, health_path?}`；key 名是 `service_id`，写 `id` 会被静默忽略。省略时由 CLI 按 endpoint+model 派生；其余 action 禁用 |
| `evaluation.params` | 覆盖 | 可选 | **不是 CLI 字段**：`prepare-inputs` 不读取它，agent 必须把这些覆盖物化进 `cases[]`（键名须为该 case 已声明字段），并在审阅摘要中标为用户显式值 |

## deliverables / constraints

| 字段 | 级别 | 必填条件 | 说明 |
| --- | --- | --- | --- |
| `deliverables.local_dir` | 需求 | 恒填 | 控制机上的**绝对路径父目录**；实际任务根为 `local_dir/task.id`，见任务目录 |
| `constraints` | 需求 | 恒填 | 只写不作为授权的护栏 |

## action 与拓扑约束

`task.action` 已把“是否部署 / 是否评测 / 是否释放”组合进一个枚举，不另设布尔位。容器模式（owned/existing）与 action、拓扑正交：**先按现场事实与授权判定模式**（判定问题见 [top-input.md](../top-input.md) §0），再决定是否写 `nodes.<id>.container`；写不写只是模式的投影，不是选择模式的手段。

“准备链”是含部署或独立评测动作的固定闭环，不可跳过其中任何一步：
`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。`deploy_and_eval` 自行组装 plan/bindings 后直接进入该链；`deploy_only` 与 `eval_only` 先用 `prepare-inputs`（stage 分别为 deploy/eval）生成 plan/bindings，再进入同一条链。字段与产物见 plan 技能（`references/templates.md`、`references/plan-contract.md`）和 CLI 契约。

| 场景 | `action` | `topology` | CLI 入口 | nodes / instances | 额外必填 |
| --- | --- | --- | --- | --- | --- |
| 单机混部 + 评测 | `deploy_and_eval` | `colocated` | 准备链 → `deploy` → `eval` | 1 / 1 × `mixed` | `eval_mode`；existing 还需 `network`、`container`、`container_work_root` |
| 单机 PD + 评测 | `deploy_and_eval` | `pd` | 准备链 → `deploy` → `eval` | 1 / `prefill` + `decode` + `proxy` | `kv_transfer.connector`、proxy 节点 `paths.proxy_program`、`network` |
| 双机 PD + 评测 | `deploy_and_eval` | `pd` | 准备链 → `deploy` → `eval` | 2 / 两侧模型实例 + `proxy` | 同上，外加两侧 `network` 与各自 `model_paths` |
| 单机混部只部署 | `deploy_only` | `colocated` | `prepare-inputs(stage=deploy)` → 准备链 → `deploy` | 1 / 1 × `mixed` | `on_idle=keep_service` |
| 单机 PD 只部署 | `deploy_only` | `pd` | `prepare-inputs(stage=deploy)` → 准备链 → `deploy` | 1 / `prefill` + `decode` + `proxy` | `kv_transfer.connector`、proxy 节点 `paths.proxy_program`、`on_idle=keep_service` |
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
| `existing_container` | 每节点写 `container={id, image}` 与 `container_work_root`；`network` 必填；其中当前所在节点写 `container.self=true` | `runtime={"mode":"existing_container"}`，不写 `image`/`container` | 只写 `container` 与 `container_work_root`，不写 `mounts` | 只 inspect/cp/exec；不创建、不启动、不停止、不删除容器；`self` 节点本地执行，不对自身容器做 SSH/docker |

`existing_container` 必须恰好一个 `container.self=true`，且该节点是 `task.control_node`：单机时就是唯一节点，双机 PD 时就是当前所在的容器。其余节点不写 `self`，仍经 SSH + docker 进入。

借用容器由派单方给出（`nodes.<id>.container`），不得自行创建替代容器。existing 默认就在容器内；若派单方未给容器而现场存在多个候选，停止并向派单方回报，不按名称猜测。existing 模式下 `model_paths`、`paths`、`network` 等仍按容器内可见性填写；脚本与日志位于 `<container_work_root>/<task.id>`，宿主任务目录只保留状态与可回传产物。工作根目录可能已存在，按 `task.id` 子目录隔离，不复用或读取其他任务内容。

existing 中 `container.self` 只描述当前所在容器，不是“同机不同容器”的独立形态：该节点与控制进程同容器，控制→runtime 与 runtime→容器都走本地；其余节点不变，仍按 existing 经 SSH 与 docker exec。runtime 会用 `/.dockerenv` 自证在容器内，缺失即失败；镜像摘要无法复核，证据标 `UNVERIFIED`。

`container.self` 节点的 `allowed_root`、`guard_root`、`container_work_root`、`deliverables.local_dir` 以及由 `allowed_root/task.id` 派生的节点 `root` 一律按**容器内路径**填写，`evaluation.tester.work_root` 与 `allowed_root` 保持一致。宿主路径与容器内路径不同时填宿主路径，会在 `check-ready` 的本地 root 绑定比对处失败。existing 的节点与容器内 tester 的 `machine_id` 取容器内 `/etc/machine-id`，不是宿主值；owned 取宿主值。

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

## 字段级别

- **需求**：要什么。
- **授权**：允许动什么，不得扩大。
- **事实**：派单方已确认的现场值，填了即用，只核验存在性与冲突。
- **覆盖**：会改变模板默认值，必须进审阅摘要。

结构分三层，colocated 是 pd 的退化情形：

```text
task        → 这次任务是什么
nodes{}     → 哪些机器、各自授权什么
deployment  → 什么拓扑、几个实例、各在哪台机器、各用哪些卡
evaluation  → 在哪评测、评什么、哪些参数被覆盖
```
