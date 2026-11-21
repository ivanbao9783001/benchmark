# Remote tester

## 执行路径判定

`tester.machine_id` 等于本机 `/etc/machine-id` 且服务节点为 `container.self=true` 时，tester 在本地执行：直接调用绑定的 AISBench Python，不经 SSH，也不使用 `ContainerTester`。`ContainerTester` 通过 `docker inspect/exec/cp` 工作，只用于非 self 的 existing 容器。无法判定时先核对 machine_id 并停止，不要假设走远端。

Remote testers use detached Python workers with durable per-operation acceptance and process receipts. Frozen machine, root, runtime/input digests and token must match. Repeated requests return a completed response or UNKNOWN_OUTCOME, never duplicate load. Cancellation writes an operation tombstone; the worker observes it, cancels its owned subprocesses, and records terminal cleanup evidence. Missing terminal evidence keeps cleanup UNVERIFIED and claims retained. Runtime and tester need Linux Python with jsonschema; tester additionally needs the bound AISBench environment. No system service manager is used.
