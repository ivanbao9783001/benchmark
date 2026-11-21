# 派生规则

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
| `bindings.testers.<id>` | `evaluation.tester` 逐字段投影；同机缺省：`host`←`nodes[control_node].host`、`work_root`←`allowed_root`、`model_path`←`model_paths[model.id]`、`container`←`nodes[control_node].container`、`container_work_root`←`<container_work_root>/<task.id>-tester`、`host_python`←`/usr/bin/python3`。缺省只用于补齐，**补齐后的值必须显式写回 top_input**（校验不替你读缺省）；显式值优先于派生值；`container_work_root` 派生值按任务隔离，不复用固定目录 |
| 容器网络模式与 NPU 可见性 | existing 的 `self` 节点在容器内核对网络与 `/dev/davinciN`；其余 existing 节点经 SSH + `docker inspect` 核验 `NetworkMode` 与 Devices；owned 由 CLI 创建，不预检宿主 IP |
| 任务目录 `root` | 约定为 `allowed_root/task.id`；CLI 不推导，必须在 `control.json` 的 tasks 条目与定位该任务的 `nodes` 条目中显式写入同一值 |
| `policy.on_idle` | `task.on_idle` |
| `control.json` 的 tasks 条目 | `nodes[control_node]` 的 `host` / `root` / `allowed_root` / `python`，并在其 `nodes` 下补齐其余节点的同名四项；existing 的控制节点必带 `container.self`，该条目加 `self: true` |
| `control.json` 的 `tester` 目标 | `evaluation.tester` 的 host、`work_root/task.id`、`python`；键名固定为 `host` / `root` / `python` |
| `bindings.nodes.<n>.machine_id` 与 `bindings.testers.<id>.machine_id` | 取 **CLI 实际执行位置**的 `/etc/machine-id`：existing 节点与容器内 tester 取容器内值（`self` 节点本地读取，其余节点经 docker exec）；owned 取宿主值；两者都是 check-ready 硬门槛，节点缺失返回 `NODE_MACHINE_ID_REQUIRED`。`self` 节点的节点与 tester 的 `machine_id` 必须同时等于容器内的值；读不到合法 32 位值时停止回报，不用宿主值代替 |
| `bindings.nodes.<n>.work_dir` | `nodes.<n>.allowed_root` |
| `deployment.runtime.mode` | 由 top_input 投影：任一节点有 `container` ⇒ `existing_container`，否则 `owned_container`；模式本身由派单事实与授权决定，见 top-input.md §0 |
| `deployment.runtime` | owned：`{image, container}`；existing：只写 `{mode:"existing_container"}` |
| `bindings.nodes.<n>.container` / `container_work_root` | `nodes.<n>.container` / `nodes.<n>.container_work_root` 逐字段投影；existing 模式不写 `mounts` |
| 其余节点绑定 | `nodes` 逐节点投影为 bindings 的 `nodes`，不经 `control.json` |
| 交付目录 | `deliverables.local_dir/task.id` |
