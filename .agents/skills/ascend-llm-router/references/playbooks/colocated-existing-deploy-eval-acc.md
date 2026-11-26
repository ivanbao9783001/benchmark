# 单机混部 + 借用容器 + 精度评测

适用：`action=deploy_and_eval`、`topology=colocated`、`runtime=existing_container`、`eval_mode=acc`。测试端与节点在同一容器，本地执行；示例用 GPQA-Diamond。
示例：[colocated-existing-deploy-eval-acc.json](../examples/colocated-existing-deploy-eval-acc.json)

## 读什么

1. 本文件与示例。
2. [contract.md](../top-input/contract.md) 的 `task`、`nodes`、`deployment`、`evaluation`、`容器模式`。
3. [derivation.md](../top-input/derivation.md) 全部（尤其 tester 同机缺省）。
4. [validation.md](../top-input/validation.md) 全部通用项。
5. 阶段技能：`ascend-llm-plan/SKILL.md` → `ascend-llm-deployment/SKILL.md` →（`deployment.ready` 后）`ascend-llm-eval/SKILL.md` →（`on_idle=release_owned`）`ascend-llm-release/SKILL.md`。

## 本场景必填

| 位置 | 值 |
| --- | --- |
| `task.action` / `task.eval_mode` | `deploy_and_eval` / `acc` |
| `task.on_idle` | `release_owned` 或 `keep_service` |
| `nodes.<id>.container` / `container_work_root` | 借用容器的 ID、镜像摘要与容器内可写根 |
| `nodes.<id>.python` | 容器内解释器绝对路径；bindings 与 control.json 同值，node_worker 由它启动 |
| `nodes.<id>.container.self` | `true`（单机唯一节点；表示当前就在该容器内） |
| `nodes.<id>.network` | `{ip, interface}` |
| `deployment.instances` | 1 个 `mixed`，`ports` 只写 `http` |
| `evaluation.tester` | 同 perf 的必填字段，另加实际数据集条目 `datasets.<id>`；`container` 与 `container_work_root` 必填 |
| `evaluation.dataset` | `{id, split, scope}`，必须与 `datasets` 中的键一致 |
| 不写 | `evaluation.service`、`deployment.image` |

acc 的 `dataset_id` 缺省 `gpqa`，也支持 `gsm8k`（`gsm8k_gen_0_shot_cot_str`）；派单未写数据集时按默认继续，不得提问。`datasets.<id>` 的路径属现场事实，必须在 tester 侧取证；GPQA 目录需含 `gpqa_diamond.csv`，GSM8K 目录需同时含 `train.jsonl` 与 `test.jsonl`；路径不存在或为空时停止并回报“缺数据文件”，不得改选数据集或缩小 `scope`。

## 步骤

1. 校验：本表 + [validation.md](../top-input/validation.md)，重点确认数据集 id、split、scope 三方一致。
2. 自行组装 plan/bindings（不经 `prepare-inputs`）。
3. 准备链：`package-runtime` → `draft-review` → `freeze-inputs` → `check-ready` → `publish-task` → `start-task`。
4. `deploy`；`deployment.ready` 后再读 eval 技能。
5. `eval`；`evaluation.completed` 后按 `on_idle` 释放或保留。

## 证据

- 部署：`deployment.ready`、健康与冒烟响应。
- 评测：`evaluation.completed`、逐题结果文件、总分与样本量；记录是否 `full` 还是子集。

## 常见错误

- `evaluation.dataset.id` 与 `datasets` 键不一致。
- 数据集缺失时自行挑一个替代。
- 复用 perf 示例后忘记把 `datasets.synthetic` 换成实际任务。
- 未核对 `deployment.ready` 就 `eval`。
