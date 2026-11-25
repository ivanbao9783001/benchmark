---
name: ascend-llm-eval
description: Deploy and Eval v2 的服务测试规划、结果证据分析与阶段交接；支持已有产物离线分析、本地/远端测试端及既有容器内 AISBench。
---

# Eval

依赖 deploy-eval-v2 CLI 0.1 与协议 1.0，先执行 `deploy-eval-v2 --version` 核对。共享 CLI 契约随 ascend_llm_cli 包提供；未安装时按 [安装与前置](../ascend-llm-router/references/installation.md) 安装并复验，安装失败才停止报告，不用其他实现替代。自有服务支持绑定远端测试机，需发布冻结包并核对 machine_id、独立 Python 进程能力 和主节点到测试机的 SSH；外部服务的 runtime 在测试机运行。远端停止未核验时保留服务和资源声明，进入 RECOVERY_REQUIRED。缓存重置只适用于 owned 评测：须有冻结权限及逐实例目标，全部实例核验空闲后执行，结果不明不重试、不启动负载；external_eval 由 `prepare-inputs` 固定 `reset_cache=false`，因此外部服务不具备清缓存能力，不得承诺或尝试。原生汇总及稳态窗口会按证据判定；缺失时间戳、未知汇总格式或前缀路由覆盖不完整时保持 UNVERIFIED，不将请求成功写成性能有效。

独立入口接受 endpoint+model 或部署产出的 service.json（`endpoint` 记节点 IP；同机同容器服务也可用 `127.0.0.1:<port>`，远端 tester 必须用节点 IP），加 cases 与 tester JSON；通过 plan 的 prepare-inputs 准备新评测任务，并重新健康检查，不继承部署任务释放权限。执行输入为 service handle 和一个冻结 case；不依赖 deploy 私有 dispatch.json。外部端点通过 external_eval 计划登记，不要求先 deploy，也不自动获得清缓存、重启、停止或释放权限。不用 fake-service 冒充外部服务。

目标执行顺序：重新检查服务健康 → AISBench/数据核验 → 按 case.mode 分支：性能执行显式获授权的缓存操作、预热、正式测试与指标采集；精度执行原生全量推理与评测 → 自有客户端清理 → 原生结果及指标证据核验。每次使用阶段剩余时间，不能把多次调用各自的完整超时累加突破 deadline。

保留用户显式负载；默认普通请求数为并发×4、稳态为×10。不得为演示减少请求数、改变端口或丢弃未知参数。记录合成长度及前缀偏差，不自动判失败，不把目标前缀比例写成缓存命中率。

精度 case（`mode=acc`）与性能 case 同级：绑定 `type=aisbench_task` 的原生任务，当前支持 `gpqa_gen_0_shot_str`（目录含 `gpqa_diamond.csv`）与 `gsm8k_gen_0_shot_cot_str`（目录含 `train.jsonl` 与 `test.jsonl`，评测 `test.jsonl`）。样本数由预检在 tester 侧统计实际条数，不写死常量；可选 `num_prompts`（1..实际条数）走 AISBench `--num-prompts` 取前 N 条，mini 模板默认 30；配置不写 `test_range`、不做缓存重置或指标采集。命令为 `<aisbench> <config> --mode all --work-dir <case>/outputs`，有 `num_prompts` 时追加 `--num-prompts N`，只转发 AISBench 评测器返回的百分制 `accuracy`，并核验 predictions 有效样本数行、results JSON、infer/eval 日志与 summary；日志或 summary 缺失时 `accuracy_validity=UNVERIFIED`，但保留原值。子集结果须标注 subset，不可与全量对比。

分析时分别给出 execution_status、evidence_status，以及按 `evaluation_type` 选择的 performance_validity 或 accuracy_validity。性能分支检查 AISBench 退出码、预期模型×数据集结果组、请求数量/成功状态、worker 日志和稳态窗口；精度分支检查预测行数、原生 results、日志存在性，以及输出截断率（达到 max_out_len 的样本占比）。请求完成但没有有效稳态窗口可为 SUCCEEDED + UNVERIFIED；未知指标不填零。

evaluation.completed 仅表示阶段结束。按结果决定下一 case 或收尾，不在子技能内递归调用 deploy。测试失败先核验自有客户端清理，服务处理遵循冻结策略；外部服务始终不随测试失败释放。交付结果引用、实际负载、证据状态和限制，不粘贴完整日志。

## 本阶段参考与交付

[测试环境预检](references/benchmark-preflight.md)用于解释环境门控；远端测试器见 [远端测试](references/remote-tester.md)，结果核验见 [结果契约](references/result-validation.md)和[原生证据](references/native-result-evidence.md)，owned 性能 case 显式请求清缓存时才读 [缓存重置](references/cache-reset.md)。共享请求字段见已安装 ascend-llm-router 的 references/cli-contract.md。本阶段请求体见 [阶段请求](references/requests.md)，精度与性能共用同一 eval 请求形状。

从 get-status 获取当前版本，使用冻结 service_id/case_id 提交 eval；RUNNING 时按 next_cursor 等待。保持 tester.environment 一致，进程使用直接 Python 启动。交付结果引用、实际负载、三项状态与清理证据；外部服务不交给 release 停止。缺少对应模式的证据时保留 UNVERIFIED。

独立 external_eval 的框架收尾：先核验 evaluation.completed、原生结果和被测服务仍健康，再确认 get-status 无活动 operation。当前CLI不允许直接停止EVALUATED runtime；如无需继续case，使用原评测task_ref和当前expected_version提交 `cancel`（scope=task，新的request_id），只终结本评测会话。external_eval契约不具备部署或服务释放权，此cancel不会停止被测服务。等 operation.cancelled 且 cleanup_status=VERIFIED 后，用原启动nonce调用stop-runtime；再核验外部服务仍健康，补收最终task.jsonl。此流程不是release服务，不需要也不得取得服务部署归属身份。若清理未知或仍有活动操作，报告并按原身份查询，不以手工kill或换任务掩盖未知状态。保留evaluation.completed成功结果；随后task会话终结的CANCELLED不等于性能请求被取消。

## 容器内测试工具
AISBench 可位于宿主或已有运行容器。宿主未找到工具时，继续只读检查已有容器的工具、解释器、依赖、tokenizer和网络；不要要求用户重复提供可自行发现的绑定。服务节点为 `container.self=true` 且 tester 的 `machine_id` 等于本机时，测试工具就在当前容器内本地执行，不经过 SSH 或 `ContainerTester`；判定规则见 [remote-tester.md](references/remote-tester.md)。

容器 tester 的字段、路径归属（`python`/`aisbench`/`model_path`/`environment`/`datasets` 属容器内，`host_python` 与 `control.json` 的 `python` 属宿主）以及 `container_work_root` 的适用条件（容器未把 `work_root` 暴露在同一路径时才需要）以 plan 的 [plan-contract.md](../ascend-llm-plan/references/plan-contract.md) 为唯一来源，本阶段不重述。

本阶段只负责执行边界：按 plan 阶段的冻结前核验结果复核容器身份，并只读确认 endpoint 从容器可达、AISBench 依赖完整（工具存在不代表依赖完整）。使用公开CLI执行，不手写docker exec负载封装。容器仅提供测试环境，不授予停止、重启、删除容器或被测服务的权限。执行器只停止本任务工具进程组；证据回收失败或清理未知必须报告，不能标记完整。

## 服务健康路径
模型和代理健康路由必须分别从本轮参考或实际接口确认，不统一套用/health。deploy实例checks.health绑定各自路径；service.json携带入口health_path；部署后的eval沿用该路径。独立prepare-inputs(stage=eval)允许health_path字段，与endpoint同源；默认/health只适用于已确认提供该路由的服务。用户提供service对象时沿用其health_path。仅设置健康路径不授予服务管理权限。AISBench预检也从容器实际测试环境访问同一路由。

## 有效性与日志归集
普通原生性能case没有shared_prefix_ratio时，性能有效性由原生请求记录、负载和性能汇总证据决定；附加prefix指标保留各自验证状态，不作为普通case的必需门槛。有shared_prefix_ratio时仍须核验数据、前缀指标、路由和预热覆盖，不因普通case规则放宽。原生合成input_tokens是工具配置目标；实际重新分词长度另列synthetic-evidence，不把目标长度冒充实测长度，不自行改造合成数据。目标与实测并列展示，长度偏差只作诊断、不作为通过门槛（synthetic的exact标记同理）；有shared_prefix_ratio时按实测前缀覆盖判定；锚与候选对比时以实测输入长度均值判断负载是否可比。

本地tester（含同宿主容器tester）的case目录为results/<eval operation_id>/benchmark，原生AISBench控制台日志是该目录/aisbench.log；原生summary/details/worker日志由该目录/validated.json和performance-evidence.json中的引用定位，逐一归集。不要误读logs/aisbench.log。preflight.log、postflight.log、container.log是case目录下的框架诊断文件，内容同时按source标签汇入task.jsonl；task.jsonl中出现标签不等于对应文件已归集，容器与远端场景按回传证据核验。JSON证据preflight.json、synthetic-evidence.json等仍在case目录。服务原生日志为logs/<instance>.service.log。远端tester使用响应的remote_evidence定位，不能把主机路径猜为本地路径。

所有read-result分页读到total_bytes，保存原始JSON/退出码并核对连续偏移。停止runtime后可按当前CLI的只读产物接口补收最终task.jsonl。缺少实际要求的AISBench控制台或原生日志时标PARTIAL；不把按设计合并的框架日志标签误当缺失，也不把尚未回收的远端文件标为已归集。
