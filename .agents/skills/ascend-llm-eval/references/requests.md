# 阶段请求

评测任务的 `service_id` 来自部署产出的 `service.json`；外部服务来自 `prepare-inputs` 归一化后的 service handle。

## eval

```json
{
  "task_ref": "<eval_task_id>",
  "service_id": "svc-<deployment_task_id>",
  "case_id": "<case_id>",
  "request_id": "eval-001",
  "expected_version": 1
}
```

- 不要带 `plan_ref`/`plan_sha256`：评测请求只接受任务身份、服务身份、case 身份、幂等键与版本。多带字段会得到 `INVALID_INPUT`。
- `expected_version` 取 `start-task` 后的当前版本（首次评测通常为 1）；每个 operation 结束后重新 `get-status` 再提交下一 case。
- 同一 `case_id` 只能对应冻结的一个 case；acc 与 perf 混跑时分别提交。

## cancel（operation 范围）

```json
{
  "task_ref": "<eval_task_id>",
  "scope": "operation",
  "operation_id": "<RUNNING 响应的 operation_id>",
  "request_id": "cancel-001",
  "expected_version": 2
}
```

取消只终结该次 operation；`operation.cancelled` 不代表客户端进程已清理，必须等到 `cleanup_status=VERIFIED`。清理未知时进入 RECOVERY_REQUIRED，保留现场并按原身份查询。

## cancel（task 范围，仅 external_eval 会话收尾）

```json
{
  "task_ref": "<eval_task_id>",
  "scope": "task",
  "request_id": "cancel-task-001",
  "expected_version": 3
}
```

仅用于终结评测会话，不会停止被测服务；不得用于替代 release。

## 等待与取结果

```text
deploy-eval-v2 get-status --task-ref <eval_task_id>
deploy-eval-v2 wait-event --task-ref <eval_task_id> --after-seq <next_cursor> --wait-ms 30000
deploy-eval-v2 read-result --task-ref <eval_task_id> --artifact-ref <ref> --offset <n>
```

`evaluation.completed` 只表示阶段结束；有效性按 `evaluation_type` 分别读 `performance_validity` 或 `accuracy_validity`，请求成功不得写成指标有效。
