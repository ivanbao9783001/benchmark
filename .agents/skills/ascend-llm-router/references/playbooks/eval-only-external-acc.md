# 外部已有服务 + 精度评测

适用：`action=eval_only`、无部署、`eval_mode=acc`。控制端兼测试机；不取得外部服务的管理或释放权限。
示例：[eval-only-external-acc.json](../examples/eval-only-external-acc.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`evaluation`、`action 与拓扑约束`。
3. [derivation.md](../top-input/derivation.md) 的 tester 同机缺省与 `machine_id` 行。
4. [validation.md](../top-input/validation.md) 全部通用项（第 16 条最关键）。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-eval/SKILL.md`；**不读** deployment/release 技能。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.eval_mode` | `eval_only` / `acc` |
| `task.on_idle` | 不写（禁用） |
| `nodes.<id>` | 一台，`host` 即控制端兼测试机；不需要 `guard_root`、`model_paths`、`authorization` |
| `evaluation.service` | `{endpoint, model}` 必填，`service_id` 可省略 |
| `evaluation.tester` | 同 acc；容器内测试需 `container` 与 `container_work_root` |
| `evaluation.dataset` | `{id, split, scope}`，与 `datasets.<id>` 一致 |
| 不写 | `deployment`；`evaluation.params` 不是 CLI 字段，参数须物化进 `cases[]` |

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md) 第 16 条（tester 与 control_node 同机）。
2. `prepare-inputs (stage=eval)` 生成 plan/bindings；核对 `cases[]` 与数据集一致。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `eval`；等待 `evaluation.completed`。
5. 报告结果；不调用 `deploy`/`release`。收尾按 eval 技能的 external_eval 流程：先核验 `evaluation.completed` 与外部服务仍健康，再用 `cancel(scope=task)` 终结本评测会话，最后以原 nonce `stop-runtime`；这些动作都不会停止或释放被测服务。

## 证据

- 外部服务 endpoint 的可达性与 `model` 名称核验。
- `evaluation.completed`、逐题结果、总分与样本量。
- 外部服务未由本任务管理的声明（`ownership` 不属于本任务）。

## 常见错误

- 试图 `release` 外部服务。
- 把 eval 技能的 `cancel(scope=task)`/`stop-runtime` 当成“触碰外部服务”而跳过收尾；它们只终结本任务会话，不停被测服务。
- `evaluation.service.id` 写错键名（应为 `service_id`，写成 `id` 会被静默忽略）。
- tester 与 control 节点不同机。
- 为观察更多数据而提高并发或增加请求数。
