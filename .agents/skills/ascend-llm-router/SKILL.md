---
name: ascend-llm-router
description: 昇腾模型部署、性能测试及资源释放的任务入口；识别意图，选择 plan、deployment、eval、release，并根据阶段边界继续任务。
---

# Ascend LLM Router

依赖 deploy-eval-v2 CLI 0.1、协议 1.0；具体补丁版本以 `deploy-eval-v2 --version` 的实际输出为准，不在文档中固定。进入任务前先按 [安装与前置](references/installation.md) 检查 `--version`；未安装时先安装并复验，安装失败才停止报告。五个技能平级安装，不切换到其他实现。

| 用户意图 | 阶段 |
| --- | --- |
| 部署并测试 | ascend-llm-plan → ascend-llm-deployment → ascend-llm-eval → ascend-llm-release |
| 仅部署 | ascend-llm-plan → ascend-llm-deployment，按授权保留 |
| 测试已有服务 | ascend-llm-plan → ascend-llm-eval，不取得外部服务管理权限 |
| 分析已有结果 | ascend-llm-eval |
| 释放已有任务 | ascend-llm-release |

由当前 Agent 加载下一技能，不递归调用技能或自行启动协调 Agent。已有授权持续有效。阶段交接保留 task_ref、request_id、operation_id、next_cursor、输入摘要、service/case 身份及证据引用。

RUNNING 按游标等待；WAIT_TIMEOUT 不代表失败。结果未知时恢复查询，不更换请求身份盲重跑。具体参数、预检、保留与清理规则由对应阶段技能负责，本文件不重复。

共享命令格式和返回语义见 [CLI 契约](references/cli-contract.md)。阶段请求模板由各阶段技能的 references/requests.md 提供，plan 参数模板见 ascend-llm-plan 的 references/templates.md。仅在用户要求无模型验收时读取 [Linux socket 验证](references/socket-validation.md)。最终分别报告执行、证据完整性、性能有效性和清理状态，不把模拟结果当实机性能。

从第一条现场/CLI命令开始保存命令、原始stdout/stderr和实际退出码。每次调用使用唯一文件名（序号+阶段+attempt），失败调用的输出不得被后续同名文件覆盖。PowerShell Start-Transcript可能漏记原生命令stdout，不能只凭transcript存在宣称响应完整；把CLI JSON直接保存为独立文件并核对可解析性。阶段身份、游标、技能加载与下一CLI调用同步写检查点。停止runtime前归集日志；新CLI支持停止后的read-result只读回收，但不能把未回收完整的日志标为完整。

## 输入契约
入口只有两种：机器派单直接提交 top_input；自然语言派单由当前 Agent 归一化为同等内容的 top_input，并一次性确认授权字段。两者共用 [派单契约](references/top-input.md)。

### 派单方视角（上层主 Agent）

当用户要求“把任务交给 subagent / 派单 / 起一个执行 agent”时，你是派单方，不是执行方。先向用户收齐四类信息，缺一项就停：

1. **环境结论**：`existing`（每节点容器 id、image、`container_work_root`、`network`）、`owned`（明确创建授权 + `deployment.image`）或 `self`（existing 且 CLI 在容器内执行）。
2. **任务形态**：`action`、`topology`、`eval_mode`、`on_idle`。
3. **现场事实**：`host`、`allowed_root`、`guard_root`、`python`、`model_paths`、`devices`、`network`/`ports`、`tester`（含 `machine_id` 与 `datasets`）；PD 另加 `kv_transfer.connector` 与 proxy 的 `paths.proxy_program`。
4. **授权与护栏**：`preempt_other_processes`、`create_work_root`、`control_node`、`deliverables.local_dir`、`constraints`。

取值要求见 [dispatch-kit/README.md](../../dispatch-kit/README.md)，可复制的 prompt 见 [prompt.md](../../dispatch-kit/prompt.md)。不要替 subagent 写 plan/bindings，不要把示例值当授权，不要附带历史任务产物。

**先定环境模式（owned / existing / self），再定任务形态（action / topology / eval_mode）。** 三个判定问题、各自的必填差异与停止规则见派单契约 §0；判不出容器归属就停，不自行创建容器，也不得把 owned 硬套成 existing。`scenarios.json` 只是已有示例的索引，不是支持范围的全部；没有示例的路由按派单契约 §2 与字段契约组装，不得因为找不到示例而改判环境模式。

提交前用 `validate-top-input` 核对必填字段，缺失时一次性列出全部缺口向用户澄清，不进入 plan。`control.json` 是阶段产物，不作为独立入口。节点投影、`task.id` 与 `root` 派生、事实值只核验不重选、示例只示范结构等规则，以 [派单契约](references/top-input.md)、[字段契约](references/top-input/contract.md) 与 [派生表](references/top-input/derivation.md) 为准，本文件不重述。

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
