# 阶段请求

请求体先落盘为唯一文件，再用 `--request <file>` 执行；`<...>` 为占位符，必须替换为冻结产物或上一响应的真实值。

## check-ready / publish-task

两者字段相同：

```json
{
  "task_ref": "<task_id>",
  "runtime_archive": "<本地>/work/runtime.zip",
  "input_directory": "<本地>/work/frozen",
  "runtime_sha256": "<package-runtime 返回值>",
  "bundle_sha256": "<freeze-inputs 返回值>"
}
```

- `runtime_sha256` 与 `bundle_sha256` 必须来自本任务本轮的冻结产物，不得跨任务复用。
- 路径指向本地冻结目录；上传由 CLI 负责，不要手工打包或改写归档。

## start-task

```json
{
  "task_ref": "<task_id>",
  "runtime_sha256": "<package-runtime 返回值>",
  "bundle_sha256": "<freeze-inputs 返回值>",
  "nonce": "<check-ready 或 publish-task 响应中的 nonce>"
}
```

`nonce` 只能取自前序响应；不能自造、不能复用旧任务的值。

## deploy

```json
{
  "task_ref": "<task_id>",
  "plan_ref": "<bundle_sha256>",
  "request_id": "deploy-001",
  "expected_version": 0
}
```

- 首次执行 `expected_version=0`；恢复时重读 `get-status`，版本冲突不重放旧值。
- `request_id` 在重试时保持不变，保证幂等。

## get-status / wait-event

```text
deploy-eval-v2 get-status --task-ref <task_id>
deploy-eval-v2 wait-event --task-ref <task_id> --after-seq <next_cursor> --wait-ms 30000
```

`--wait-ms` 上限 30000；返回 WAIT_TIMEOUT 不等于失败，按游标继续等待。

## stop-runtime

该子命令需要 `--config` 与 `--request`，字段随 launcher 配置变化，见 [Python runtime](launcher.md)；使用原启动 nonce，不猜 PID、不手工 kill。
