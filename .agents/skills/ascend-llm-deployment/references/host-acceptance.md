# v2 上机验收

本地代码与验证已完成；本文列出经明确环境授权后执行的上机验收项。每次更换宿主、容器或镜像都要重新验收，历史通过记录不迁移到新环境。
支持：外部服务独立 eval（runtime 在测试机）、或自有服务 deploy → eval → release（测试进程在主节点或显式绑定的远端测试机）。单机与双机 PD 均已有执行路径，双机 PD 已完成 existing 容器的跨机验收；新增拓扑仍建议先单节点跑通再增加节点。

参数必须保留用户原意，不得为上机修改。部署重试已按已确认失败、清理核验及共享期限实现；未知结果不重试。远端独立测试机需配置 tester 发布目标，行为见 [remote-tester.md](../ascend-llm-eval/references/remote-tester.md)。缓存重置仅对 owned 评测可用，必须使用已授权的冻结逐实例目标，行为见 [cache-reset.md](../ascend-llm-eval/references/cache-reset.md)；
`prepare-inputs(stage=eval)` 生成的 external_eval 固定 `reset_cache=false`，外部服务不承诺清缓存。稳态/性能按实际证据判定；缺失时间戳、未知版本格式或路由/预热覆盖不足时返回 UNVERIFIED。测试同时核验执行、证据和资源生命周期。

## 1. 安装与无模型检查

控制端 Python >=3.11，安装本目录构建出的 wheel（自动安装 jsonschema）。
所有远端 runtime/node Python 同样需要 jsonschema；代码发布只发布本包，
不会自动安装第三方依赖。测试端另需计划指定的 AISBench（**tag ≥ `v3.1-20260820-master`**）、数据和 tokenizer。
不把 Windows 依赖 wheel 上传到 Linux；按目标系统/架构准备依赖。

```powershell
py -3.11 -m pip wheel --no-deps --wheel-dir dist .
py -3.11 -m pip install dist/deploy_eval_v2-<version>-py3-none-any.whl
deploy-eval-v2 --version
```

经授权后，在 Linux 的独立测试目录运行无模型集成测试：

按 [socket-validation.md](../../ascend-llm-router/references/socket-validation.md) 的公开 socket-start 流程执行；Agent 自身必须依次调用 deploy、`deployment.ready` 后 eval、`evaluation.completed` 后 release，不需要读源码或单测。旧的开发者单测不能替代 Agent 验收。

在每个目标节点核验 Python 版本、jsonschema 导入、`deploy-eval-v2 identity` 可输出 32 位小写十六进制
身份（身份链见 installation.md 第 5 节，无需预置 `/etc/machine-id`）、
节点运行根目录权限；在 runtime 主节点核验 detached Python process creation 可用及授权。
模型节点另核验 Docker、NPU、驱动、镜像、挂载与模型路径；远端子节点
须能从 runtime 主节点通过非交互 SSH 访问。控制端 SSH 可达不能替代此检查。
正式启动器还会校验 detached Python process 单元及 live socket 身份，不提供后台 & 降级。

## 2. 形成冻结输入

准备 plan.json、bindings.json，完成现场绑定（含由 `identity` 取证并写回的 machine_id）。用
draft-review 生成摘要，审阅通过后形成 review.json，再执行 freeze-inputs。
不要直接把测试 fixture 当作已批准生产输入；其中的主机、路径和参数是示例。

每次源码修改先重新 package-runtime，再重新审阅/冻结；runtime_sha256 与
bundle_sha256 必须匹配。保留原始用户参数与授权证据，不手工改冻结目录。

PD 部署在冻结前还要在两侧节点的镜像内分别只读确认 `deployment.kv_transfer.connector`
已注册且可加载（两节点镜像可能不同）；connector 名与私有键随镜像/版本变化，不能沿用其他现场或示例值。
参考启动配置带的 connector 私有键必须与 `compiled.json` 逐键一致；核验不了记
`UNVERIFIED` 并回问派单方，不用猜测值冻结。

publish.json 的字段：task_ref、runtime_archive、input_directory、
runtime_sha256、bundle_sha256。control.json 的 tasks[TASK] 包含 host、root、
allowed_root、绝对 python 路径；附加节点放在 nodes[SLOT]，同样包含上述字段。

```sh
deploy-eval-v2 --config control.json check-ready --request publish.json
```

此命令只读本地内容，验证冻结输入、runtime 包和部署目标，并报告不支持的
配置。只有 READY_FOR_HOST_PREFLIGHT 才进入现场检查；remote_checks=NOT_RUN
明确表示没有验证连通性、系统服务、镜像或 NPU。它不会启动任何远端操作。

## 3. 发布、启动与阶段闭环

```sh
deploy-eval-v2 --config control.json publish-task --request publish.json
deploy-eval-v2 --config control.json start-task --request start.json
deploy-eval-v2 --config control.json get-status --task-ref TASK
deploy-eval-v2 --config control.json deploy --request deploy.json
deploy-eval-v2 --config control.json wait-event --task-ref TASK --after-seq CURSOR --wait-ms 30000
```

start.json 的字段见 deployment/references/requests.md，nonce 语义见 router 的 references/cli-contract.md；
响应丢失后按原身份查询，不换 nonce 重试。
阶段请求包含 task_ref、request_id、expected_version、wait_ms；deploy 另含
plan_ref（bundle 摘要），eval 含 service_id/case_id，release 含 service_id。
每次后继操作从 get-status 读取当前版本，禁止对旧事件盲发后继操作。

deployment.ready 后核对服务及健康/冒烟证据，再 eval；收到
evaluation.completed 后分别检查 execution_status、evidence_status 和
performance_validity，最后按批准策略 release。外部服务不调用 deploy/release。
wait-event 的 after-seq 只使用实际交付的 next_cursor。

## 4. 必须保留的验收证据

| 场景 | 要求 |
| --- | --- |
| 发布/启动 | 摘要匹配；detached Python process 与 socket 身份匹配；同 nonce 不重复启动 |
| PD KV 配置 | connector 在每个节点镜像的注册表中存在且可加载；两侧 `kv_transfer_config` 的 `kv_connector`/`kv_role`/`kv_port`/`engine_id` 与拓扑一致；私有键来自参考配置并原样出现在 `compiled.json` 和启动 argv |
| existing 设备编号 | `placement.devices`/`npu_ids` 始终是宿主物理号；借用容器重编号时 bindings 必须声明 `container.device_id_map`。`self` 节点未声明却在本地 `npu-smi info -m` 中发现重编号时，`check-ready` 直接返回 `DEVICE_ID_MAP_REQUIRED`（`BLOCKED`，不发布、不启动）。create 在占用探测后、启动前用容器内 `npu-smi info -m` 逐卡核对，证据写 `probes/<digest([command_id,'device-map'])>.json`；声明不符或未声明却检测到重编号即 `RECOVERY_REQUIRED`，`-m` 不可用且未声明时按旧行为放行并记 inconclusive。这类拒绝发生在首个副作用之前，claims 会被释放、cleanup_status=VERIFIED，可按失败收尾并 stop-runtime |
| 短阶段/长阶段 | 返回边界，或 RUNNING 后按游标得到边界 |
| 观察中断 | 结束 client/SSH 后 runtime 仍在；重连无重复模型容器 |
| 幂等 | 同 request_id 同内容返回原操作；不同内容拒绝 |
| 等待超时 | WAIT_TIMEOUT 不取消执行、不重复历史 |
| 部分失败/取消 | 客户端停止；逐资源清理证据可核对；不以取消接纳代表清理完成 |
| 释放 | 仅完整容器 ID 与归属一致时释放；资源占用检查通过后才解锁 |
| 崩溃/不明结果 | 进入 RECOVERY_REQUIRED，保留 claims，禁止盲重跑 |
| 输出 | 普通响应 <=8192 字节；日志按引用读取，原日志保留 |

保留完整交付根与节点运行根、请求文件、响应及现场版本；不要只保存最终 PASS。
恢复不明资源时按初版契约进入 RECOVERY_REQUIRED，保留证据并人工现场核验，不盲目重跑。自动跨节点修复不属于初版要求。owner 可用 task 范围 cancel 发起核验收尾：无 create/unknown 命令历史才释放 claims；create 记录存在但无副作用标记（`effects/<digest([owner,command_id])>.json`）时同样视为未产生副作用、释放 claims；有副作用标记则保持 RECOVERY_REQUIRED。

## 五技能安装

共享 CLI 安装后，使用 `install-skills --request skills-install.json`。请求为
`{"source":"<repo>/.agents/skills","destination":"目标 Agent 的技能目录"}`，安装与前置检查见
[installation.md](../../ascend-llm-router/references/installation.md)。
安装器校验五个技能的身份和 CLI/协议声明；已存在且不同的内容返回
REQUEST_CONFLICT，不覆盖个人草案。先核对并选择新目录进行验收。

NPU 清理核验需要 fuser 可用且有设备检查权限；缺失工具、设备错误、
端口占用或节点不可达均保留资源声明，不终止其他任务。
