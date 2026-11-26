# Ascend LLM CLI

为新版本 Ascend LLM skills 提供 JSON 驱动的部署、评测与资源释放能力。

## 功能边界

- 只部署：部署环境预检、容器及模型启动、服务健康检查、冒烟测试；成功返回 service.json 并保留服务。
- 只评测：接受 endpoint + model，或已有 service.json；核验测试工具、依赖及服务健康，运行 AISBench 性能或精度测试并输出结构化结果。不需要部署配置，也不取得停止外部服务的权限。
- 部署后评测：把 service.json 交给一个独立评测任务，可使用新的 case 和测试环境，不必重新部署。
- release/cancel：按归属释放本任务资源。停止 runtime 不等于释放服务。
- 精度评测与性能评测是同级 `mode`：`perf` 跑性能负载，`acc` 支持 AISBench 原生任务目录（GPQA-Diamond `gpqa_gen_0_shot_str`、GSM8K `gsm8k_gen_0_shot_cot_str`）并报告 `accuracy`；不把健康或冒烟测试视为精度评测。

## 安装

Python >=3.11。控制端可运行在 Windows；执行端需要 Linux、jsonschema，以及对应阶段使用的 Docker/驱动或 AISBench。部署不要求 AISBench；只评测不要求 Docker/NPU 管理权限。

```bash
python -m pip install .
deploy-eval-v2 --version
```

命令仍为 deploy-eval-v2，源码在 src/deploy_eval_v2。技能与 CLI 平级，安装五技能使用 install-skills 的 source/destination JSON；技能负责收集需求和阶段决策，CLI 不猜测用户授权或更改负载。

## JSON 交接

| 阶段 | 输入 | 成功输出 |
| --- | --- | --- |
| 部署 | 部署计划、物理绑定和审阅结果 | service.json，包含端点、归属、健康/冒烟证据引用 |
| 评测 | 服务信息、cases、tester 绑定和审阅结果 | 测试结果 JSON，包含实际负载、按 `mode` 选择的性能或精度证据与有效性 |
| 释放 | service_id、task_ref、请求身份及当前版本 | 资源释放结果 JSON |

先使用 prepare-inputs 把业务 JSON 转成待审阅的 plan.json / bindings.json。此步骤仅本地校验，不授权、不连接现场、不执行部署。

只部署输入：

```json
{"stage":"deploy","destination":"prepared-deploy","deployment":{"schema_version":"1.0","task_id":"deploy-01","intent":"deploy","deployment":"按 deploy-plan Schema 填写部署对象","policy":{"idle_timeout_seconds":900,"on_idle":"keep_service","on_failure":"release_owned"},"execution":"按 Schema 填写超时与部署参数","unresolved":[]},"bindings":"按 deploy-bindings Schema 填写节点绑定对象"}
```

上例展示字段组成，字符串占位必须替换为 Schema 指定的对象。部署输入禁止嵌入 evaluation/testers；保留服务策略必须显式为 keep_service。

只评测输入（路径和主机需替换为现场值）：

```json
{"stage":"eval","destination":"prepared-eval","task_id":"eval-01","endpoint":"http://node.example:8000","model":"served-model","tester":{"host":"node.example","work_root":"/work/tasks","python":"/venv/bin/python","aisbench":"/venv/bin/ais_bench","model_path":"/models/tokenizer","datasets":{"synthetic":{"type":"aisbench_synthetic"}}},"cases":[{"case_id":"c1","concurrency":1,"input_tokens":128,"output_tokens":16,"formal_requests":4,"num_warmups":1,"steady_state":false,"reset_cache":false,"dataset_id":"synthetic","timeout_seconds":300}]}
```

也可用 `service` 对象代替 endpoint/model；该对象可直接使用部署输出。评测任务不继承服务生产者的释放权限。独立评测当前不允许清缓存，需使用已审阅的完整计划路径显式授权该操作。

精度 case 与性能 case 同级，使用 `mode=acc` 和 `aisbench_task` 数据绑定，不允许混入性能字段：

```json
{"stage":"eval","destination":"prepared-acc","task_id":"acc-01","endpoint":"http://node.example:8000","model":"served-model","tester":{"host":"node.example","work_root":"/work/tasks","python":"/venv/bin/python","aisbench":"/venv/bin/ais_bench","model_path":"/models/tokenizer","datasets":{"gpqa":{"type":"aisbench_task","task":"gpqa_gen_0_shot_str","path":"/data/datasets/gpqa"}}},"cases":[{"case_id":"gpqa_diamond_acc","mode":"acc","dataset_id":"gpqa","concurrency":8,"max_out_len":512,"timeout_seconds":3600}]}
```

`path` 必须是数据集绝对目录：GPQA 含 `gpqa_diamond.csv`，GSM8K 含 `train.jsonl` 与 `test.jsonl`。样本数由预检在测试机现场统计，不在代码或 schema 写死；case 可写 `num_prompts` 走 `--num-prompts` 取前 N 条（不得超过实际条数；mini 模板默认 30，结果标注 subset 且不可与全量对比）；命令固定 `--mode all`，不写 `test_range`、不重置缓存、不采集性能指标。结果报告 AISBench 评测器返回的 `accuracy`（百分制）与 `accuracy_validity`。

```bash
deploy-eval-v2 prepare-inputs --request task.json
deploy-eval-v2 export-contracts --request export.json
```

export.json 为 `{"destination":"contracts"}`，从当前安装包导出唯一权威 Schema。完整的审阅、打包、冻结、发布、启动及阶段请求字段见 [DESIGN](DESIGN.md)。运行阶段使用 `deploy-eval-v2 --config CONTROL deploy --request FILE`（eval/release 同理）；具体示例见设计文档。

派单契约在进入 plan 前用 `deploy-eval-v2 validate-top-input --request top_input.json` 校验；返回 `{valid, scenario, issues[]}`，不读 `--config`、不连接现场，退出码 2 表示存在缺口。

## 输出与日志

stdout 仅返回有界 JSON。长操作先返回 RUNNING、operation_id 和 next_cursor，通过 wait-event 继续等待边界。成功与否以业务结果为准，不只看 CLI 退出码。

每个执行工作根只有一份框架 `task.jsonl`，同时承载持久事件与框架诊断。服务日志、AISBench 控制台及原生产物保留；不新增 preflight/postflight/worker/executor 日志文件。远端节点/测试器的诊断先暂存，阶段响应后有界回收到主任务 task.jsonl，校验摘要并持久化后才确认清空远端暂存。断线或并发追加导致回收不完整时保留暂存，并记录 remote.log.pending；日志回收不重新执行部署或负载。

## 本地验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

2026-11-13 已完成 Ascend950DT、DeepSeek-V4-Flash、DP4/TP1 的 1P1D 真实技能功能验收，覆盖部署并评测、仅部署保留服务、独立评测后由原归属流程释放服务。各场景使用全新测试 Agent；原生合成负载为 concurrency=1、input_tokens=128、output_tokens=16、formal_requests=4、num_warmups=1、steady_state=false、reset_cache=false。健康、冒烟、原生性能证据、日志归集及自有资源收尾通过；这不是稳态性能、精度或其他平台兼容性结论。现场报告与调试产物保存在仓库外。

本地回归全量通过；Linux 专用项在不支持 Unix socket 的系统上跳过。Linux 专用项在不支持 Unix socket 的系统上跳过；本地模拟与原生产物 fixture 不等于模型或 NPU 测试。CLI 的 acceptance=local-only 标签仍保留，不以单一现场功能验收承诺通用生产就绪。

容器 tester：existing 场景 tester 与部署节点一样在容器内闭环，绑定 `container.id`（完整容器 ID）与 `container.image`（sha256 镜像摘要；`self` 节点可省略，非 self 节点仍须给出）；`python`/`aisbench`/`model_path`/`environment`/`datasets`/`machine_id` 全部属于容器，`container_work_root` 为容器内操作根。owned 场景 tester 在宿主，不写 `container`。复用已有运行容器，只创建任务独立工具目录，不安装依赖或授予容器管理权限。工具预检和负载在容器执行，证据留在容器内由控制端读取。

## 宿主绑定与现场预检

节点绑定 `nodes.<slot>` 支持可选 `guard_root`（绝对路径），默认 `/var/lib/deploy-eval-v2/guard`。
该目录是宿主机全局的资源占用声明与命令日志根，必须由执行用户可写，且不得位于节点运行根内；
无 sudo 的现场应显式绑定到用户可写位置。

`publish-task` 在上传前用 SSH 验证每个部署节点的 guard_root 可创建可写，失败返回
`GUARD_ROOT_UNAVAILABLE` 且不上传任何 payload，修正绑定后可重试；`check-ready` 离线校验
同一规则（`GUARD_ROOT_INVALID`）。启动阶段的失败一律保持 `UNVERIFIED`，必须先核对进程、
容器和端口状态。

`docker create` 的算力卡来自排布（`npu_ids` → `/dev/davinciN`），不再由默认列表枚举，
因此不假设卡数（含 8 卡以上）。已分配卡缺失时在任何资源声明之前拒绝，不产生 claim 或命令记录；
默认 `runtime.container.devices` 只列附属节点，驱动差异导致的缺失（如 `/dev/ummu`、`/dev/uburma`）
会被跳过并写入 `probes/<digest>.json` 证据。显式列出的设备只有附属节点允许缺失；其他设备缺失
仍按原行为拒绝，避免设备名写错时静默少挂。显式 `mounts` 仍要求源路径存在，缺失即拒绝。

## 显式容器逻辑卡映射

`placement.devices`、`npu_ids`、资源声明、占用检查与 `npu-smi` 探测始终表示宿主物理卡。
借用已有容器（`runtime.mode=existing_container`）若把所选物理卡以不同逻辑编号暴露，可在
该节点 bindings 的 `container` 中声明 `"device_id_map": {"5": 0, "6": 1}`（键为宿主物理
NPU 号，值为容器内逻辑号）。此时 `ASCEND_RT_VISIBLE_DEVICES` / `device-ids` 使用逻辑编号；
若未显式提供进程选择器，编译器按该映射生成。`ASCEND_VISIBLE_DEVICES` 仍按物理编号校验，
不会单独抑制逻辑选择器的生成。不得仅凭 `--device` 参数顺序推断该映射。

映射必须按节点恰好覆盖该节点本次 placement 的物理卡；逻辑编号必须从 0 连续且唯一；
`src:dst` 设备路径写法与 owned 模式出现该字段都会被拒绝。不带此字段的既有计划行为不变。
映射随 bindings 冻结并写入编译后的节点配置。`check-ready` 静态校验映射，`self`/本地节点
还会执行 `npu-smi info -m` 比对 `Chip Logic ID`。`create` 在占用探测后、启动前再核对一次：
声明不符或未声明却检测到重编号即 `RECOVERY_REQUIRED`，证据写入
`guard/probes/<digest([command_id,'device-map'])>`；`-m` 不可用且未声明映射时按旧行为
放行并记 inconclusive。这不代表运行时一定可用，也不代表服务部署或性能验收通过。
