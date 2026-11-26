# 审阅摘要

`draft-review --request draft.json` 接收 plan、bindings、runtime_sha256 与 destination（JSON 文件路径）。它在目标目录生成 review-summary.json 与 review.json，CLI 响应中 `status=DRAFT`、`evidence_refs=[]`、`approved=false`。审阅本身不授权执行，也不接触宿主。

摘要内容包括任务与意图、全部输入哈希、归属与清理策略、执行设置、部署参数或外部服务与用例、现场绑定、未决项；部署摘要显式列出生效的容器默认值与哪些字段用了默认值。大段细节留在 JSON 文件里，工具响应只返回引用与摘要。

`review.inputs` 除 plan/bindings/runtime 哈希外还必须含 `summary_sha256`。审阅完成后，审批记录必须包含当前哈希、`APPROVED` 状态、无未决项与证据引用；`freeze-inputs` 会把确定性摘要重新生成进冻结包。读取与安装校验会重复该比对，改写 manifest.json 里的摘要无法通过审批。

runtime 还会按 journal 验收与已知终态事件重建 `operations/OPERATION_ID.json`。索引文件缺失不能证明操作未发生；未知事件类型仍可观察，但不能清除活动操作或替换其 service/state。

## 本地请求精确字段

`freeze-inputs` 的 request 为 plan、bindings、review（三者是 JSON 文件路径）、destination（新的冻结目录）、runtime_sha256（64 位摘要），不得额外增加字段。`verify-inputs` 为 directory、sha256、runtime_sha256；`draft-review` 字段见上文。请求字段正确仍可能因计划、绑定或审阅内容不满足 schema 或语义约束而失败，须依据 `error.message` 定位，不要把 `INVALID_INPUT` 一律解释成请求字段错误。

## 检查元数据

模型实例的 `checks` 必须有 health、prefix_cache_reset、metrics 三个 HTTP 路径；proxy 只需 health。这些只是检查/指标/缓存接口的路径元数据，不授予清缓存权限；本轮 `reset_cache=false` 时不得调用缓存清理接口。`freeze-inputs` 会校验完整模型 launch 与这些检查元数据。实际路径应从所选框架的公开接口确认，vLLM 通常为 `/health`、`/reset_prefix_cache`、`/metrics`。
