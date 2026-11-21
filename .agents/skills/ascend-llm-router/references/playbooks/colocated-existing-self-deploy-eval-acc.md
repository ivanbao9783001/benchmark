# 单机混部 + 借用容器（控制端在容器内）+ 精度评测

适用：`action=deploy_and_eval`、`topology=colocated`、`runtime=existing_container`、`container.self=true`、`eval_mode=acc`。CLI 与控制进程直接运行在该容器内。
示例：[colocated-existing-self-deploy-eval-acc.json](../examples/colocated-existing-self-deploy-eval-acc.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`evaluation`、`容器模式`。
3. [derivation.md](../top-input/derivation.md) 全部。
4. [validation.md](../top-input/validation.md)，重点第 24 条与 `container.self` 相关项。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-deployment/SKILL.md` →（`deployment.ready` 后）`ascend-llm-eval/SKILL.md` →（`on_idle=release_owned`）`ascend-llm-release/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.eval_mode` | `deploy_and_eval` / `acc` |
| `task.control_node` | **就是**写 `container.self` 的那个节点 |
| `nodes.<id>.container.self` | `true`（全任务至多一个） |
| `nodes.<id>.container` / `container_work_root` | 仍必填；镜像摘要无法核验，证据记 `UNVERIFIED` |
| `nodes.<id>.network` | `{ip, interface}` |
| `nodes.<id>.allowed_root` / `guard_root` / `deliverables.local_dir` | 按**容器内路径**填写；`tester.work_root` 与 `allowed_root` 一致，`machine_id` 取容器内 `/etc/machine-id` |
| `deployment.instances` | 1 个 `mixed`，`ports` 只写 `http` |
| `evaluation.tester` | `host`、`work_root`、`python`、`aisbench`、`model_path`、`machine_id`、`datasets.<id>` 必填（路径按容器内写，`machine_id` 取容器内 `/etc/machine-id`）；`host_python` 建议显式给出；另加 `container`（与节点同一 ID/镜像）与 `container_work_root` |
| `evaluation.dataset` | `{id, split, scope}`，与 `datasets.<id>` 一致 |
| 不写 | `deployment.image`、`evaluation.service` |

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)，含第 24 条。
2. 自行组装 plan/bindings；runtime 仍为 `{mode:"existing_container"}`。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `publish-task`、`start-task` 与查询在本地执行，不发 ssh。
5. `deploy` → 核验 `deployment.ready` → `eval` → 按 `on_idle` 释放或保留。

## 证据

- `/.dockerenv` 与 `/etc/machine-id` 核验结果；`control.json` 的 `self: true`。
- 镜像摘要标 `UNVERIFIED`。
- 评测的逐题结果、总分与样本量；数据集 split/scope 与派单一致。

## 常见错误

- 照抄 existing 示例却漏写 `container.self=true`，退化为经 SSH + docker exec 的普通 existing 通道。
- 使用宿主路径或宿主 `machine_id`，而不是容器内取值。
- 数据集缺失时自选替代或缩小 `scope`。
- 在非控制节点上写 `container.self=true`，或写出多个 `self` 节点（至多一个，且必须是 `control_node`）。
- 声称镜像摘要已核验。
