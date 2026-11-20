# 阶段请求

释放前先 `get-status` 取当前版本、活动 operation 与归属记录；`expected_version` 必须来自该响应。`service_id` 取部署产出的 `service.json` 原值，不按命名规则拼接。

## release

```json
{
  "task_ref": "<deployment_task_id>",
  "service_id": "<service.json 中的 service_id>",
  "request_id": "release-001",
  "expected_version": 2
}
```

- 只释放本任务明确拥有的资源；外部服务不进入默认释放范围。
- 存在活动 operation 时先按授权 cancel，等到 `cleanup_status=VERIFIED` 再 release。
- `request_id` 在恢复时保持不变。

## cancel（活动测试或未完成 operation）

```json
{
  "task_ref": "<task_id>",
  "scope": "operation",
  "operation_id": "<当前活动 operation_id>",
  "request_id": "cancel-001",
  "expected_version": 2
}
```

`operation.cancelled` 不等于资源已释放；`release.unverified`、通信未知或部分失败时保留现场，不删锁、不重放部署试探。

## 释放后核对

```text
deploy-eval-v2 --config <root>/control.json get-status --task-ref <deployment_task_id>
```

确认无活动 operation、逐资源结果与 `resource_results_ref` 可读后，再停止 runtime。任务目录、冻结包与结果默认保留，不随容器释放删除。
