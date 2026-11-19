# Plan and bindings contract

The accompanying schemas define deploy-plan, deploy-bindings, external-eval-plan and eval-bindings. Review and freezing perform schema and semantic validation, compile topology and scripts, and bind all input and runtime digests. These local checks do not prove host readiness.

Deploy plans for either container mode (owned_container or existing_container) may contain evaluation {cases, test_binding}; deploy bindings then include testers. Each tester binds host, machine_id, work_root, python, aisbench, model_path and datasets. Native synthetic datasets use type=aisbench_synthetic. Explicit load values are preserved; defaults must be resolved before review.

Optional tester.environment is a map of explicit environment variable strings applied to preflight, AISBench and postflight subprocesses. No shell expansion occurs. Freeze actual absolute paths, not $VARIABLE expressions. Discover required CANN environment from the selected installed toolkit using read-only checks; validate imports under those values before deployment. Do not install or upgrade a user's environment to resolve missing libraries. Record chosen toolkit, environment values and import evidence. Never infer that an executable's presence means its imports work. Environment values must not contain secrets, since frozen inputs and evidence retain them.

Production preflight checks interpreter and console entrypoint ownership, tokenizer assets, config imports and dataset capability. Explicit authorized cache reset requires frozen per-instance targets; never infer these from the service URL. Execution and evidence/performance validity remain separate outcomes.

## 借用已有容器（existing_container）

当任务使用运行中的已有容器而不是新建容器时，plan 与 bindings 的形态同时改变：

- plan：`deployment.runtime` 只写 `{"mode":"existing_container"}`，不写 `image` 与 `container`；schema 允许 `deployment.image` 缺省，因此不要填镜像摘要。
- bindings：每个参与部署的节点必须有 `container={"id":"64位容器ID","image":"sha256:镜像摘要"}` 与 `container_work_root`；不写 `mounts`（编译器在 existing 模式下不使用宿主挂载）。`work_dir`、`model_paths`、`paths`、`network`、`ports` 仍按容器内可见性填写；existing 模式下 `network` 不得为空，HCCL 与监听地址依赖它。
- 编译结果：脚本与日志位于 `container_work_root/<task_id>`；宿主 `work_dir/<task_id>` 只保存状态与可回传产物，不挂载进容器。
- 冻结与部署前：`docker inspect` 核对完整 ID、镜像与 `Running=true`；容器绝不被创建、启动、停止或删除，release 只结束带任务标记的进程组。
- 所有节点模式必须一致，owned 与 existing 不得混用；existing 模式下出现 `deployment.runtime.image`/`container`，或 owned 模式下出现 bindings 的 `container`，都会被拒绝。

## 容器内测试工具
AISBench 可位于宿主或已有运行容器。宿主未找到工具时，继续只读检查已有容器的工具、解释器、依赖、tokenizer和网络；不要要求用户重复提供可自行发现的绑定。
容器 tester 在原有 host/work_root/machine_id 字段之外设置 container={"id":"完整64位容器ID","image":"sha256:完整镜像摘要"}；python、aisbench、model_path、environment、datasets 路径均属于容器内环境。host_python（默认 /usr/bin/python3）和 control.json 的 python 属于宿主，用于框架，不要求容器安装框架依赖。work_root 是已授权宿主工作根，执行器在容器同路径下建立独立操作目录，通过公开CLI发布工具代码与收回原生产物，不要求已有容器挂载该目录。若容器没有把 work_root 暴露在同一路径（例如宿主 /home/u 挂载为容器 /workspace），再加 container_work_root 声明容器内操作根；此时只有 work_root/control.json/host_python 用宿主路径，其余 tester 路径仍用容器内路径。container_work_root 只能与 container 同时出现，必须是容器内绝对路径且不含 `:`。它可以是容器内独立目录（如 /tmp/de-eval-tester，执行器在容器内建目录、跑完回传），也可以恰好与宿主 work_root 是同一 bind mount；后者两侧看到同一物理目录，不要假定容器目录必然不存在。
冻结前 docker inspect 核对完整ID、镜像、运行状态；只读检查容器内Python、AISBench版本及依赖、权重可见性、被测endpoint从容器可达。工具存在不代表依赖完整。使用公开CLI执行，不手写docker exec负载封装。容器仅提供测试环境，不授予停止、重启、删除容器或被测服务的权限。执行器只停止本任务工具进程组；证据回收失败或清理未知必须报告，不能标记完整。

## 服务健康路径
模型和代理健康路由必须分别从本轮参考或实际接口确认，不统一套用/health。deploy实例checks.health绑定各自路径；service.json携带入口health_path；部署后的eval沿用该路径。独立prepare-inputs(stage=eval)允许health_path字段，与endpoint同源；默认/health只适用于已确认提供该路由的服务。用户提供service对象时沿用其health_path。仅设置健康路径不授予服务管理权限。AISBench预检也从容器实际测试环境访问同一路由。
