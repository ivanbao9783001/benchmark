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
- AISBench、数据集、tokenizer 与模型权重由 plan 绑定的现场提供，本流程不安装。
- 容器创建/复用按 top_input 的 owned/existing 模式执行；本节不创建容器，也不改变归属。
