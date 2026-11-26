# 参数模板

本目录提供可复制的输入片段，回答两个问题：填什么、必须替换什么。每个文件只放一个可直接嵌入的对象；`{{...}}` 表示必须由现场 bindings 决定的值，不是可以原样提交的配置。

优先级固定为：**用户显式值 > 需求文档明确值 > 模板默认值**。模板不得覆盖用户明确要求；覆盖时在审阅摘要中记录原值、采用值与理由。

## 使用步骤

1. 复制对应文件，按“嵌入位置”整体粘贴，不拼接、不改层级。
2. 替换全部 `{{...}}`，取值来源见“替换清单”；替换后同一占位符在各处取值必须一致。
3. 提交前对请求文件做全文搜索，`{{` 必须无匹配（bash：`grep -n "{{" <file>`；PowerShell：`Select-String -Path <file> -Pattern '\{\{'`）。残留占位符视为规划未完成，不得进入 draft-review。

## 文件索引

| 文件 | 片段用途 | 嵌入位置 |
| --- | --- | --- |
| `eval-acc-gpqa.case.json` | 精度 case（GPQA 全量） | `task.json.cases[]`（stage=eval） |
| `eval-acc-gpqa-mini.case.json` | 精度 mini case（GPQA 前 30 条） | `task.json.cases[]`（stage=eval） |
| `eval-acc-gsm8k-mini.case.json` | 精度 mini case（GSM8K 前 30 条） | `task.json.cases[]`（stage=eval） |
| `eval-perf-synthetic.case.json` | 性能 case | `task.json.cases[]`（stage=eval） |
| `dataset-aisbench-task.json` | 原生数据集绑定（GPQA） | `task.json.tester.datasets.gpqa` |
| `dataset-aisbench-gsm8k.json` | 原生数据集绑定（GSM8K） | `task.json.tester.datasets.gsm8k` |
| `dataset-synthetic.json` | 合成数据集绑定 | `task.json.tester.datasets.synthetic` |
| `deploy-runtime-qwen3-8b.json` | owned 容器运行时 | `deployment.runtime` |
| `deploy-runtime-existing-container.json` | 借用已有容器运行时 | `deployment.runtime` |
| `deploy-instance-qwen3-8b-tp2.json` | 单实例启动档 | `deployment.instances[0]` |

## 模板不覆盖的内容

模板只覆盖上表“嵌入位置”列的 9 个路径。完整输入还需：

| 缺口 | 去哪取 |
| --- | --- |
| `stage`、`destination` | 本次任务与授权的工作目录；`destination` 必须是交付根 `<deliverables.local_dir>/<task.id>/`（`task_root`）下的新文件或新目录 |
| `deployment` 顶层：`schema_version`、`task_id`、`intent`、`id`、`topology`、`model`、`endpoint`、`connections`（必填，可为 `{}`），以及 `instances` 数组 | 字段语义见 references/plan-contract.md；结构由 `export-contracts` 导出；`instances[0]` 由启动档模板填充 |
| `policy`、`execution`（超时、重试、空闲动作） | 按授权与需求确定，不是模板默认值 |
| `unresolved` | 必填数组：只放授权与目标类中无法推断的项；现场事实与模板/派生默认值不得进入，冻结前必须为空。采用的缺省值写进审阅摘要 |
| `bindings` 全部（host、work_dir、machine_id、model_paths、mounts、network、ports） | 现场发现并取证，禁止跨机复制；`machine_id` 用 `deploy-eval-v2 identity` 在目标节点取证；借用容器时改为 `container` 与 `container_work_root`，不写 `mounts` |
| `tester`（host、python、aisbench、datasets、machine_id） | 现场发现；`machine_id` 用 `deploy-eval-v2 identity` 取证，是 check-ready 的硬门槛；数据集绑定模板只提供 `datasets` 里的一个条目 |

组装完成后：独立部署（`deploy_only`）或独立测试（`eval_only`）先用 `prepare-inputs` 校验，再进入 draft-review；`deploy_and_eval` 不经过 `prepare-inputs`，直接进入 draft-review（schema 与摘要校验等价）。schema 缺项会拒绝，但不要依赖报错来发现缺块。

## 精度 case

当前支持两个原生任务：GPQA-Diamond（`gpqa_gen_0_shot_str`）与 GSM8K（`gsm8k_gen_0_shot_cot_str`）。case 模板：GPQA 全量 `eval-acc-gpqa.case.json`、GPQA mini `eval-acc-gpqa-mini.case.json`、GSM8K mini `eval-acc-gsm8k-mini.case.json`。

| 字段 | 默认 | 可改 | 依据 |
| --- | --- | --- | --- |
| `case_id` | `gpqa_diamond_acc` / `gsm8k_acc_mini` | 是 | 任务内唯一即可 |
| `mode` | `acc` | **否** | 改成 perf 就换了指标 |
| `dataset_id` | `gpqa` / `gsm8k` | **否** | 必须与 tester.datasets 中的键一致，且该条目的 `task` 与数据集匹配 |
| `num_prompts` | 省略=全量 | 是 | 只取前 N 条（1 到该数据集实际样本数）；CLI 追加 `--num-prompts`。上限不写死在 schema，改为运行时按数据实际条数校验。mini 模板写 30；子集结果不可与全量对比 |
| `concurrency` | 8 | 是 | 负载形状，按显存与卡数调整（1–1024） |
| `max_out_len` | 2048 | 是 | 按模型实际输出长度设置；GPQA 虽只要求一行答案，GSM8K 需要思维链，都可能先输出推理过程，512 经常截断。评测后必须核对截断率（16–32768） |
| `timeout_seconds` | 3600 | 是 | 整轮余量，全量与子集都适用（1–86400） |

除 `num_prompts` 外，不可注入的语义由 CLI 按数据集固定：GPQA 为零样本选项题（`first_option_postprocess('ABCD')`，百分制 accuracy），GSM8K 为零样本 CoT（`gsm8k_gen_0_shot_cot_str`，`gsm8k_postprocess` + `gsm8k_dataset_postprocess`，数值精确匹配）。两者都使用 `temperature=0`、非流式、`request_rate=0`、`retry=1`。这些不在 case 中，也不要试图新增其他字段（`additionalProperties=false` 会拒绝）。

## 性能 case（合成数据）

文件：`eval-perf-synthetic.case.json`

| 字段 | 默认 | 可改 | 依据 |
| --- | --- | --- | --- |
| `case_id` | `qwen3-8b-1k256-c16-steady` | 是 | 任务内唯一即可 |
| `mode` | `perf` | 可省略 | 省略时按 perf 处理，但建议显式写 |
| `dataset_id` | `synthetic` | **否** | 必须与 tester.datasets 中的键一致 |
| `concurrency` | 16 | 是 | 负载形状 |
| `input_tokens` / `output_tokens` | 1024 / 256 | 是 | 目标长度，不是实测长度；也不作为通过门槛，实测分布见性能证据 |
| `steady_state` | `true` | 是 | 稳态才有多窗口汇总 |
| `num_warmups` | 1 | 是 | 预热轮数（≥0） |
| `reset_cache` | `false` | 是 | 清缓存是独立授权，不得默认开启 |
| `timeout_seconds` | 3600 | 是 | 整轮余量 |
| `formal_requests` | 按公式推导 | 是 | 内嵌 evaluation 必须显式，见下 |

`formal_requests` 分两种情况，不是同一个规则：

- **独立 eval 任务**（`stage=eval`）：省略，由代码推导为 `concurrency×4`，`steady_state=true` 时 `concurrency×10`，并在审阅摘要中展示推导值。
- **内嵌 owned evaluation**（deploy plan 顶层的 `evaluation` 块）：**必须显式写**推导结果。冻结时任何仍需推导的字段都会被拒绝（`normalize owned evaluation before review`），因为内嵌评测必须在审阅前定型。

显式写不是“改节流”：值仍按上面的公式计算，只是把推导结果落成字面量。

## 内嵌 evaluation（deploy plan 自带评测）

当部署与评测属于同一任务时，deploy plan 顶层直接带 `evaluation`：

```json
{
  "evaluation": {
    "cases": [ ... ],
    "test_binding": "t1"
  }
}
```

- 位置：与 `deployment` **平级**的顶层字段，不要放进 `deployment` 内部（schema 会拒绝）。
- `cases[]` 使用上面的精度或性能 case 模板；`test_binding` 是 bindings 中 `testers` 的键。
- 块内 case 的 `formal_requests` 必须显式写，规则见上。


## 数据集绑定

原生任务（精度唯一可用形式）。GPQA：

```json
{
  "type": "aisbench_task",
  "task": "gpqa_gen_0_shot_str",
  "path": "{{dataset.gpqa.root}}"
}
```

GSM8K：

```json
{
  "type": "aisbench_task",
  "task": "gsm8k_gen_0_shot_cot_str",
  "path": "{{dataset.gsm8k.root}}"
}
```

- `path` 必须替换为 POSIX 绝对路径，指向数据集目录。GPQA 目录含 `gpqa_diamond.csv`（按 CSV 记录计，不含表头）；GSM8K 目录必须同时含 `train.jsonl` 与 `test.jsonl`，评测与计数使用 `test.jsonl`，缺 `train.jsonl` 时原生 loader 直接失败。
- 样本数不写死在代码或 schema：预检在测试机现场统计实际条数，`num_prompts` 超过实际条数会被拒绝。全量评测省略 `num_prompts`；mini 写 `num_prompts`，CLI 追加 `--num-prompts` 取前 N 条，不在配置里写 `test_range`。子集结果只用于快速冒烟/回归，不可与全量 accuracy 对比。
- 数据需提前下载到测试机，CLI 不自动拉取；两个数据集目录同级存放（如 `/data/datasets/gpqa`、`/data/datasets/gsm8k`）。

合成数据（性能）：

```json
{
  "type": "aisbench_synthetic"
}
```

## 部署启动档（Qwen3-8B / TP2）

拆成两个文件，避免整段贴错层级：`deploy-runtime-qwen3-8b.json` 整体贴到 `deployment.runtime`；`deploy-instance-qwen3-8b-tp2.json` 整体贴到 `deployment.instances[0]`。

借用已有容器时，`deployment.runtime` 改用 `deploy-runtime-existing-container.json`（只有 `{"mode":"existing_container"}`），`deployment.image` **省略**（不要写 `null`），也不需要 `{{image.digest}}`；容器身份写入 bindings 的 `container`，并补 `container_work_root`。tester 与节点同规则：existing 的 tester 在容器内闭环，写 `container` 与 `container_work_root`，`python/aisbench/model_path/environment/datasets/machine_id` 均为容器内值；owned 的 tester 在宿主，不写 `container`。启动档仍可描述实例与参数，但 `shm_size`、`privileged`、`devices` 等容器创建参数不会生效（容器已存在），不要据此假定可以调整容器规格，也不要假定工作根目录不存在。

### 替换清单（提交前必须全部消除）

| 占位符 | 替换为 | 来源 |
| --- | --- | --- |
| `{{image.digest}}` | 本机镜像的 `sha256:...` | 现场镜像清单，不得跨机复用 |
| `{{gpu.0}}` `{{gpu.1}}` | 卡号数字，如 `6`、`7` | 用户授权 + 占用检查；同时出现在 `placement.devices` 与 `/dev/davinci` 后缀 |
| `{{node.id}}` | 部署节点名，如 `node1` | bindings |
| `{{hccl.base_port}}` | HCCL 基端口，如 `64023` | bindings 端口分配：默认 64023，被占用时顺延；top_input 不写该值 |
| `{{network.ip}}` | 节点 IP | bindings |
| `{{network.interface}}` | 网卡名 | bindings |
| `{{dataset.gpqa.root}}` | GPQA 数据集目录，如 `/data/datasets/gpqa` | 测试机实际路径 |
| `{{dataset.gsm8k.root}}` | GSM8K 数据集目录，如 `/data/datasets/gsm8k` | 测试机实际路径 |

替换后 `placement.devices` 必须是 JSON 数字（写 `6`，不要留成 `"6"`）；`/dev/davinci` 后缀处仍然是字符串。

任务级部署参数只能来自 `top_input.deployment.instances[].args`，按实例合并进该实例的 `launch.args` 后逐项记录差异；不要为单个任务改共享模板，也不要把覆盖扩散到 `env`、`placement`、`ports` 或 `checks`。

### 保留默认值（可改，但不要为省事而改）

| 字段 | 值 | 依据 |
| --- | --- | --- |
| `tensor-parallel-size` | 2 | 必须等于两个卡占位符的数量，也等于 `devices` 中 davinci 条目数 |
| `max-model-len` | 8192 | 精度场景足够，显著降低显存压力 |
| `gpu-memory-utilization` | 0.72 | 见故障记录 |
| `shm_size` | `500g` | 与镜像基线一致 |
| `enable-prefix-caching` | `true` | 性能 case 需要清缓存接口时保留 |
| `ports` / `checks` | http=1、hccl=1；`/health`、`/reset_prefix_cache`、`/metrics` | plan 的端口值是**跨度宽度**（1 表示单端口，schema 允许 1..256），不是基址；真实基址以 bindings 为准（两处键名集合必须一致，跨度不得互相覆盖）；`/metrics` 路由须与实际服务一致 |

故障记录（2026-11-18，同一机器）：

- 0.85 利用率下，精度评测到第 48/198 题时 TP1 worker 报 `Resource_Error_Insufficient_Device_Memory`（err 507899，ACL graph replay 申请 2 MB 失败），随后出现 `No available shared memory broadcast block found in 60 seconds`，engine 假死；取消后进入 RECOVERY_REQUIRED。
- 卡上另有约 2.8 GB 既有占用，0.85 只余约 4.4 GB，不足 graph replay 与运行时分配。
- 改为 0.72 + `max-model-len=8192` 后，启动占用约 21.2/29.49 GiB，该数据集全量（当次 198 题）稳定完成（accuracy 26.26%，当次实测）。

因此共享卡环境优先使用 0.72；若确需提高，先确认卡上无其他占用，并按最小可用增量调整。

## 不变式

- 模板只提供结构、推荐默认值和占位符，不改变 CLI 语义；prompt、判分、缓存策略、限速不在模板中。
- 锁定字段由 schema 强制（`const`/`enum`/`additionalProperties=false`），不依赖文档提醒。
- 所有现场值以 `{{...}}` 表达，模板不得包含真实镜像摘要、真实卡号或真实路径；测试拦截这类回归。
- 模板位于技能目录，不进入 runtime 包，修改模板不会改变 `runtime_sha256`，也不影响已冻结任务。
