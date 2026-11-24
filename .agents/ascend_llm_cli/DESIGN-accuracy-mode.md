# eval 精度评测（acc mode）设计说明

- 适用分支：`feat/ascend-llm-skills`
- 目标组件：`.agents/ascend_llm_cli`（协议 1.0）+ `.agents/skills/ascend-llm-eval`
- 文档状态：已实现
- 读者：第一次接手该功能的开发 Agent

---

## 0. 先读这一页（给新 Agent 的行动摘要）

1. 现状：`ascend-llm-eval` 只有性能评测，`case.mode` 只允许 `perf`；精度是明确预留但未实现的。
2. 本期的唯一交付：在同一个 eval 能力里增加 `mode=acc`，支持原生任务目录 **GPQA-Diamond（`gpqa_gen_0_shot_str`）+ GSM8K（`gsm8k_gen_0_shot_cot_str`）**，缺省全量、可选 `num_prompts` 子集，只输出 accuracy。
3. 不允许：新增独立 skill、改动 AISBench 安装目录、增加门禁/基线。数据集扩展只能通过任务目录新增原生条目（数据文件与原生语义随任务固定），子集只能通过 case 的 `num_prompts` 开启，不得改题。
4. 代码入口：`src/deploy_eval_v2/aisbench_cli.py`、`plan_model.py`、`evaluate.py`、`benchmark_preflight.py`、`result_contract.py`、`external_executor.py`、`owned_executor.py`。
5. 实现顺序、逐文件改法、测试与验收见第 8、9、10、12 节，照做即可。
6. 所有判定必须可追溯到 AISBench 原生产物；不得用日志文本推断指标，不得把未知值写成 0。

---

## 1. 背景

### 1.1 旧能力与解耦后的结构

旧版是端到端 skill：`job_spec.json` + `bindings.json` → 生成脚本 → FULL 模式执行。新分支把它拆成 router / plan / deployment / eval / release 五个阶段，真正的协议与执行由 `.agents/ascend_llm_cli`（`deploy-eval-v2`）实现。

`ascend-llm-eval` 只负责「对一个已存在的服务做评测」：

- 输入：冻结的 `plan.json` + `bindings.json`，其中 plan 包含 `service`、`cases`、`test_binding`，bindings 包含 `testers`
- 输出：`results/<operation_id>/stage-result.json` 与 `evaluation.completed` 事件
- 执行端：`external_eval` 的 tester 必须与协调器同机（`executor_factory` 校验 machine_id）；托管部署（`owned`）评测可以把 tester 放到远端 tester 机器

### 1.2 当前 eval 的真实能力边界

| 事实 | 代码位置 |
|---|---|
| 只支持性能模式，非 perf 直接抛错 | `aisbench_cli.require_supported_mode()` |
| case 契约只有性能字段，`mode` 不在 schema 里 | `schemas/external-eval-plan.schema.json` |
| 数据集只支持 `aisbench_synthetic` / `jsonl` / `csv` | `schemas/eval-bindings.schema.json` |
| 生成 AISBench 配置时只写 `VLLMCustomAPI` + `GenInferencer`，无 `eval_cfg` | `aisbench_cli.write_config()` |
| 命令固定 `--mode perf --num-prompts <formal_requests> --num-warmups <n>` | `aisbench_cli.commands()` |
| 原生结果校验只认 `outputs/*/performances/*/*_details.jsonl` | `native_results.validate_native_result()` |
| 结果契约强制 `performance_validity` | `schemas/result.schema.json`、`result_contract.evaluation_result()` |
| 事件契约强制 `performance_validity` | `schemas/event.schema.json` |
| 文档明确写「精度测试尚未实现」 | `ascend_llm_cli/README.md`、`DESIGN.md` |

因此精度不是「加一个数据集」就能跑通，必须同时改 case 契约、binding 契约、配置生成、命令构造、预检、结果契约和文档。

### 1.3 为什么选 GPQA-Diamond + `gpqa_gen_0_shot_str`

- AISBench 原生配置里 GPQA 只启用 `diamond`（`main` / `extended` 被注释），对应 `gpqa_diamond.csv`
- `gpqa_gen_0_shot_str` 是生成式、单指标 `accuracy`，答案抽取用 `first_option_postprocess(options='ABCD')`，不涉及 PPL、judge 或多轮
- 评测器返回 `{'accuracy': 100 * correct / count}`，指标天然是百分制
- 仓库已有同路径 smoke（`run_server_accuracy/llm_datasets_main/accuracy_gpqa_diamond`），产物形态可对照

---

## 2. 目标与非目标

### 2.1 目标

1. 在现有 `ascend-llm-eval` 的 `eval` 命令内支持 `mode=acc`，跑通 GPQA-Diamond 与 GSM8K 全量；可选 `num_prompts` 子集用于快速验证。
2. 结果只报告 `accuracy`，并提供 `accuracy_validity` 与证据引用。
3. 保持 perf 路径行为、产物、判定完全不变。
4. 所有精度判定可由 AISBench 原生产物复核。

### 2.2 非目标（本期明确不做）

- 不新增 skill（不做 `acc-eval`）；不新增命令；不新增协议版本。
- 不支持 GPQA 的 `main` / `extended`，不支持 `test_range` / 子集 / 采样。
- 不支持 `gpqa_gen_0_shot_cot_chat_prompt`、`gpqa_ppl_0_shot_str`。
- 不支持 lm-eval-harness、LLM-as-judge、多轮、主观题。
- 不做 baseline 对比、阈值判定、通过/失败门禁、趋势报表。
- 不自动把 AISBench 产物回传协调器；保留在 tester 的节点运行根。
- 不修改 AISBench 安装目录里的 `configs/`（smoke 的 `cp -r configs` 做法不复用）。
- 不为精度开启缓存重置、指标采集、合成数据 postflight。

---

## 3. 现状事实清单（实现时必须遵守）

### 3.1 执行链

`execute_external_case()` 当前顺序：

```
health 重检 → prepare_case（写配置 + native-expectations）→ preflight 子进程
→ 可选 reset_cache → metrics before → run AISBench → metrics after
→ validate_native_result → performance_evidence.summarize → result.json
```

精度分支在同一函数内分叉，复用 health、preflight、run_logged、deadline、cancelled、result 落盘等公共部分。

### 3.2 AISBench 精度产物

`--mode all`（默认值）时，产物在 `<work-dir>/<timestamp>/` 下：

```
<work-dir>/<timestamp>/logs/infer/<model_abbr>/<dataset_abbr>.out
<work-dir>/<timestamp>/logs/eval/<model_abbr>/<dataset_abbr>.out
<work-dir>/<timestamp>/predictions/<model_abbr>/<dataset_abbr>.jsonl
<work-dir>/<timestamp>/results/<model_abbr>/<dataset_abbr>.json
<work-dir>/<timestamp>/summary/summary_<timestamp>.csv|txt|md
```

- `predictions/*.jsonl` 每行形如 `{"success": true, "uuid": "...", "origin_prompt": "...", "prediction": "...", "gold": "B"}`（accuracy 模式写原始输入输出；perf 模式才写 `*_details.jsonl`）
- `results/*.json` 就是 AISBench 评测器返回值，未加 `--dump-eval-details` 时只有 `{"accuracy": <float>}`
- 失败请求会单独落到 `<dataset_abbr>_failed.jsonl`，不混在 predictions 里

### 3.3 GPQA 数据集行为

- `GPQADataset.load(path, name)`：`path` 为目录、`name` 为 csv 文件名；`get_data_path` 对 `/` 开头的绝对路径原样返回
- 读取时跳过 `row[7] == 'Question'` 的表头行；题干取 `row[7]`，四个选项取 `row[8..11]`，第一个是正确项；按 `cnt % 4` 做确定性选项轮转
- 因此文件顺序必须稳定，只能用官方 `gpqa_diamond.csv`
- 该 CSV 实测：**198 条数据行**、78 列、含带引号的换行（物理行数 9429）。**统计行数必须用 `csv.reader`，不能按物理行数**

### 3.4 与 smoke 的差异（不要照抄）

- smoke 的 dataset 配置写了 `test_range='[0:10]'`，精度线必须没有
- smoke 的 `max_out_len=20`，只适合短回答 smoke；精度线默认 512
- smoke 用 `cp -r` 覆盖 AISBench 源码目录的 `configs/`，精度线改为生成自包含配置
- smoke 同时跑 `0_shot_cot_chat` 与 `0_shot_str`，精度线只跑 `str`

---

## 4. 核心决策

| 编号 | 决策 | 理由 | 后果 |
|---|---|---|---|
| D1 | 同一 `ascend-llm-eval` + `eval` 命令，case 增加 `mode: "perf" \| "acc"` | 范围极窄（单数据集/全量/单指标/无门禁），独立 skill 属过度设计 | 不新增入口；SKILL.md 增加 acc 说明 |
| D2 | 生成自包含 AISBench 配置，不改安装目录 | 与 perf 现有做法一致；避免污染环境、避免版本升级被覆盖 | 配置由我们维护，需与原生 `gpqa_gen_0_shot_str` 语义一致 |
| D3 | binding 新增 `type: "aisbench_task"`，任务取自受支持的原生目录（GPQA `gpqa_gen_0_shot_str`、GSM8K `gsm8k_gen_0_shot_cot_str`） | 表达「AISBench 原生任务」而非任意文件；目录白名单防止扩散 | 新增 schema 分支；文件名、reader/evaluator/postprocessor 由任务目录固定，用户不可指定 |
| D4 | 全量由三层保证：配置无 `test_range`、命令无 `--num-prompts`、校验行数等于有效样本数；子集通过 case `num_prompts` 显式开启，CLI 追加 `--num-prompts`，校验行数等于 `num_prompts` | 全量保持默认；子集是显式覆盖，必须可追溯并标注 subset | 样本数不在代码或 schema 写死：预检在测试机按实际数据统计，`accuracy_sample_count` 只做类型/下界，上界由运行时计数校验 |
| D5 | 模型客户端用 `VLLMCustomAPIChat` | 与仓库 smoke 的 `vllm_api_general_chat` 路径一致，走 `/v1/chat/completions` | 与 perf 的 `VLLMCustomAPI` 不同；配置生成需单独函数 |
| D6 | case 三个数值字段必填，不给隐式推导；文档推荐 512 / 8 / 3600 | 避免隐藏默认值改变评测语义；perf 的 `concurrency*4` 推导不适用于精度 | schema 必填；SKILL.md 与 prepare-inputs 提示推荐值 |
| D7 | 证据只核对原生产物，不重算 accuracy | 用户明确选择；198 样本收益有限，多一个子进程增加复杂度 | 接受「完全信任 `results/*.json` 的 `accuracy`」这一残余风险 |
| D8 | `accuracy.value` 保留 AISBench 原生百分制，附 `unit: "percent"` | 避免 0–1 / 0–100 歧义；与 AISBench 摘要一致 | 下游不得自行换算后混用 |
| D9 | 结果契约增量演进：新增 `evaluation_type` / `accuracy_validity` / `accuracy`，perf 分支字段不变 | 不影响已有 perf 结果语义 | `evaluation_result()` 必须按 type 分支校验 |
| D10 | 精度不采集 metrics、不重置缓存、不做 synthetic postflight | 与精度无关，采集反而引入额外失败面 | 执行链需按 mode 跳过 |
| D11 | 允许一个 plan 里混合 perf/acc case，但每次 eval 只执行一个 case | 与现有「case 原子提交」一致，无需额外约束 | 结果与事件按 case 携带各自 type |
| D12 | 原始产物留在 tester，不自动回传 | 与现有 log 归一集策略一致；避免大文件传输 | 结果只给引用路径 + host |

---

## 5. 契约设计

### 5.1 输入 plan（精度 case）

```json
{
  "case_id": "gpqa_diamond_acc",
  "mode": "acc",
  "dataset_id": "gpqa",
  "concurrency": 8,
  "max_out_len": 512,
  "timeout_seconds": 3600
}
```

- 必填：`case_id`、`mode`、`dataset_id`、`concurrency`、`max_out_len`、`timeout_seconds`
- 禁止出现：`input_tokens`、`output_tokens`、`steady_state`、`num_warmups`、`formal_requests`、`request_rate`、`shared_prefix_ratio`、`reset_cache`
- `mode` 取 `const "acc"`；perf 分支保留原有必填集，`mode` 可选且 `const "perf"`
- 建议范围：`concurrency` 1–1024、`max_out_len` 16–32768、`timeout_seconds` 1–86400

### 5.2 输入 bindings（精度数据集）

```json
{
  "datasets": {
    "gpqa": {
      "type": "aisbench_task",
      "task": "gpqa_gen_0_shot_str",
      "path": "/data/datasets/gpqa"
    }
  }
}
```

- `task`：枚举，当前为 `gpqa_gen_0_shot_str`、`gsm8k_gen_0_shot_cot_str`
- `path`：绝对目录；GPQA 必须含 `gpqa_diamond.csv`，GSM8K 必须含 `train.jsonl` 与 `test.jsonl`
- 文件名、abbr、reader/evaluator/postprocessor 由任务目录决定，用户不可覆盖；样本数由预检按实际数据统计

### 5.3 生成的 AISBench 配置

文件位置：`<operation>/benchmark/aisbench-config.py`。内容要点：

```python
from mmengine.config import read_base
from ais_bench.benchmark.models import VLLMCustomAPIChat
from ais_bench.benchmark.datasets import GPQADataset, GPQAEvaluator
from ais_bench.benchmark.openicl.icl_prompt_template import PromptTemplate
from ais_bench.benchmark.openicl.icl_retriever import ZeroRetriever
from ais_bench.benchmark.openicl.icl_inferencer import GenInferencer
from ais_bench.benchmark.partitioners import NaivePartitioner
from ais_bench.benchmark.runners.local import LocalRunner
from ais_bench.benchmark.tasks import OpenICLApiInferTask
from ais_bench.benchmark.utils.postprocess.text_postprocessors import first_option_postprocess

datasets = [dict(
    type=GPQADataset,
    abbr='gpqa_diamond_0_shot_str',
    path='/data/datasets/gpqa',
    name='gpqa_diamond.csv',
    reader_cfg=dict(input_columns=['question', 'A', 'B', 'C', 'D'], output_column='answer'),
    infer_cfg=dict(
        prompt_template=dict(
            type=PromptTemplate,
            template='What is the correct answer to this question: {question}\nChoices:\n'
                     '(A){A}\n(B){B}\n(C){C}\n(D){D}\n'
                     'Format your response as follows: "The correct answer is (insert answer here)"'),
        retriever=dict(type=ZeroRetriever),
        inferencer=dict(type=GenInferencer)),
    eval_cfg=dict(
        evaluator=dict(type=GPQAEvaluator),
        pred_postprocessor=dict(type=first_option_postprocess, options='ABCD')),
)]

models = [dict(
    type=VLLMCustomAPIChat, attr='service', abbr='service',
    path='<tester.model_path>', model='<plan.service.model>',
    stream=False, request_rate=0, retry=1,
    host_ip='<endpoint host>', host_port=<endpoint port>,
    url='<service.endpoint.rstrip("/") + "/">',
    max_out_len=<case.max_out_len>, batch_size=<case.concurrency>,
    trust_remote_code=False,
    generation_kwargs=dict(temperature=0, ignore_eos=False))]

infer = dict(partitioner=dict(type=NaivePartitioner),
             runner=dict(type=LocalRunner, max_num_workers=1, task=dict(type=OpenICLApiInferTask)))
```

硬性要求：

1. **不写 `reader_cfg.test_range`**
2. `url` 必须是 base URL（以 `/` 结尾），`VLLMCustomAPIChat._get_url()` 会 `urljoin(base_url, "v1/chat/completions")`；沿用 perf 的 `endpoint.rstrip('/') + '/'` 规则
3. `abbr` 固定为 `service`（模型）与 `gpqa_diamond_0_shot_str`（数据集），证据定位依赖这两个常量
4. 不设置 `pred_postprocessor` 到 model 上（与原生 GPQA 配置一致，后处理在 `eval_cfg`）
5. 不写 `eval` 顶层段，`--mode all` 会按 dataset 的 `eval_cfg` 构建评测任务

### 5.4 命令

```bash
<aisbench> <config.py> --mode all --work-dir <operation>/benchmark/outputs
```

不传：`--num-prompts`、`--num-warmups`、`--datasets`、`--dump-eval-details`、`--max-num-workers`。

### 5.5 产物布局

```
<work_root>/results/<eval operation_id>/benchmark/
├── aisbench-config.py
├── dataset.json
├── native-expectations.json          # [["service","gpqa_diamond_0_shot_str"]]
├── health.json
├── preflight-request.json / preflight.json
├── tester-environment.json
├── aisbench.log
├── execution.json
├── validated.json                    # acc 结构校验结果
├── accuracy-evidence.json            # acc 指标证据
├── result.json
└── outputs/<timestamp>/...           # AISBench 原生产物，保留
```

### 5.6 结果契约（acc）

```json
{
  "schema_version": "1.0",
  "evaluation_type": "acc",
  "service_id": "svc-x",
  "case_id": "gpqa_diamond_acc",
  "execution_status": "SUCCEEDED",
  "evidence_status": "COMPLETE",
  "accuracy_validity": "VERIFIED",
  "accuracy": {
    "schema_version": "1.0",
    "status": "VERIFIED",
    "task": "gpqa_gen_0_shot_str",
    "dataset": "gpqa_diamond",
    "metric": "accuracy",
    "unit": "percent",
    "value": 52.02,
    "num_samples": 198,
    "scope": "GPQA-Diamond full"
  },
  "accuracy_evidence": {
    "host": "<tester host>",
    "path": "<operation>/benchmark/accuracy-evidence.json"
  },
  "reason": "Native accuracy evidence verified",
  "time": "2026-11-16T10:00:00Z",
  "actual_load": {
    "case_id": "gpqa_diamond_acc",
    "mode": "acc",
    "dataset_id": "gpqa",
    "concurrency": 8,
    "max_out_len": 512,
    "timeout_seconds": 3600
  }
}
```

判定语义：

- `accuracy_validity`：`VERIFIED` / `UNVERIFIED` / `INVALID`，与 perf 的取值域一致（`INVALID` 当前不产生）
- `evidence_status`：`COMPLETE` 仅当 `accuracy_validity=VERIFIED`
- `value` 只要 `results/*.json` 合法就输出；`UNVERIFIED` 表示次级产物缺失，不代表数值无效
- 硬失败（结构不符）不产出 `accuracy` 块，走 `operation.failed`

perf 结果增加 `evaluation_type: "perf"`，其余字段不变。

### 5.7 事件契约

`evaluation.completed` 的 `data` 原本强制 `performance_validity`，改为二选一：

```json
"anyOf": [
  {"required": ["performance_validity"]},
  {"required": ["accuracy_validity"]}
]
```

`data.properties` 增加 `accuracy_validity`。`event_journal.visible()` 的字段白名单同步加入 `accuracy_validity`。

---

## 6. 执行流程

```
eval 命令
  └─ execute_external_case(plan, bindings, case_id, folder, cancelled, verified_local_host)
       1. normalize + bindings 校验 + tester 身份校验 + require_linux
       2. health 重检（deadline 内）
       3. prepare_case：按 mode 生成配置与 native-expectations
       4. benchmark_preflight 子进程（tester python）
       5. mode == 'acc' ?
          ├─ 否：reset_cache → metrics before → run → metrics after
          │      → validate_native_result → performance_evidence.summarize
          └─ 是：run（无 metrics / 无 reset）
                 → validate_accuracy_result
                 → accuracy_evidence.summarize
       6. 组装 result.json 并返回
```

`require_linux()` 不变：生产 evaluator 必须在 Linux。

计时约束不变：所有子步骤共用 `case['timeout_seconds']` 的 deadline，不得叠加。

---

## 7. 校验规则

### 7.1 预检（tester python 内，`benchmark_preflight.inspect_environment`）

精度 case 增加：

1. `tester.datasets[dataset_id]` 必须是 `aisbench_task`，`task` 在受支持目录内
2. 目录存在；GPQA 的 `gpqa_diamond.csv` 可读，GSM8K 的 `train.jsonl` 与 `test.jsonl` 均可读
3. GPQA 用 `csv.reader` 统计 `row[7] != 'Question'` 的数据行且每行至少 12 列；GSM8K 统计 `test.jsonl` 有效行（`question` 为字符串且含 `answer`）。实际条数即 `dataset_samples`
4. 能 import 任务目录声明的原生类与后处理器（防止 AISBench 版本漂移）
5. 不检查 tokenizer 长度、不检查 `formal_requests`、不检查 `input_tokens`

### 7.2 结构校验（`validate_accuracy_result`）

在 coordinator 侧执行，读同一 operation 目录。

| 检查 | 不满足时 |
|---|---|
| `outputs/` 下恰好一个时间戳目录 | 硬失败 `ACC_EXP_DIR_AMBIGUOUS` |
| `predictions/service/<dataset_abbr>.jsonl` 存在 | 硬失败 `ACC_PREDICTIONS_MISSING` |
| 每行合法 JSON、≤1 MiB、`success is True`、有 `gold` 与 `prediction` | 硬失败 `ACC_PREDICTIONS_INVALID` |
| 行数 == 有效样本数（`num_prompts` 或预检统计的实际条数） | 硬失败 `ACC_PREDICTIONS_COUNT_MISMATCH` |
| `results/service/<dataset_abbr>.json` 存在且为对象 | 硬失败 `ACC_RESULTS_MISSING` |
| `accuracy` 是有限数值且 `0 <= value <= 100` | 硬失败 `ACC_VALUE_INVALID` |
| `logs/infer/...`、`logs/eval/...` 存在 | 软条件，缺失记 `ACC_*_LOG_MISSING` |
| `summary/summary_<timestamp>.*` 存在 | 软条件，缺失记 `ACC_SUMMARY_MISSING` |

硬失败实现为 `ProtocolError`，与 perf 结构不符时的处理一致（`operation.failed`）。
软条件只影响 `accuracy_validity=UNVERIFIED`，`value` 照常输出。

所有路径必须 `resolve()` 后仍位于 operation 目录内，且不得是符号链接。

### 7.3 指标汇总（`accuracy_evidence.summarize`）

写 `accuracy-evidence.json`：

```json
{
  "schema_version": "1.0",
  "status": "VERIFIED",
  "task": "gpqa_gen_0_shot_str",
  "dataset": "gpqa_diamond",
  "metric": "accuracy",
  "unit": "percent",
  "value": 52.02,
  "num_samples": 198,
  "missing_artifacts": [],
  "scope": "GPQA-Diamond full; native evaluator metric; no baseline or gate"
}
```

`status` 为 `VERIFIED` 当且仅当结构校验通过且 `missing_artifacts` 为空。

### 7.4 结果契约校验（`result_contract.evaluation_result`）

```python
if value['evaluation_type'] == 'perf':
    if value['performance_validity'] == 'VERIFIED' and value['evidence_status'] != 'COMPLETE':
        raise ProtocolError('INVALID_INPUT: verified performance requires complete evidence')
else:
    if value['accuracy_validity'] == 'VERIFIED' and value['evidence_status'] != 'COMPLETE':
        raise ProtocolError('INVALID_INPUT: verified accuracy requires complete evidence')
```

失败结果同样必须带 `evaluation_type` 与对应 validity 字段。

---

## 8. 逐文件改动清单

> 路径均相对 `.agents/ascend_llm_cli/src/deploy_eval_v2/`。

### 8.1 `schemas/external-eval-plan.schema.json`

- `$defs.case` 改为 `oneOf`，拆出 `$defs/perf_case` 与 `$defs/acc_case`
- `perf_case`：保留现有 required 与全部 properties，增加可选 `mode: {"const": "perf"}`
- `acc_case`：`additionalProperties: false`，required 见 5.1，`mode: {"const": "acc"}`，`concurrency` / `max_out_len` / `timeout_seconds` 加范围
- `cases.items` 仍指向 `$defs/case`
- `deploy-plan.schema.json` 与 `deploy-bindings.schema.json` 同步相同分支，保证 owned 评测路径可用

### 8.2 `schemas/eval-bindings.schema.json`

- `$defs.dataset.oneOf` 增加第三分支：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "task", "path"],
  "properties": {
    "type": {"const": "aisbench_task"},
    "task": {"enum": ["gpqa_gen_0_shot_str", "gsm8k_gen_0_shot_cot_str"]},
    "path": {"$ref": "#/$defs/path"}
  }
}
```

### 8.3 `schemas/result.schema.json`

- 顶层 `required` 去掉 `performance_validity`，加入 `evaluation_type`
- 增加 `accuracy_validity`、`accuracy`、`accuracy_evidence` properties
- 增加条件：

```json
"allOf": [
  {
    "if": {"properties": {"evaluation_type": {"const": "perf"}}, "required": ["evaluation_type"]},
    "then": {"required": ["performance_validity"]},
    "else": {"required": ["accuracy_validity"]}
  },
  {
    "if": {"properties": {"evaluation_type": {"const": "acc"},
                          "accuracy_validity": {"const": "VERIFIED"}},
           "required": ["evaluation_type", "accuracy_validity"]},
    "then": {"required": ["accuracy"]}
  }
]
```

第二条条件保证：只有 VERIFIED 才必须有 `accuracy` 块；acc 硬失败/失败结果只带 `accuracy_validity`，与 5.6「硬失败不产出 accuracy 块」一致。

- 不要给 `accuracy` 之外的对象加 `additionalProperties: false`，保持现有宽松度

### 8.4 `schemas/event.schema.json`

- `evaluation.completed` 的 `data.required` 去掉 `performance_validity`
- 加 `anyOf`（见 5.7），`properties` 增加 `accuracy_validity`

### 8.5 `aisbench_cli.py`

新增模块级常量：

```python
SUPPORTED_TASKS = {
    'gpqa_gen_0_shot_str': dict(
        layout='single_csv',
        file='gpqa_diamond.csv',
        dataset_class='GPQADataset', evaluator='GPQAEvaluator',
        imports=((DATASETS_MODULE, ('GPQADataset', 'GPQAEvaluator')),
                 (POSTPROCESS_MODULE, ('first_option_postprocess',))),
        dataset_abbr='gpqa_diamond_0_shot_str',
        dataset_name='gpqa_diamond', label='GPQA-Diamond',
        reader_columns=('question', 'A', 'B', 'C', 'D'), output_column='answer',
        prompt=GPQA_PROMPT,
        pred_postprocessor="dict(type=first_option_postprocess, options='ABCD')",
    ),
    'gsm8k_gen_0_shot_cot_str': dict(
        layout='split_jsonl',
        split_files=dict(train='train.jsonl', test='test.jsonl'), evaluated_split='test',
        dataset_class='GSM8KDataset', evaluator='Gsm8kEvaluator',
        imports=((DATASETS_MODULE, ('GSM8KDataset', 'gsm8k_postprocess',
                                    'gsm8k_dataset_postprocess', 'Gsm8kEvaluator')),),
        dataset_abbr='gsm8k', dataset_name='gsm8k', label='GSM8K',
        reader_columns=('question',), output_column='answer',
        prompt=GSM8K_PROMPT, pred_role='BOT',
        pred_postprocessor='dict(type=gsm8k_postprocess)',
        dataset_postprocessor='dict(type=gsm8k_dataset_postprocess)',
    ),
}
MODEL_ABBR = 'service'
```

- `require_supported_mode(case)`：允许 `perf` 与 `acc`，其余保持原报错文案
- 新增 `task_mode(case)`：返回 `case.get('mode', 'perf')`
- 新增 `accuracy_imports(task)`：返回任务目录里的原生 import 列表，配置生成与预检共用
- 新增 `accuracy_dataset_for(dataset)`：校验 `type` / `task` / `path`，返回任务目录派生的布局、文件、abbr 等（不返回样本数）
- 新增 `check_accuracy_dataset(dataset, num_prompts)`：按 layout 统计实际条数，按实际条数校验 `num_prompts` 上界，返回 `samples` / `requested_samples`
- 新增 `write_accuracy_config(spec, case, dataset, service, folder)`：按 5.3 生成配置，返回 `config` 路径
- `write_config` 保持 perf 语义不变；由 `prepare_case` 决定调用哪个写配置函数
- `commands(executable, config, case, folder)`：按 mode 返回

```python
if task_mode(case) == 'acc':
    return [('benchmark', [executable, str(config), '--mode', 'all',
                           '--work-dir', str(folder/'outputs')])]
```

### 8.6 `plan_model.py`

- `normalize_external_plan`：
  - 只对 `perf` case 做 `reset_cache` 权限/目标校验
  - 只对 `perf` case 推导 `formal_requests`
  - 对 `acc` case 不读取任何性能字段
- `validate_eval_bindings`：
  - 路径校验循环跳过 `aisbench_task` 的「后缀 == type」规则，改为校验目录绝对路径
  - 新增交叉校验：`acc` case 的 dataset 必须是 `aisbench_task`；`perf` case 不得绑定 `aisbench_task`
  - `shared_prefix_ratio` 规则只对 perf 生效

### 8.7 `benchmark_preflight.py`

- `inspect_environment` 按 mode 分支：
  - `perf`：维持现有 `dataset_for` + `check_dataset` + synthetic 检查
  - `acc`：`accuracy_dataset_for` + `check_accuracy_dataset` + 导入检查（见 7.1）
- 不要修改 `main()` 的请求/结果形状

### 8.8 新增 `accuracy_evidence.py`

建议结构：

```python
def validate_accuracy_result(folder, case, dataset, *, deadline=None, cancelled=None): ...
def summarize(folder, case, dataset, validated, *, deadline=None, cancelled=None): ...
```

- `validate_accuracy_result` 写 `validated.json`，返回 dict（含 `accuracy` 与 `predictions_sha256`）
- `summarize` 写 `accuracy-evidence.json`，返回 5.6 中的 `accuracy` 块内容
- 复用 `json_io.load_json`、`durable.atomic_json`、`process_guard.remaining`，错误码用 7.2 的短码

### 8.9 `evaluate.py`

- `prepare_case`：
  - 取 mode；`acc` 用 `write_accuracy_config`，`native-expectations.json` 写 `[[MODEL_ABBR, dataset_abbr]]`
  - `perf` 保持原逻辑
- `execute_external_case`：
  - run 之前：`if mode == 'perf':` 才执行 `reset_cache` 与 metrics 采集
  - run 之后：

```python
if mode == 'acc':
    validated = accuracy_evidence.validate_accuracy_result(folder, case, dataset, deadline=..., cancelled=...)
    accuracy = accuracy_evidence.summarize(folder, case, dataset, validated, ...)
    verified = accuracy['status'] == 'VERIFIED'
    result = dict(schema_version='1.0', evaluation_type='acc',
                  service_id=..., case_id=..., execution_status='SUCCEEDED',
                  evidence_status='COMPLETE' if verified else 'PARTIAL',
                  accuracy_validity='VERIFIED' if verified else 'UNVERIFIED',
                  accuracy=accuracy,
                  accuracy_evidence=dict(host=verified_local_host, path=str(folder/'accuracy-evidence.json')),
                  reason=..., time=now(), actual_load=case)
else:
    ... 现有逻辑，补 evaluation_type='perf'
```

  - 精度分支不调用 `validate_native_result`、`performance_evidence.summarize`、`metric_evidence`
  - 精度 case 的 `tester['datasets'][dataset_id]` 可能没有 `generation_manifest`，避免沿用 perf 的前缀校验代码

### 8.10 `result_contract.py`

- 按 7.4 分支校验
- 兼容读取：写入前若 `evaluation_type` 缺失，视为 `perf`（只影响历史产物重写场景）

### 8.11 `external_executor.py` / `owned_executor.py`

- 成功路径不要硬取 `performance_validity`：

```python
for key in ('performance_validity', 'accuracy_validity'):
    if key in result:
        data[key] = result[key]
```

- 失败路径根据该 case 的 mode 决定写哪个 validity 字段（可用 `next(c for c in plan['cases'] if c['case_id'] == ...)`）
- 失败结果必须包含 `evaluation_type`

### 8.12 `owned_eval.py`

- `any(case['reset_cache'] ...)` → `any(case.get('reset_cache', False) ...)`

### 8.13 `event_journal.py`

- `visible()` 的 keys 集合加入 `accuracy_validity`

### 8.14 文档

- `README.md`：删除「精度测试尚未实现」的表述，补 acc 支持范围
- `DESIGN.md`：补 acc 分支与结果契约
- `.agents/skills/ascend-llm-eval/SKILL.md`：增加「精度模式」小节（模式选择、case 字段、推荐值、结果解读、不做门禁）
- `references/benchmark-preflight.md`：补 GPQA 数据与导入预检
- `references/result-validation.md`、`references/native-result-evidence.md`：补 `accuracy_validity` 与产物路径
- 如 `skill_bundle.py` 存在冻结摘要，同步更新

---

## 9. 实现顺序

1. 四个 schema（8.1–8.4）。先让契约能表达 acc，再动实现。
2. `plan_model.py`（8.6）。保证 normalize / bindings 不崩、交叉校验生效。
3. `aisbench_cli.py`（8.5）。配置生成与命令构造。
4. `benchmark_preflight.py`（8.7）。
5. `accuracy_evidence.py`（8.8）。
6. `evaluate.py`（8.9）。
7. `result_contract.py` + 两个 executor + `owned_eval.py` + `event_journal.py`（8.10–8.13）。
8. 测试（第 10 节）。
9. 文档（8.14）。

每完成 1–7 中一步就跑一次 `python -m pytest tests -q`，不要攒到最后。

---

## 10. 测试计划

工作目录：`.agents/ascend_llm_cli`

```bash
python -m pytest tests -q
```

### 10.1 新增 `tests/test_accuracy_evidence.py`

用临时目录造 AISBench 产物，覆盖：

| 场景 | 期望 |
|---|---|
| 198 行合法 predictions + results + logs + summary | `validated.status = PASSED`，`accuracy.value` 等于 results 值，validity `VERIFIED` |
| 197 行 / 199 行 | `ProtocolError`，`ACC_PREDICTIONS_COUNT_MISMATCH` |
| 某行 `success=false` 或缺 `gold` | `ProtocolError`，`ACC_PREDICTIONS_INVALID` |
| results 缺失 | `ProtocolError`，`ACC_RESULTS_MISSING` |
| `accuracy = 101` / `"abc"` / `NaN` | `ProtocolError`，`ACC_VALUE_INVALID` |
| 两个时间戳目录 | `ProtocolError`，`ACC_EXP_DIR_AMBIGUOUS` |
| 缺 summary | validity `UNVERIFIED`，`missing_artifacts` 含 `ACC_SUMMARY_MISSING`，`value` 仍输出 |
| predictions 指向目录外符号链接 | `ProtocolError` |

### 10.2 扩展现有测试

- `tests/test_plan_model.py`
  - acc case 规范化成功且 `derived == []`
  - acc case 不要求 `reset_cache`
  - acc case 绑定 `jsonl` 数据集 → 拒绝
  - perf case 绑定 `aisbench_task` → 拒绝
  - perf 行为与现有断言完全一致
- `tests/test_result_contract.py`
  - acc 结果通过 schema
  - acc `accuracy_validity=VERIFIED` + `evidence_status=PARTIAL` → 拒绝
  - perf 结果仍通过
  - 缺 `evaluation_type` 的历史结果按 perf 处理
- `tests/test_benchmark_preflight.py`
  - 198 行 CSV 通过；197 行被拒
  - 缺 `gpqa_diamond.csv` 被拒
- `tests/test_evaluate.py`
  - `commands()` acc 分支不包含 `--num-prompts` / `--num-warmups` / `--mode perf`
  - 生成的配置文本不含 `test_range`
- `tests/test_external_runtime.py` / `tests/test_owned_executor.py`
  - 成功事件带 `accuracy_validity`，不带 `performance_validity`
  - 失败事件带 `evaluation_type` 与对应 validity

### 10.3 手工端到端（需 Linux + AISBench + GPQA + 已部署服务）

1. 准备 plan/bindings（见附录 B），`prepare-inputs(stage=eval)` 审查通过、`unresolved=[]`
2. 提交 eval，观察 `phase.changed` 至少覆盖 health → preflight → benchmark
3. 检查 `<operation>/benchmark/`：
   - `validated.json`：`num_samples=198`、`all_requests_succeeded=true`
   - `accuracy-evidence.json`：`status=VERIFIED`
   - `outputs/<timestamp>/summary/` 三个文件存在
4. 检查 `stage-result.json`：`evaluation_type=acc`、`accuracy_validity=VERIFIED`、`accuracy.num_samples=198`
5. 检查 `evaluation.completed.data` 含 `accuracy_validity`
6. 负例：临时把 CSV 截成 197 行，确认硬失败且不产出 `accuracy`

---

## 11. 兼容性与迁移

- 已有 perf plan 不需要改：`mode` 缺省为 `perf`。
- 已有 perf 结果文件：schema 允许缺少 `evaluation_type` 的读取路径按 perf 处理；新写入必须带 `evaluation_type`。
- 已冻结的 perf 任务包（`bundle_digest`）不受影响，因为 schema 只放宽了 conditioned requirement，未改 perf 必填字段。
- 事件消费方若强依赖 `performance_validity`，需改为按 `evaluation_type` 或字段存在性判断；这是本次唯一的对外可见变化，需在 release note 中写明。

---

## 12. 验收清单（Definition of Done）

- [ ] `mode=acc` 的 plan 能通过 schema 与 `normalize_external_plan`
- [ ] perf plan / perf 结果 / perf 事件行为零变化，全量测试通过
- [ ] 生成的配置不含 `test_range`，命令行不含 `--num-prompts`
- [ ] 预检用 `csv.reader` 数出 198 行并核验列数
- [ ] 结构校验覆盖 7.2 全部硬/软条件，错误码为短码常量
- [ ] 结果含 `evaluation_type`、`accuracy_validity`、`accuracy`、`accuracy_evidence`
- [ ] `evaluation.completed` 在 acc 下带 `accuracy_validity`
- [ ] 失败路径不产生伪造的 accuracy，且带 `evaluation_type`
- [ ] `README.md` / `DESIGN.md` / `SKILL.md` / 三个 reference 已更新
- [ ] 手工端到端跑出 `num_samples=198` 的 VERIFIED 结果

---

## 13. 已知风险与后续

| 风险 | 影响 | 处理 |
|---|---|---|
| 完全信任 `results/*.json` 的 `accuracy`（D7） | 若 AISBench 评测器本身有 bug，无法发现 | 已接受；后续可增加「用原生 postprocessor 重算」作为可选强化 |
| `max_out_len=512` 会被思维链模型截断 | 精度偏低且原因隐蔽 | 写入 `actual_load` 与文档；由用户显式调大 |
| AISBench 版本升级导致产物布局变化 | 结构校验失败 | 预检已导入关键类；错误码可定位，按版本更新常量 |
| `url` 依赖服务 endpoint 是 base URL | 请求路径错误 | 与 perf 保持同一规则；部署阶段已保证 base URL |
| 多 case 混合模式下事件消费方只看一个 validity 字段 | 误判 | 文档与 release note 明确按 `evaluation_type` 读取 |

---

## 附录 A：关键事实速查

- 分支：`feat/ascend-llm-skills`
- 原生数据集配置：`ais_bench/benchmark/configs/datasets/gpqa/gpqa_gen_0_shot_str.py`
- 评测器：`ais_bench/benchmark/datasets/gpqa.py`（`accuracy = 100 * correct / count`）
- 数据文件：`ais_bench/datasets/gpqa/gpqa_diamond.csv`（198 行 / 78 列 / 含引号换行）
- 精度产物写入点：`ais_bench/benchmark/tasks/openicl_eval.py`、`openicl/icl_inferencer/output_handler/base_handler.py`
- 参考 smoke：`smoke_tests/test-case/run_server_accuracy/llm_datasets_main/accuracy_gpqa_diamond/`
- 模型配置参考：`ais_bench/benchmark/configs/models/vllm_api/vllm_api_general_chat.py`
- `VLLMCustomAPIChat._get_url()`：`urljoin(base_url, "v1/chat/completions")`

## 附录 B：完整输入样例

`plan.json`：

```json
{
  "schema_version": "1.0",
  "task_id": "gpqa-acc-001",
  "intent": "external_eval",
  "service": {
    "service_id": "svc-qwen",
    "endpoint": "http://10.0.0.10:8000/",
    "model": "qwen",
    "ownership": "external",
    "deployment_summary": null,
    "permissions": {"reset_cache": false, "restart": false, "release": false}
  },
  "cases": [
    {
      "case_id": "gpqa_diamond_acc",
      "mode": "acc",
      "dataset_id": "gpqa",
      "concurrency": 8,
      "max_out_len": 512,
      "timeout_seconds": 3600
    }
  ],
  "test_binding": "tester-a",
  "policy": {"idle_timeout_seconds": 600, "on_idle": "keep_external_service", "on_failure": "stop_owned_clients"},
  "unresolved": []
}
```

`bindings.json`：

```json
{
  "schema_version": "1.0",
  "task_id": "gpqa-acc-001",
  "testers": {
    "tester-a": {
      "host": "10.0.0.10",
      "work_root": "/data/eval/work",
      "python": "/usr/local/python3.10/bin/python3",
      "aisbench": "/usr/local/python3.10/bin/ais_bench",
      "model_path": "/models/qwen",
      "machine_id": "0123456789abcdef0123456789abcdef",
      "datasets": {
        "gpqa": {
          "type": "aisbench_task",
          "task": "gpqa_gen_0_shot_str",
          "path": "/data/datasets/gpqa"
        }
      }
    }
  }
}
```
