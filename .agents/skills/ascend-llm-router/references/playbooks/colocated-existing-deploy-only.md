# 单机混部 + 借用容器 + 只部署

适用：`action=deploy_only`、`topology=colocated`、`runtime=existing_container`。部署成功后服务保留，不读 eval 技能、不启动负载、不自动释放。
示例：[colocated-existing-deploy-only.json](../examples/colocated-existing-deploy-only.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`action 与拓扑约束`、`容器模式`。
3. [validation.md](../top-input/validation.md) 全部通用项。
4. 阶段技能：先 `ascend-llm-plan/SKILL.md`；准备链通过后再读 `ascend-llm-deployment/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` | `deploy_only` |
| `task.on_idle` | `keep_service`（唯一合法值） |
| `nodes.<id>.container` | 派单方给出的运行中容器 ID 与镜像摘要 |
| `nodes.<id>.container_work_root` | 容器内可写根 |
| `nodes.<id>.network` | `{ip, interface}`，不得留空 |
| `nodes.<id>.model_paths` | 模型 id → 容器内可见路径 |
| `deployment.instances` | 恰好 1 个 `mixed`，`ports` 只写 `http` |
| `deployment.endpoint` | 指向该 `mixed` 实例 |
| 不写 | `evaluation`、`deployment.image`、`kv_transfer` |

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md) 逐条。
2. `prepare-inputs (stage=deploy)` 生成 plan/bindings；核对 runtime 为 `{mode:"existing_container"}`，无 `image`/`mounts`。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`，等待 `deployment.ready`，核对健康与冒烟证据。
5. 交付 `service.json` 与证据，声明服务已保留；不调用 `eval`、`release`、`cancel`。

## 证据

- `docker inspect` 的容器 ID、镜像、`Running=true`、`NetworkMode`；容器内 NPU 可见性。
- `deployment.ready` 事件与冒烟请求的原始响应。

## 常见错误

- 写了 `on_idle=release_owned`，`deploy_only` 会拒绝。
- 顺手补 `evaluation` 段或 `deployment.image`。
- 部署完成后自动 release。
- 容器不可用时自行创建替代容器，而不是停止回报。
