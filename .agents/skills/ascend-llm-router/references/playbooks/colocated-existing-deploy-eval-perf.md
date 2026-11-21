# 单机混部 + 借用容器 + 性能评测

适用：`action=deploy_and_eval`、`topology=colocated`、`runtime=existing_container`、`eval_mode=perf`。测试端在容器内。
示例：[colocated-existing-deploy-eval-perf.json](../examples/colocated-existing-deploy-eval-perf.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`evaluation`、`action 与拓扑约束`、`容器模式`。
3. [derivation.md](../top-input/derivation.md) 全部（尤其 tester 同机缺省）。
4. [validation.md](../top-input/validation.md) 全部通用项。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-deployment/SKILL.md` →（`deployment.ready` 后）`ascend-llm-eval/SKILL.md` →（`on_idle=release_owned`）`ascend-llm-release/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.eval_mode` | `deploy_and_eval` / `perf` |
| `task.on_idle` | `release_owned` 或 `keep_service` |
| `nodes.<id>.container` / `container_work_root` | 借用容器的 ID、镜像摘要与容器内可写根 |
| `nodes.<id>.network` | `{ip, interface}` |
| `deployment.instances` | 1 个 `mixed`，`ports` 只写 `http` |
| `evaluation.tester` | `host`、`work_root`、`python`、`aisbench`、`model_path`、`machine_id`、`datasets.synthetic` 必填；`host_python` 建议显式给出（容器内缺省 `/usr/bin/python3`）；容器内测试再加 `container` 与 `container_work_root` |
| 不写 | `evaluation.dataset`、`evaluation.service`、`deployment.image` |

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)。
2. 自行组装 plan/bindings（`deploy_and_eval` 不走 `prepare-inputs`），按 [derivation.md](../top-input/derivation.md) 派生 tester 与端口。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`；收到 `deployment.ready` 并核对健康/冒烟后再读 eval 技能。
5. `eval`；等待 `evaluation.completed`，按 `on_idle` 决定 release 或保留。

## 证据

- 部署：`deployment.ready`、端口/设备核验、冒烟响应。
- 评测：`evaluation.completed`、吞吐与延迟原始结果、窗口有效性说明。
- 负载只取模板默认值与 `evaluation.params`，不得为提高可见性放大。

## 常见错误

- 把 plan 的端口跨度宽度当成真实端口。
- `machine_id` 空缺，`check-ready` 报 `NODE_MACHINE_ID_REQUIRED`。
- 在 `deploy` 后未核验 `deployment.ready` 就直接 `eval`。
- 把 `evaluation.params` 当作 CLI 字段，未物化进 `cases[]`。
