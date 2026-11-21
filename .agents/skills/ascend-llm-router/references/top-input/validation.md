# 提交前校验与规则

先跑场景 `playbook` 里列出的 `required_facts`，再逐条核对下列通用项。任一不满足即停止并向派单方回报，不自行修正。

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
19. `container.id` 匹配 `^[a-f0-9]{64}$`，`container.image` 匹配 `^sha256:[a-f0-9]{64}$`。`existing` 且非 self 时，冻结与部署前用 `docker inspect` 核对 ID、镜像与 `Running=true`，任一不符即停止；`container.self=true` 时控制端就在容器内、没有 docker，只能以 `/.dockerenv` 与容器内只读信息自证，`id`/`image` 仅做格式核验，证据记 `UNVERIFIED`，不得声称已核验；owned 模式没有 `container` 字段，不做此项，镜像由 `deployment.image` 交给 CLI。
20. `container_work_root` 为容器内绝对路径，不含 `:` 与 `..`，且在容器内可写、不与已有服务或他人目录重叠；`allowed_root`（self 模式为容器内路径）只保存任务状态与可回传产物。
21. existing 模式下 `devices` 是容器可见的卡号：非 self 经 `docker inspect` 核验 NPU 直通，self 直接在容器内核对 `/dev/davinciN` 或 `npu-smi info`；不可把宿主卡号当成容器内逻辑编号，也不得因此假定容器可被重建。
22. `nodes.<id>.python` 必须现场验证 `from jsonschema import Draft202012Validator` 成功；只有 `jsonschema` 3.x 的解释器不能用作发布/启动解释器。解释器不满足时先向派单方回报并给出已核验的候选，不自行安装依赖。
23. 同机 tester 使用派生值时，派生结果必须与节点事实一致；`host_python` 缺省 `/usr/bin/python3`，显式给出时必须存在且可执行。部署任务里容器内 tester 的 `container` 必须与所属节点同一 ID/镜像；`eval_only` 没有节点 `container`，只按 `evaluation.tester.container` 单独核验。
24. `container.self` 只能出现在 `existing_container`，且全任务至多一个；该节点必须是 `task.control_node`，其 `host`/`root` 与该 tasks 条目一致。控制端必须处于该容器内（`/.dockerenv`），`machine_id` 取该容器内 `/etc/machine-id`，且节点与 tester 的值必须一致；容器内读不到合法 32 位值时停止回报，不用宿主值代替。该模式下 `allowed_root`、`guard_root`、`container_work_root`、`deliverables.local_dir` 与派生出的节点 `root` 都按容器内路径填写，`tester.work_root` 与 `allowed_root` 一致；非 POSIX 控制端直接拒绝。

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
