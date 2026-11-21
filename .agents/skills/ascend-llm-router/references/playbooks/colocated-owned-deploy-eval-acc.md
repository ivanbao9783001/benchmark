# 单机混部 + CLI 自建容器 + 精度评测

适用：`action=deploy_and_eval`、`topology=colocated`、`runtime=owned_container`、`eval_mode=acc`。派单方授权 CLI 创建容器并在释放时删除；测试端在宿主上，示例用 GPQA-Diamond。
示例：[colocated-owned-deploy-eval-acc.json](../examples/colocated-owned-deploy-eval-acc.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`evaluation`、`容器模式`。
3. [derivation.md](../top-input/derivation.md) 全部（尤其 tester 同机缺省与 `runtime.mode` 投影）。
4. [validation.md](../top-input/validation.md) 全部通用项（第 18、19 条最关键）。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-deployment/SKILL.md` →（`deployment.ready` 后）`ascend-llm-eval/SKILL.md` →（`on_idle=release_owned`）`ascend-llm-release/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.eval_mode` | `deploy_and_eval` / `acc` |
| `task.on_idle` | `release_owned` 或 `keep_service`；`keep_service` 时容器由 CLI 保留，本任务不删除 |
| `nodes.<id>` | `host`、`allowed_root`、`python`、`guard_root`、`model_paths`、`network`（建议给）、`authorization` |
| `deployment.image` | **owned 必填**：一个部署共用一个 `sha256:` 镜像摘要 |
| `deployment.instances` | 1 个 `mixed`，`ports` 只写 `http` |
| `evaluation.tester` | `host`、`work_root`、`python`、`aisbench`、`model_path`、`machine_id`、`datasets.<id>` 必填；`host_python` 建议显式给出；测试在宿主上进行，`machine_id` 取宿主值 |
| `evaluation.dataset` | `{id, split, scope}`，必须与 `datasets` 中的键一致 |
| 不写 | `nodes.<id>.container`、`nodes.<id>.container_work_root`、`evaluation.service`、`evaluation.tester.container` |

数据集只能来自派单方：缺失时停止回报，不得自选数据集或缩小 `scope`。

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)，重点确认没有残留 `container` 字段、`deployment.image` 为合法摘要。
2. 自行组装 plan/bindings（不经 `prepare-inputs`）；plan 的 `runtime` 写 `{image, container}`，bindings 写 `mounts` 且不写 `container`。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`（CLI 创建并启动容器）；`deployment.ready` 后再读 eval 技能。
5. `eval`；`evaluation.completed` 后按 `on_idle` 由 CLI 删除容器或保留。

## 证据

- 部署：`deployment.ready`、容器创建与镜像摘要、健康与冒烟响应。
- 评测：`evaluation.completed`、逐题结果文件、总分与样本量；记录是否 `full` 还是子集。
- 释放：`on_idle=release_owned` 时记录容器已删除；`keep_service` 时声明容器与服务的保留归属。

## 常见错误

- 从 existing 示例照抄 `nodes.<id>.container` 或 `container_work_root`：任一出现即被判定为 existing，owned 语义被静默替换。
- 漏写 `deployment.image`，或写非 `sha256:` 摘要。
- 在 `evaluation.tester` 里写 `container`：owned 下 tester 跑在宿主，容器由 CLI 创建、top_input 阶段拿不到 ID。
- `on_idle=keep_service` 却仍手工删除容器，破坏后续释放与复用。
- 未核对 `deployment.ready` 就 `eval`。
