# CLI 公开契约（0.1 / protocol 1.0）

补丁版本以 `deploy-eval-v2 --version` 的实际输出为准；本文不固定 alpha 编号。

## 准备、审阅与发布请求

以下均为 `deploy-eval-v2 COMMAND --request FILE` 的公开 JSON 请求。表中字段全部必填，不添加阶段命令的 request_id/expected_version 等字段。路径是控制端本地路径；远端路径仅来自 control.json 和冻结绑定。所有摘要使用 CLI 返回的原值。

| COMMAND | FILE 的字段 | 输出及约束 |
| --- | --- | --- |
| package-runtime | destination | 将当前已安装 CLI 打包到新的本地 ZIP 文件；返回 runtime_package，其中 archive、runtime_sha256 用于后续命令 |
| draft-review | plan, bindings, destination, runtime_sha256 | plan/bindings 为文件路径，destination 为新目录；生成 review.json 和 review-summary.json，初始为 DRAFT，必须按实际授权审阅后批准 |
| freeze-inputs | plan, bindings, review, destination, runtime_sha256 | review 为已审阅文件路径；返回 input_bundle.path 与 input_bundle.sha256，后者作为 bundle_sha256 |
| verify-inputs | directory, sha256, runtime_sha256 | directory 为冻结目录，sha256 为 input_bundle.sha256；只校验，不修改冻结输入 |
| check-ready | task_ref, runtime_archive, input_directory, runtime_sha256, bundle_sha256 | 需要 --config CONTROL；仅本地可执行性检查，不代表现场预检成功 |
| publish-task | task_ref, runtime_archive, input_directory, runtime_sha256, bundle_sha256 | 需要 --config CONTROL；发布包到授权节点运行根，不能预先用其他文件占据该目录 |
| start-task | task_ref, runtime_sha256, bundle_sha256, nonce | 需要 --config CONTROL；两项摘要和 nonce 都必须是64位小写十六进制字符串（正则[a-f0-9]{64}）；nonce 一次生成并保存，未知结果先查询，不更换身份盲目重试 |
| stop-runtime | task_ref, nonce | 需要 --config CONTROL；使用原启动 nonce，仅停止已允许收尾的框架进程，不替代服务释放 |

任务目录分两棵树，职责互斥，逐项目录见 [contract.md](top-input/contract.md#任务目录交付根与运行根分离)：交付根 `task_root = <deliverables.local_dir>/<task.id>` 只在控制机；节点运行根 `node_root = <allowed_root>/<task.id>` 每节点一份，existing 另有容器运行根 `container_root = <container_work_root>/<task.id>`。

下文 `<root>` 一律指 `task_root`；原始证据只能从 `node_root`/`container_root` 经 `read-result` 读取。

`validate-top-input --request <top_input.json>` 在进入 plan 前校验派单契约：不读 `--config`、不连接现场，返回 `{valid, scenario, issues[]}`；退出码 2 表示存在缺口。

例如 package-runtime 的请求文件内容为 `{"destination":"<root>/runtime.zip"}`。所有 `destination` 必须指向该根内的新文件或新目录，不得写到仓库根、当前目录或任意临时路径。准备顺序为打包、审阅、冻结、check-ready，再由对应阶段技能发布与启动。destination 使用未存在的新文件或目录（类型按表）；冻结后不手工修改内容。`runtime_archive` 与 `input_directory` 必须位于 `task_root` 内，跨目录即视为输入缺口。prepare-inputs/export-contracts 见下文及 plan 技能。control.json 的任务键、冻结 plan.task_id 和 task_ref 必须一致。

无参 `deploy-eval-v2 identity` 不读 request 与 config，在本地输出 `{"machine_id":"<32位小写十六进制>","source":"machine-id"|"hostname-derived"}`，用于在节点/容器内取证 machine identity（见 installation.md 第 5 节）。

nonce 是启动身份随机值，不是摘要计算结果，也不是32位GUID；可用控制端 Python `secrets.token_hex(32)` 生成64位值，保存到请求后始终复用。`INVALID_INPUT: startup digest` 表示 runtime_sha256、bundle_sha256 或 nonce 中至少一项格式不符合上述契约；不能因此把已发布包推断为已启动。对未知启动结果仍按身份与持久回执查询，禁止换nonce盲重试。

生产执行已接入守卫：`freeze-inputs` 校验 schema、语义引用与编译输入；`check-ready` 只做本地检查，不证明宿主就绪；`publish-task` 安装不可变的 runtime/input 包；`start-task` 校验 detached Python 进程与 live socket 身份。实机功能验收只覆盖 CLI README 记录的场景与有界负载，不代表其他宿主已就绪。

## 无模型 socket 流程

无模型的 Linux socket 演练用 `socket-start`，见 [socket-validation.md](socket-validation.md)。它显式模拟各阶段，Agent 仍须通过常规 CLI 自行提交后续每个阶段。

以下命令均在交付根 `task_root` 内执行，文件名相对该根；`--config` 固定使用 `task_root/control.json`：

```text
python -m deploy_eval_v2 --config control.json deploy --request requests/deploy.json
python -m deploy_eval_v2 --config control.json eval --request requests/eval.json
python -m deploy_eval_v2 --config control.json release --request requests/release.json
python -m deploy_eval_v2 --config control.json cancel --request requests/cancel.json
python -m deploy_eval_v2 --config control.json get-status --task-ref task-1
python -m deploy_eval_v2 --config control.json wait-event --task-ref task-1 --after-seq 2 --wait-ms 30000
python -m deploy_eval_v2 --config control.json read-result --task-ref task-1 --artifact-ref task.jsonl --offset 0 --max-bytes 8192
python -m deploy_eval_v2 --config control.json task-summary --task-ref task-1
```

## 任务全景（只读派生）

`task-summary` 从交付根产物、`progress.log` 与 `get-status` 合成 `task-summary/1`，供外层监控和验收直接读取，不解析模型 token 流：

- `task`：action、topology、runtime_mode、`model`/`model_id`、endpoint、connector、节点（container、npu、ports、`device_map`）。`model` 是生效的 served 名（可含点号），`model_id` 恒为 `^[A-Za-z0-9][A-Za-z0-9_-]*$` 的技术 id；外部已有服务只给 `service.model` 时，`model_id` 取其规范化结果（如 `granite-4.2-3b` → `granite-4-2-3b`）。
- `state`：`source=get-status|unavailable|none`；取不到状态时仍返回成功，并在 `limitations` 留痕。
- `phases`：固定 11 个阶段名；有 `progress.log` 心跳时以心跳 rc 为准，否则取 `results/<op>/stage-result.json` 的产物状态，两者都没有记 `unknown`；动作本身不经过的阶段记 `not_applicable`（如 `eval_only` 的 `deploy`/`release`、`deploy_only` 的 `eval`、`deploy_and_eval` 的 `prepare-inputs`），`detail.source=action`。`progress.log` 里的非枚举名字（`init`、`*-submit`、`*-pending` 等）不扩充 `phases`，只在 `limitations` 留痕。
- `operations`：deploy / eval / release 的摘要与证据引用；`service`、`evaluation`、`cleanup` 是从中提取的投影。
- `limitations`：冻结缺失、状态不可达、心跳缺失等。

该命令只读，不写状态、不触碰服务、claims 或日志；`task_root` 必须是交付根，普通响应仍受 8 KiB 预算约束。业务成败以事件和证据为准，summary 不替代 `get-status`、`service.json` 与原始证据。

control.json 是受信控制端配置，固定位于交付根 `task_root/control.json`：tasks 下以 task_ref 为键，值包含 `host`、`root`（节点运行根 `node_root`）、`allowed_root`、`task_root`（交付根）和可选 `python`。`root` 必须位于 `allowed_root` 内且不能等于它；`task_root` 必须是绝对路径，末段等于 task_ref，且不能等于或位于该任务的 `root` 内。旧版 control.json（无 `task_root`）与旧 launch identity（`task_root` 键）不兼容，升级后按新契约重写，不混用。配置不含密码。需要发布测试端时，在任务条目下加 `tester`，键名为 `host`、`root`（测试端运行根，取 `work_root/task.id`）和可选 `python`；与 tasks 条目同形，但不写 `allowed_root` 与 `task_root`。同机测试且 tester 与任务运行根一致时可省略该段。

当控制端本身运行在该任务的借用容器内（`bindings.nodes.<n>.container.self=true`）时，任务条目额外写 `self: true`。该条目的 publish-task、start-task、stop-runtime 与所有查询改在本地执行，不再调用 ssh；check-ready 只有在存在非 self 目标时才要求 ssh，并额外校验 self 节点唯一、等于控制节点、本机身份等于该节点 `machine_id`。本机身份走统一身份链：优先 `/etc/machine-id`，为空或不可读时用 hostname 派生（见 installation.md 第 5 节），可用 `deploy-eval-v2 identity` 直接查看。runtime 侧通过 `/.dockerenv` 自证处于容器内，缺失即失败。

副作用请求公共字段：task_ref、request_id、expected_version、wait_ms（0—30000）；protocol_version 可省略，CLI 补为 1.0。deploy 加 plan_ref；eval 加 service_id、case_id；release 加 service_id；cancel 加 scope（task/operation）、operation_id。CLI 将 task_ref 映射为协议 task_id，kind 由子命令决定。业务配置放冻结 JSON，不扩展成零散 CLI 参数。

使用 `export-contracts --request export.json`（内容为 `{"destination":"<root>/contracts-001"}`）导出当前安装包的 Schema。`destination` 必须是不存在的新目录，由 CLI 创建；不要先 `mkdir`，否则返回 `REQUEST_CONFLICT: contract destination exists`。`command.schema.json` 位于导出目录；它是传输内部请求契约，不是 plan Schema；plan/bindings 的权威 Schema 与它同在导出目录（deploy-plan、deploy-bindings、external-eval-plan、eval-bindings）。production `plan_ref` 必须与冻结包摘要一致。

## 事件与错误处理

- RUNNING：保存身份与 next_cursor，用 wait-event 继续观察。提交类命令返回的 `next_cursor` 已消费此前事件（含该命令自身的 `command.accepted`），`wait-event` 只返回其后事件；核验 `command.accepted` 以节点 `task.jsonl` 为准，不要据此判定事件缺失。
- EVENTS：按 task_id+seq 去重；has_more 时继续补页，不能自行跳到最新游标。deployment.ready 还须核对健康/冒烟证据。
- WAIT_TIMEOUT：保持任务运行判断，继续使用安全游标；不重复粘贴服务配置。
- STALE_VERSION：重读事实并重新决策，不盲改版本重发。
- REQUEST_CONFLICT：同幂等键内容不同，停止并核对原请求。
- UNKNOWN_OUTCOME、协议坏 JSON、SSH 断开：先查状态和原游标，不创建新任务或重跑部署。
- OBSERVER_UNAVAILABLE：只读查询（get-status/wait-event/read-result）的观察进程未能启动或完成，例如其解释器缺少依赖或执行超时。它只说明取证失败，既不表示任务结果未知，也不否定已有清理结论；应先用可用解释器重查，或采用 task-summary 的本地快照回退。写操作失败仍返回 UNKNOWN_OUTCOME。
- RECOVERY_REQUIRED、release.unverified：保留现场，停止复用资源，核验归属与执行证据。

退出码已实现：输入/状态错误 2、通信未知 3、确认操作失败 4；退出码 0 只表示成功提交/查询/等待（含停机后的离线状态查询），业务成败仍以事件为准。

普通响应预算 8 KiB、每页最多 10 关键事件；日志默认 8 KiB，显式最多 32 KiB，包含 JSON 转义与元数据。先按 artifact_ref 读取一次所需片段，再按 next_offset 续读；不从 SSH 获取整份服务日志。定位支持互斥的 --tail-bytes N、--line-start N --line-end M、--keyword TEXT；按返回的扫描游标续读。task.jsonl 为统一框架日志，service.json 为部署交接（`endpoint` 记节点 IP；同机同容器可用 `127.0.0.1:<port>`，远端 tester 必须用节点 IP，回环不跨机）；results/ 与 logs/ 下只读相应证据，禁止目录遍历。它们都在节点运行根 `node_root`（existing 模式的原生产物在 `container_root`），交付根的 `results/` 只放摘要与引用。
## 取消语义

`scope=operation` 必须给出当前活动操作的 `operation_id`；`scope=task` 可省略该字段，用于取消仍持有自有服务的空闲任务。若给了 operation ID，必须与活动操作一致。任务取消是持久的，会阻止后续阶段；清理异步完成，必须检查其事件。验收不等于资源已释放。

任务已以 `cleanup_status=VERIFIED` 结束且属主不再持有 claim 时，空闲任务取消直接复用该结论而不重跑清理：发出 `operation.cancelled`/`CANCELLED` 并带 `reused_from_seq`，不触碰 claims 或命令历史。仍有 claim 时照常重新清理。

本地业务输入：`prepare-inputs --request task.json`（详见 plan 技能）返回 plan/bindings 路径及待审阅默认值；`export-contracts --request export.json` 导出与 CLI 同版本的权威 JSON Schema。新日志布局仅用于新任务，不用新包接管历史冻结任务。远端日志归集未完成时 task.jsonl 记录 remote.log.pending，不能声称日志完整；原生服务/AISBench 日志保持原位。

当前 CLI：原子JSON产物采用与事件摘要相同的规范序列化，expected-sha256验证文件字节；不改旧包旧产物。read-result在runtime socket不可用时，可核对持久任务身份后只读本任务节点运行根的受限路径产物；不恢复执行、不改变状态或授权其他任务。停机后仍按offset/next_offset有界取回日志，并独立核验完整性。

get-status 同样支持停机后离线查询：socket 不可用时，核对同一持久身份后只读 state.json，返回 `offline=true`、`source=state.json`、`runtime=STOPPED|UNOBSERVED`。若 task.jsonl 末条记录比快照新，则标记 `stale=true` 并给出 `journal_next_cursor`；末条为终态事件时以该终态为准。身份或快照无法核验时仍返回 UNKNOWN_OUTCOME。该路径不恢复执行、不写状态、不触碰 claims/effects。`runtime=UNOBSERVED` 且状态非终态时不要再 wait-event，按 RECOVERY_REQUIRED 保留现场。进程已确认退出的重复 stop-runtime 返回 `runtime_stopped=true` 与 `already_stopped=true`；进程仍存活但 socket 不可达时仍为 UNKNOWN_OUTCOME。

`task-summary` 在状态查询不可用时，会在控制进程内直读本任务节点运行根的同一持久快照（不新起解释器），返回 `state.source=state.json`、`state.offline=true` 与终态；持久证据无法核验时才退回 `state.source=unavailable` 并把 `OBSERVER_UNAVAILABLE` 记入 limitations。
