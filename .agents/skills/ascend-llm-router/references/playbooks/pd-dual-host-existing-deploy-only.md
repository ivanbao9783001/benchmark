# 双机 PD + 借用容器 + 只部署

适用：`action=deploy_only`、`topology=pd`、两台机器、`runtime=existing_container`。服务部署成功后保留。**两侧都是容器内闭环**：当前所在节点写 `container.self=true` 本地执行，另一节点不写 `self`，但它的控制面同样在该节点容器内，经 SSH 到宿主后 `docker exec` 进入执行；两侧都不使用宿主路径或宿主 Python。
示例：[pd-dual-host-existing-deploy-only.json](../examples/pd-dual-host-existing-deploy-only.json)

单机 PD 不是“把 `nodes` 减到一台”就等价：唯一节点同样写 `container.self=true` 并本地执行，proxy 与 prefill/decode 同机，`devices` 与 `ports` 必须互不重叠；本文件不提供单机 PD 示例，按 [contract.md](../top-input/contract.md) 组装。

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`action 与拓扑约束`、`容器模式`。
3. [derivation.md](../top-input/derivation.md) 中 PD 相关行（`kv_transfer`、`routing`、`kv_transfer_config`、`hccl`）。
4. [validation.md](../top-input/validation.md) 全部通用项（第 3、4、14 条最关键）。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-deployment/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.on_idle` | `deploy_only` / `keep_service` |
| `nodes.<id>.*`（每个节点） | `host`、`allowed_root`、`python`、`guard_root`、`model_paths`、`network`、`container`、`container_work_root`、`authorization`；上述路径与解释器**均为容器内值** |
| `nodes.<control-node>.container.self` | `true`；当前节点本地执行，另一节点经 SSH + `docker exec` 进入容器 |
| `nodes.<other-node>.container` | 必须给出 `id`；`image` 缺失时由控制端经宿主 `docker inspect` 取证并写回；该节点控制面在容器内 |
| `nodes.<proxy-node>.paths.proxy_program` | proxy 所在节点必须有 |
| `deployment.kv_transfer.connector` | 必填，值必须随镜像 tag 一起给出：以本镜像注册表中存在且可加载的 connector 为准；执行方只做只读确认，确认不了记 `UNVERIFIED`，不得自行选择或改写参数。connector 名与私有键是目标镜像/版本的属性，不是单机/双机拓扑的属性；不得按拓扑或示例推断 |
| `deployment.instances` | 至少 1 `prefill` + 1 `decode` + **恰好 1** `proxy`；`prefill`/`decode` 的 `ports` 为 `{http, kv}`，`proxy` 为 `{http}` |
| `deployment.instances.<prefill\|decode>.launch.engine_id` | **必填**（`preserve_reference=true` 时缺了会被 topology 校验拒绝），值 = `deployment.id-instance.id`，例如 `case-pd-deploy-only-01-prefill` / `case-pd-deploy-only-01-decode`；必须与 `launch.kv_transfer_config.engine_id` 一致 |
| `deployment.endpoint` | 必须指向 `proxy` |
| 不写 | `evaluation`、`deployment.image`、`mixed` 角色 |

两侧 `model_paths` 各自独立，可以不同；`prefill`/`decode` 设备数必须相同。

示例 JSON 里的 connector 是结构占位（`REPLACE_WITH_REGISTRY_CONNECTOR`，历史示例曾用 `MooncakeHybridConnector`），不代表任何现场验证结果；不同镜像注册的 connector（如 `MooncakeConnectorV1`）不同，复制后必须替换为目标镜像注册表中真实可加载的 connector 再 `validate-top-input`。connector 私有键同理，只能来自该镜像的参考启动配置。

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)，重点设备数一致、端口不重叠、两侧 `network.ip` 互通。
2. `prepare-inputs (stage=deploy)` 生成 plan/bindings；核对 `connections.kv_transfer` 与 `routing` 已派生。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`；等待 `deployment.ready`，核验两侧服务健康与 proxy 冒烟。
5. 交付 `service.json`，声明服务保留；不调用 `eval`/`release`。

## 证据

- `self` 节点在容器内核对 `/.dockerenv`、NPU 可见性与网络；另一节点控制端经 SSH 到宿主 `docker inspect` 核对容器与 NPU 直通，再 `docker exec` 进入容器核对路径、解释器与身份；两侧都不写宿主目录、不用宿主 Python。
- HCCL 基端口与 `launch.env.HCCL_IF_BASE_PORT` 一致。
- `deployment.ready`、两侧实例健康、proxy 冒烟响应。
- 跨机时间线按请求 ID 对齐，不按时间戳排序：两侧容器时钟可能不同步（实测可差数分钟），对端日志时间早于本端部署开始属正常；KV 传输用 decode 日志的请求 ID 与 `remote_session_id` 对齐 proxy/prefill 记录后再判定。

## 常见错误

- `endpoint` 指向 `prefill`/`decode` 而不是 proxy。
- 把远端节点的 `allowed_root`、`guard_root`、`python` 写成宿主路径或宿主解释器；existing 全容器内闭环。
- 两侧设备数不同，KV 并行签名不一致被 CLI 拒绝。
- `kv` 端口缺失或与 `http` 重叠。
- proxy 节点漏写 `paths.proxy_program`。
- 双机两个节点都写 `self`，或都没写；existing 必须恰好一个。
- 用单机 PD 的双实例假设设备足够，实际两台各自有限。
