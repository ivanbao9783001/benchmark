# Remote tester

## 执行路径判定

`tester.machine_id` 等于本机身份（`deploy-eval-v2 identity` 的输出：优先 `/etc/machine-id`，为空时用 hostname 派生）且服务节点为 `container.self=true` 时，tester 在本地执行：直接调用绑定的 AISBench Python，不经 SSH，也不使用 `ContainerTester`。`eval_only`/`external_eval` 没有部署节点，但控制端与 tester 同容器时同样本地执行：`tester.machine_id` 与本机一致、控制端在容器内（`/.dockerenv` 存在）且 tester 声明了 `container`，该 container 即当前容器，不要求 `image`，也不执行任何 `docker` 命令。`ContainerTester` 通过 `docker inspect/exec/cp` 工作，只用于控制端不在该容器内的情形（如控制端在宿主机、测试在借用容器内）。无法判定时先核对 machine_id 并停止，不要假设走远端。

Remote testers use detached Python workers with durable per-operation acceptance and process receipts. Frozen machine, root, runtime/input digests and token must match. Repeated requests return a completed response or UNKNOWN_OUTCOME, never duplicate load. Cancellation writes an operation tombstone; the worker observes it, cancels its owned subprocesses, and records terminal cleanup evidence. Missing terminal evidence keeps cleanup UNVERIFIED and claims retained. Runtime and tester need Linux Python with jsonschema; tester additionally needs the bound AISBench environment. No system service manager is used.
