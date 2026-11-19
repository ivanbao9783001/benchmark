---
name: ascend-llm-router
description: 昇腾模型部署、性能测试及资源释放的任务入口；识别意图，选择 plan、deployment、eval、release，并根据阶段边界继续任务。
---

# Ascend LLM Router

依赖 deploy-eval-v2 CLI 0.1、协议 1.0；具体补丁版本以 `deploy-eval-v2 --version` 的实际输出为准，不在文档中固定。先运行 `deploy-eval-v2 --version`。五个技能平级安装；缺少依赖时报告，不隐式切换到历史 ascend-llm-deploy-eval。

| 用户意图 | 阶段 |
| --- | --- |
| 部署并测试 | ascend-llm-plan → ascend-llm-deployment → ascend-llm-eval → ascend-llm-release |
| 仅部署 | ascend-llm-plan → ascend-llm-deployment，按授权保留 |
| 测试已有服务 | ascend-llm-plan → ascend-llm-eval，不取得外部服务管理权限 |
| 分析已有结果 | ascend-llm-eval |
| 释放已有任务 | ascend-llm-release |

由当前 Agent 加载下一技能，不递归调用技能或自行启动协调 Agent。已有授权持续有效。阶段交接保留 task_ref、request_id、operation_id、next_cursor、输入摘要、service/case 身份及证据引用。

收到 deployment.ready 后核对健康及冒烟证据；仅部署任务交付 service.json 并保留服务，只有用户要求评测时才进入 eval；evaluation.completed 后按冻结策略进入下一 case 或 release。RUNNING 按游标等待；WAIT_TIMEOUT 不代表失败。结果未知时恢复查询，不更换请求身份盲重跑。具体参数、预检和清理规则由对应阶段技能负责。

共享命令格式和返回语义见 [CLI 契约](references/cli-contract.md)。阶段请求模板由各阶段技能的 references/requests.md 提供，plan 参数模板见 ascend-llm-plan 的 references/templates.md。仅在用户要求无模型验收时读取 [Linux socket 验证](references/socket-validation.md)。最终分别报告执行、证据完整性、性能有效性和清理状态，不把模拟结果当实机性能。

从第一条现场/CLI命令开始保存命令、原始stdout/stderr和实际退出码。每次调用使用唯一文件名（序号+阶段+attempt），失败调用的输出不得被后续同名文件覆盖。PowerShell Start-Transcript可能漏记原生命令stdout，不能只凭transcript存在宣称响应完整；把CLI JSON直接保存为独立文件并核对可解析性。阶段身份、游标、技能加载与下一CLI调用同步写检查点。停止runtime前归集日志；新CLI支持停止后的read-result只读回收，但不能把未回收完整的日志标为完整。

## 输入契约
入口只有两种：机器派单使用 top_input，见 [派单契约](references/top-input.md)。已填示例是起点，未覆盖的场景以 top-input 的场景矩阵为准，不再提供占位符骨架；示例在 references/examples，文件名即 `<拓扑>-<容器模式>-<动作>-<评测模式>`（`eval-only` 例外）：`colocated-existing-deploy-eval-perf`（借用已有容器 + 容器内测试端 + 性能）、`colocated-existing-deploy-eval-acc`（借用已有容器 + 容器内测试端 + 精度）、`colocated-existing-deploy-only`（借用已有容器，只部署保留）、`pd-dual-host-existing-deploy-only`（双机 PD，只部署保留）、`eval-only-external-acc`（外部已有服务精度评测，测试端在容器内）。`release_only` 不是独立派单场景，只用于原部署任务的续接释放，见派单契约。按场景选文件，不跨场景手改；自然语言派单由当前 Agent 归一化为同等内容，并一次性确认授权字段。control.json 是阶段产物，不作为独立入口。先校验必填字段，缺失时一次性列出全部缺口向用户澄清。`nodes` 段逐节点投影为 bindings，`control.json` 只取 `task.control_node` 那一项，不重新推导授权；`task.id` 与各节点 `root` 末段、tasks 键、交付目录名保持一致。top_input 不写并行度与连接：`tensor-parallel-size` 由该实例 `devices` 数量派生，PD 的 `kv_transfer` 与 `routing` 由实例角色派生。事实类字段非空即视为派单方已确认，只核验存在性与冲突，不重新选值；为空时才现场发现。示例只示范结构，不得照抄其中的 host、devices、路径与端口。

## 阶段边界驱动的技能加载

| 边界事件 | 读取 | 用途 |
| --- | --- | --- |
| top_input 校验通过，plan 完成冻结与 readiness | ascend-llm-deployment/SKILL.md | 调用 deploy；仅部署时到此为止 |
| 核验 deployment.ready，且用户要求评测 | ascend-llm-eval/SKILL.md | 调用 eval；外部服务不取得部署/释放权限 |
| 核验 evaluation.completed，且 `task.on_idle=release_owned` | ascend-llm-release/SKILL.md | 调用 release |

不要在入口一次性预加载deployment/eval/release。plan完成冻结与readiness后实际读取deployment/SKILL.md再调用deploy；收到并核验deployment.ready之后，若用户要求评测，再实际读取eval/SKILL.md并调用eval；收到并核验evaluation.completed之后，按授权实际读取release/SKILL.md并调用release。仅部署成功保留服务，不读取eval启动负载或自动释放。外部评测不取得部署/服务释放权限。
即使某技能之前因其他原因读过，阶段交接时仍必须重新读取目标SKILL.md并记录当时的边界事件、游标、技能路径/摘要和随后的CLI请求，不能把预加载或事后写的JSON摘要当作实际加载动作。边界错误需要收尾时，实际读取release技能后按授权处理，不能假定之前读过即可。检查点保留真实工具调用证据。

## 职责边界

负责：把 top_input 归一化为 plan 输入、发现现场并留证、按阶段调用 CLI、交付证据与结论。

不负责：扩大授权（变更 `devices`、`allowed_root`、`host`）、替用户改 case 参数或数据集、为观测更多数据放大负载、修改 `.agents/` 下的技能与 CLI 源码、读取历史任务目录/归档/回收站补全输入、把部分证据表述为完整成功。
