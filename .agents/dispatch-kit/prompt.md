# 派单 prompt 模板

把尖括号替换为实际值，整段作为新 subagent 的唯一输入。不要附加历史对话、旧 top_input 或示例文件内容。

```text
你是派单方派出的执行 agent。仓库根：<REPO_ROOT>。

技能入口：<REPO_ROOT>/.agents/skills/ascend-llm-router/SKILL.md；先读它，再用 references/top-input.md §0 核对环境、§1 核对任务形态，不要从示例反推。

本次环境结论：<existing | owned>。
<existing：控制节点 n=<NODE_ID>，该节点 container.self=true（当前所在容器）。镜像摘要、network、container_work_root、machine_id 由你现场取证（machine_id 用 `deploy-eval-v2 identity` 在实际执行位置读取，优先 `/etc/machine-id`，为空时 hostname 派生；其余字段 self 节点本地读取，其他节点经 SSH + docker 进入后 inspect/exec 读取）；self 节点容器 id 自证，对端容器 id 以派单值为准并核对。单机只有这一节点，本地执行；双机 PD 另一节点不写 self，对端 IP=<PEER_IP>、容器 id=<PEER_CONTAINER_ID>、SSH 访问为 <HOST/USER/KEY>。>
<owned：派单方已授权 CLI 创建并在释放时删除容器，deployment.image=<sha256:...>，不写 nodes.<id>.container，也没有 self。>

本次任务形态：action=<deploy_only | deploy_and_eval | eval_only>，topology=<colocated | pd>，eval_mode=<perf | acc>；on_idle 未给时按 keep_service 写回 top_input 并在报告中声明。
<有示例：只读 <EXAMPLE_PATH> 与 <PLAYBOOK_PATH>，不读其他场景的 playbook。>
<无示例：该组合没有示例，按 references/top-input/contract.md 组装，不得因此改判运行时模式。>

授权与目标是这份 top_input：<TOP_INPUT_PATH>
<若直接粘贴内容，替换为整份 JSON；否则只给路径。>

执行要求：
1. 先运行 `deploy-eval-v2 validate-top-input --request <TOP_INPUT_PATH>`。valid=false 时按缺口来源处理：现场事实自己取证、派生项按 references/top-input/derivation.md 补齐后重跑；只有授权与目标类缺口才一次性列出并停止，不进入 plan，不自行从示例补全。
2. 按示例 playbook 或契约组装 plan/bindings；阶段交接时重新读取目标阶段 SKILL.md 并记录边界事件、游标与技能路径，不预加载 deployment/eval/release。
3. 交付类产物只落在 `<deliverables.local_dir>/<task.id>`（交付根），运行证据只落在 `<allowed_root>/<task.id>`（节点运行根）；不写仓库根或临时路径。派单未给 `local_dir` 时按缺省 `<当前目录>/deploy-runs` 派生，该目录属于约定交付位置，不算“写仓库根”。
4. 授权只收紧不扩大：不改 host/devices/allowed_root/model_paths。existing 下不创建、启动、停止或删除容器，self 节点不做 SSH 与 docker inspect/exec；owned 下容器由 CLI 命令创建与删除，不手工执行 docker。不读历史任务目录、归档或回收站。
5. 从第一条现场/CLI 命令开始，记录命令、原始 stdout/stderr、实际退出码、时间戳，并使用唯一文件名；失败的输出不被覆盖。
6. 每完成一个 CLI 阶段（归一化、校验、冻结、发布、启动、部署、等待事件）就向 `<deliverables.local_dir>/<task.id>/progress.log` 追加一行 `PHASE <阶段> <rc> <耗时秒> <UTC 时间>`；失败同样追加（rc 非 0）。这是外层监控的唯一约定入口，不要依赖对话输出或模型 token 流。
7. 结束时先运行 `deploy-eval-v2 --config <deliverables.local_dir>/<task.id>/control.json task-summary --task-ref <task.id>`，以它作为交付全景（只读派生，不改状态）；再报告四项状态：执行、证据完整性、性能或精度有效性、清理；给出 limitations 与 confidence。部分证据不得写成完整成功。

完成后只回报：task.id、实际场景 id、四项状态、产物根、以及任何偏离派单的地方。
```
