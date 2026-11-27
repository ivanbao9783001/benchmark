# Plan and bindings contract

三种根必须区分：交付根 `task_root = deliverables.local_dir/task_id`（控制机，放 plan/bindings/compiled 的审阅与冻结产物）；节点运行根 `node_root = work_dir/task_id`（每个节点，放 runtime/inputs/状态/日志/原始 results）；existing 的容器运行根 `container_root = container_work_root/task_id`（脚本、日志与原始 results 在容器内）。plan/bindings/compiled 只记录 `work_dir`、`container_work_root` 与 `task_id`，不记录 `task_root`；编译器用前两者派生运行根。

随附 schema 定义 deploy-plan、deploy-bindings、external-eval-plan 与 eval-bindings。审阅与冻结执行 schema 与语义校验，编译拓扑与脚本，并绑定全部输入与 runtime 摘要；这些本地检查不证明宿主就绪。

两种容器模式的 deploy plan（`owned_container` 或 `existing_container`）都可以内嵌 `evaluation{cases, test_binding}`，此时 deploy bindings 包含 testers。每个 tester 绑定 host、machine_id、work_root、python、aisbench、model_path 与 datasets；原生合成数据集用 `type=aisbench_synthetic`。显式负载值原样保留，默认值必须在审阅前解析成具体值。

可选的 `tester.environment` 是显式环境变量字符串映射，作用于 preflight、AISBench 与 postflight 子进程，不做 shell 展开；冻结真实绝对路径，不写 `$VARIABLE`。所需 CANN 环境用只读检查从所选已安装工具链中发现，并在部署前用这些值验证导入。不得为补齐缺失库而安装或升级用户环境。记录所选工具链、环境值与导入证据；绝不因可执行文件存在就推断导入可用。环境值不得包含密钥，因为冻结输入与证据会保留它们。

生产预检检查解释器与控制台入口归属、tokenizer 资产、配置导入与数据集能力。显式获授权的缓存重置需要冻结的逐实例目标，绝不从服务 URL 推断。执行结果与证据/性能有效性是彼此独立的结论。

## bindings 的端口形状

`bindings.nodes.<node>.deployments` 以 `deployment.id` 为键，端口再按实例 id 分层：

```json
"deployments": {
  "<deployment-id>": {
    "network": {"ip": "<节点 IP>", "interface": "<网卡>"},
    "ports": {"<instance-id>": {"http": 8020, "hccl": 64023}}
  }
}
```

三种位置的端口形状不同，不要互相照搬：top_input 的 `deployment.instances[].ports` 是实例内平铺（`{"http": 8020}`）；plan 同名位置是跨度宽度；只有 bindings 需要实例 id 这一层。`freeze-inputs` 报 `deployment bindings schema at <JSON 路径>` 时，按路径核对这一层。

## 借用已有容器（existing_container）

当任务使用运行中的已有容器而不是新建容器时，plan 与 bindings 的形态同时改变：

- plan：`deployment.runtime` 只写 `{"mode":"existing_container"}`，不写 `image` 与 `container`；schema 允许 `deployment.image` 缺省，因此不要填镜像摘要。
- bindings：每个参与部署的节点必须有 `container={"id":"64位容器ID","image":"sha256:镜像摘要"}` 与 `container_work_root`；`self` 节点可省略 `image`（容器内无 docker，无法核验），非 self 节点仍须给出摘要。不写 `mounts`（编译器在 existing 模式下不使用宿主挂载）。`work_dir`、`model_paths`、`paths`、`network`、`ports` 仍按容器内可见性填写；existing 模式下 `network` 不得为空，HCCL 与监听地址依赖它。
- 编译结果：脚本与日志位于 `container_work_root/<task_id>`；宿主 `work_dir/<task_id>` 只保存状态与可回传产物，不挂载进容器。
- 冻结与部署前：`docker inspect` 核对完整 ID、镜像与 `Running=true`；容器绝不被创建、启动、停止或删除，release 只结束带任务标记的进程组。
- 所有节点模式必须一致，owned 与 existing 不得混用；existing 模式下出现 `deployment.runtime.image`/`container`，或 owned 模式下出现 bindings 的 `container`，都会被拒绝。

## 容器内测试工具
AISBench 可位于宿主或已有运行容器。宿主未找到工具时，继续只读检查已有容器的工具、解释器、依赖、tokenizer和网络；不要要求用户重复提供可自行发现的绑定。AISBench 必须来自 **tag `v3.1-20260820-master` 或更新**（发行名 `ais_bench_benchmark`）：`v3.1-2026xxxx` 各 tag 的 `__version__` 都是 `3.1.0`，只看版本号不够；`v3.1-20260813-master` 及更早的稳态统计仍是旧 `WAVE_OFFSET` / 末请求结束口径（#464 之前），与本 CLI 的稳态校验不一致，会得到 `SUMMARY_TIMING_MISMATCH`；3.0.x 的 `SyntheticDataset.load` 还不含 `PrefixLen`。不能因为低版本更易获取就顶替；核验命令见 [benchmark-preflight.md](../../ascend-llm-eval/references/benchmark-preflight.md)。
容器 tester 与部署节点同规则，**existing 全容器内闭环**：设置 `container={"id":"完整64位容器ID","image":"sha256:完整镜像摘要"}`（`self` 节点可省略 `image`，非 self 节点仍须给出摘要）与 `container_work_root`；`work_root`、`python`、`aisbench`、`model_path`、`environment`、`datasets`、`machine_id` 全部按容器内取值；worker 解释器取 `python`（容器内值）。`work_root` 是容器内工作根，执行器在容器内建立任务目录，不要求容器挂载宿主目录；`container_work_root` 是容器内操作根，允许不存在，由执行器创建，派生时取 `work_root`。`machine_id` 在容器内运行 `deploy-eval-v2 identity` 取证（优先 `/etc/machine-id`，为空时用 hostname 派生）。owned 场景 tester 在宿主：不写 `container` 与 `container_work_root`，`python/aisbench/model_path/datasets/machine_id` 按宿主取值。未声明 tester 时默认控制节点（existing 即控制容器，owned 即控制机）。container_work_root 只能与 container 同时出现，必须是容器内绝对路径且不含 `:`。它可以是容器内独立目录（如 /tmp/de-eval-tester，执行器在容器内建目录、跑完回传）。
冻结前 docker inspect 核对完整ID、镜像、运行状态；只读检查容器内Python、AISBench版本与稳态口径（tag ≥ v3.1-20260820-master）及依赖、权重可见性、被测endpoint从容器可达。工具存在不代表依赖完整。使用公开CLI执行，不手写docker exec负载封装。容器仅提供测试环境，不授予停止、重启、删除容器或被测服务的权限。执行器只停止本任务工具进程组；证据回收失败或清理未知必须报告，不能标记完整。

## 服务健康路径
模型和代理健康路由必须分别从本轮参考或实际接口确认，不统一套用/health。deploy实例checks.health绑定各自路径；service.json携带入口health_path；部署后的eval沿用该路径。独立prepare-inputs(stage=eval)允许health_path字段，与endpoint同源；默认/health只适用于已确认提供该路由的服务。用户提供service对象时沿用其health_path。仅设置健康路径不授予服务管理权限。AISBench预检也从容器实际测试环境访问同一路由。

## 生成 plan/bindings 前的确认清单

1. 运行时模式与 topology 已按现场事实判定；existing 全节点一致，恰好一个 `self`（当前容器）。
2. TP/DP 与 `devices` 卡数匹配；`ASCEND_RT_VISIBLE_DEVICES` 以派单授权为准，并在本次进程覆盖容器基线值。
3. HCCL 与监听端口已由 plan 的 `ports` 派生且未被占用；PD 另确认 proxy 端口。
4. launcher 的 argv/env 与模型实际权重一致；不从示例照抄模型名、路径或端口。
5. `work_dir`、`model_paths`、`paths`、`network` 均为容器内可见值；`self` 节点本地核对。
6. 发布前确认目标未被其它任务占用；结果未知时用同一请求身份恢复查询，不新开目录盲重跑。
7. 冻结前后的 `check-ready` 阻断项已逐条消除；`guard_root` 不存在不是阻塞，但必须位于可写位置。
8. tester 的 `machine_id`、`container`、`container_work_root` 与执行位置一致：existing 在容器内、owned 在宿主；未声明 tester 时默认控制节点；existing 的 tester 必须是控制节点同一容器（控制机场景）。
