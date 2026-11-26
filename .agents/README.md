# Agent Skills

本目录收录 AISBench / 昇腾 LLM 部署与评测的技能与配套 CLI。**使用入口只有一个：[AGENTS.md](AGENTS.md)**；场景、输入清单、交付内容与阶段流水线都在那里，本文件只保留安装与目录约定。

## 安装

1. 安装或核对 CLI：

   ```bash
   deploy-eval-v2 --version
   ```

   未安装时在本目录的 `ascend_llm_cli/` 执行 `python -m pip install .`，或安装提供的 wheel。

2. 安装技能：

   ```bash
   deploy-eval-v2 install-skills --request skills-install.json
   ```

   请求为 `{"source":"<repo>/.agents/skills","destination":"<Agent 技能目录>"}`。

3. 自检：

   ```bash
   deploy-eval-v2 identity
   ```

   检查命令、失败处理与远端依赖见 [installation.md](skills/ascend-llm-router/references/installation.md)。

4. 从 [AGENTS.md](AGENTS.md) 进入任务；执行阶段从 `ascend-llm-router` 技能开始。

## 目录约定

- `skills/<name>/SKILL.md`：触发条件与执行流程；`references/` 放按需读取的契约；`templates/` 放可复制模板。
- 每个 skill 不维护 schema 副本，字段以已安装 CLI 的 `export-contracts` 为准。
- `dispatch-kit/` 是派单方工具，不是技能，不参与技能加载。
- `ascend_llm_cli/` 是受信 CLI 源码与架构说明（[DESIGN](ascend_llm_cli/DESIGN.md)）。
- `.agents` 是本仓库的组织目录，不保证被所有 Agent 自动发现；无论从哪个入口进来，使用说明以 [AGENTS.md](AGENTS.md) 为准。

日志、现场绑定、模型权重和任务产物保存在仓库外，路径规则见 [AGENTS.md](AGENTS.md) §5 与 [contract.md](skills/ascend-llm-router/references/top-input/contract.md)。
