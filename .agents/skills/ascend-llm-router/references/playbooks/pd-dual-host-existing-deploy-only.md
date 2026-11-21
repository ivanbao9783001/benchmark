# 双机 PD + 借用容器 + 只部署

适用：`action=deploy_only`、`topology=pd`、两台机器、`runtime=existing_container`。服务部署成功后保留。
示例：[pd-dual-host-existing-deploy-only.json](../examples/pd-dual-host-existing-deploy-only.json)

单机 PD 不是“把 `nodes` 减到一台”就等价：proxy 与 prefill/decode 同机，`devices` 与 `ports` 必须互不重叠；本文件不提供单机 PD 示例，按 [contract.md](../top-input/contract.md) 组装。

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
| `nodes.<id>.*`（每个节点） | `host`、`allowed_root`、`python`、`guard_root`、`model_paths`、`network`、`container`、`container_work_root`、`authorization` |
| `nodes.<proxy-node>.paths.proxy_program` | proxy 所在节点必须有 |
| `deployment.kv_transfer.connector` | 两侧镜像都可用 |
| `deployment.instances` | 至少 1 `prefill` + 1 `decode` + **恰好 1** `proxy`；`prefill`/`decode` 的 `ports` 为 `{http, kv}`，`proxy` 为 `{http}` |
| `deployment.endpoint` | 必须指向 `proxy` |
| 不写 | `evaluation`、`deployment.image`、`mixed` 角色 |

两侧 `model_paths` 各自独立，可以不同；`prefill`/`decode` 设备数必须相同。

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)，重点设备数一致、端口不重叠、两侧 `network.ip` 互通。
2. `prepare-inputs (stage=deploy)` 生成 plan/bindings；核对 `connections.kv_transfer` 与 `routing` 已派生。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`；等待 `deployment.ready`，核验两侧服务健康与 proxy 冒烟。
5. 交付 `service.json`，声明服务保留；不调用 `eval`/`release`。

## 证据

- 两侧 `docker inspect` 与 NPU 可见性；`network.ip` 互通探测。
- HCCL 基端口与 `launch.env.HCCL_IF_BASE_PORT` 一致。
- `deployment.ready`、两侧实例健康、proxy 冒烟响应。

## 常见错误

- `endpoint` 指向 `prefill`/`decode` 而不是 proxy。
- 两侧设备数不同，KV 并行签名不一致被 CLI 拒绝。
- `kv` 端口缺失或与 `http` 重叠。
- proxy 节点漏写 `paths.proxy_program`。
- 用单机 PD 的双实例假设设备足够，实际两台各自有限。
