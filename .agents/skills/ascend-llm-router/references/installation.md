# 安装与前置检查

所有阶段都通过 `deploy-eval-v2` 执行，技能不内置替代实现。进入 plan 前先完成本节；未安装时**先安装再调用**，不要把“未安装”当作终止条件。

## 1. 检查是否已安装

```bash
deploy-eval-v2 --version
python -c "import jsonschema; print(jsonschema.__version__)"
```

- 能输出版本且退出码 0：CLI 已就绪，继续核对控制端 Python >=3.11。
- 命令不存在、版本不可解析或 `jsonschema` 导入失败：进入第 2 节安装。

## 2. 安装 CLI

优先使用随包提供的 wheel；没有 wheel 时从与 `skills/` 平级的 `ascend_llm_cli` 源码安装。

```bash
# 源码安装（在 ascend_llm_cli 目录执行；Python 需 >=3.11）
python3 -m pip install --no-build-isolation .

# 或安装 wheel
python3 -m pip install dist/deploy_eval_v2-<version>-py3-none-any.whl
```

Windows 用 `py -3 -m pip install --no-build-isolation .`；Linux 用 `python3 -m pip install --no-build-isolation .`。`python3` 只要 >=3.11 即可，3.12 不需要额外的 `python3.11`。`--no-build-isolation` 用于离线环境：默认隔离构建会尝试下载 `setuptools` 等构建依赖而失败；若目标机可联网，也可不加。装完必须复验第 1 节的两条命令。

源码安装会在 `ascend_llm_cli/` 下生成 `build/` 与 `src/*.egg-info/`，这是构建产物，不是对技能或源码的修改；这些路径已列入 `.gitignore`，需要保持目录整洁时可删除。

安装失败（无 Python >=3.11、无 pip/权限、找不到源码或 wheel、依赖不可达）时，一次性报告缺失项并停止；不得改用其他 CLI、脚本或手工 `docker` 代替。

## 3. 安装五个技能（一次性）

若当前 Agent 的技能目录里还没有这五个技能，由派单方或操作者安装，不要在任务执行中覆盖已有技能：

```bash
deploy-eval-v2 install-skills --request skills-install.json
```

请求为 `{"source":"<repo>/.agents/skills","destination":"<Agent 技能目录>"}`。安装器要求五个技能齐全；目标已存在且内容不同返回 `REQUEST_CONFLICT`，不覆盖个人草案。安装后从 `ascend-llm-router` 进入任务。

## 4. 远端执行端

- 模型节点与测试机需要 Python >=3.11 且可导入 `jsonschema`；`package-runtime` 只发布本包代码，不自动安装第三方依赖。
- 不要把 Windows 依赖 wheel 上传到 Linux；按目标系统与架构准备依赖。
- AISBench（**tag ≥ `v3.1-20260820-master`**；各 `v3.1-2026xxxx` tag 的版本号都是 `3.1.0`，旧 tag 的稳态统计口径与本 CLI 校验不一致，3.0.x 还缺 `PrefixLen`）、数据集、tokenizer 与模型权重由 plan 绑定的现场提供，本流程不安装。
- 容器创建/复用按 top_input 的 owned/existing 模式执行；本节不创建容器，也不改变归属。

## 5. 节点与容器的 machine identity（existing/self 模式）

check-ready 与 start-task 用同一条身份链确定“本机是谁”，不需要预置 `/etc/machine-id`：

1. `/etc/machine-id` 可读且为 32 位小写十六进制 → `source=machine-id`；
2. 文件缺失、为空或非法 → 取 `sha256("deploy-eval-v2:v1:" + hostname)` 的前 32 位十六进制 → `source=hostname-derived`。

在目标节点（借用容器内则在该容器内）直接执行：

```bash
deploy-eval-v2 identity
# {"machine_id":"...","source":"machine-id"} 或 {"machine_id":"...","source":"hostname-derived"}
```

- 把 `machine_id` 写入 bindings 的 `nodes.<n>.machine_id` 与同容器 tester 的 `machine_id`；两处必须相同。
- 每次都在实际执行位置取证：容器内取容器自己的身份，禁止复制宿主或其他容器的值。
- 容器重建后 hostname 可能变化，重新执行 `identity` 并更新 bindings。
- 派生值只区分主机（hostname）；同一 hostname 下的多个容器会得到相同派生值，区分容器以 `container.id` 为准。
- `/etc/machine-id` 与 hostname 都不可用时命令返回 `INVALID_INPUT: machine identity unavailable`，按现场问题停止回报。

