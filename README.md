# TJU-FYP-reviewer

天津大学本科毕业设计（论文）审查工作台，用于组织本地项目事实、论文审查技能和子 agent 提示词。

## 当前状态

- 当前可用的子 agent：`reviewer`
- `planner`、`researcher`、`executor`、`synthesizer` 等其他子 agent 暂为占位，后续待添加完整能力。

## 目录

- `agents/`：通用子 agent 角色说明。
- `skills/`：论文审查、研究审查、结果核对等可复用本地技能。
- `app/`：本地 orchestrator 和简单 Web UI。
- `projects/tju-fyp-reviewer/PROJECT.md`：当前项目稳定事实来源。
- `projects/tju-fyp-reviewer/MEMORY.md`：当前审查阶段、已知问题和偏好记录。
- `projects/tju-fyp-reviewer/claims.md`：论文或报告中的关键 claim 及证据状态。
- `projects/tju-fyp-reviewer/experiments.md`：实验计划、运行命令、结果路径和复核状态。
- `projects/tju-fyp-reviewer/results/`：项目原始结果。
- `projects/tju-fyp-reviewer/paper/`：论文正文或稿件材料。
- `projects/tju-fyp-reviewer/outputs/`：当前项目的 agent 运行产物和 review 报告。
- `projects/tju-fyp-reviewer/outputs/research-review/clean/`：给人阅读的干净审查报告。
- `projects/tju-fyp-reviewer/outputs/research-review/raw/`：原始模型输出、日志混杂报告和排查材料。
- `outputs/run-manifests/`：跨项目运行清单或索引。

默认项目是 `projects/tju-fyp-reviewer/`。如需切换项目，可设置环境变量 `MUTI_AGENT_PROJECT=<project-name>` 或 `MUTI_AGENT_PROJECT_DIR=<absolute-path>`。

## 本地运行

```bash
python3 app/web_app.py
```

也可以直接使用 orchestrator：

```bash
python3 app/orchestrator.py --help
```

## 本地配置

不要把自己的 `.env` 提交到 GitHub。首次使用时复制示例文件：

```bash
cp .env.example .env
```

然后在本地 `.env` 中填入自己的 DeepSeek API 配置：

```bash
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

`.env` 和 `.env.*` 已被 `.gitignore` 忽略，仓库只保留不含密钥的 `.env.example`。

本地项目档案和审查材料默认不提交到 GitHub，包括：

- 根目录 `memory.md` / `MEMORY.md`
- `projects/*/PROJECT.md`
- `projects/*/MEMORY.md`
- `projects/*/claims.md`
- `projects/*/experiments.md`
- `projects/*/paper/**`
- `projects/*/results/**`
- `projects/*/outputs/**`

如果要分享项目结构，请提交模板或 `.gitkeep`，不要提交自己的论文正文、结果文件、审查记录或 API key。

## 自动填写项目档案

把论文或结果文件放入 `projects/tju-fyp-reviewer/paper/`、`projects/tju-fyp-reviewer/results/` 后，可以让 agent 先生成项目档案：

```bash
python3 app/orchestrator.py bootstrap
```

也可以指定额外上下文：

```bash
python3 app/orchestrator.py bootstrap -c projects/tju-fyp-reviewer/paper -c projects/tju-fyp-reviewer/results
```

该命令会更新活动项目下的 `PROJECT.md`、`MEMORY.md`、`claims.md` 和 `experiments.md`。后续 `render`、`ask`、`quality-review` 会自动读取活动项目的 `PROJECT.md` 和 `MEMORY.md`。

`bootstrap` 会先生成确定性的项目清单，包括 LaTeX 主文件候选、章节文件、参考文献文件、正文引用 key、Bib 条目、缺失引用、模板占位痕迹、图表资源和可能的结果文件，再把清单和核心源码摘录交给 agent 填写项目档案。默认会过滤 `.codex/`、`docx导出/`、`outputs/` 等内部材料，避免把 prompt、旧 review 报告或导出日志当成论文证据。

每次运行都会在本次 run 目录下备份旧档案：

```bash
projects/tju-fyp-reviewer/outputs/runs/<timestamp>-planner-bootstrap-project-profile/existing-profile/
```

如果只想检查 prompt 和清单，不写入项目档案：

```bash
python3 app/orchestrator.py bootstrap --dry-run
```

## 论文质量审查

`quality-review` 会先运行确定性 preflight，再启动多分支 reviewer。preflight 会输出结构化 findings，检查主 TeX、章节引用、Bib 条目、缺失引用、图片路径、交叉引用、模板占位、本地路径 / 内部导出痕迹，并默认尝试运行 `latexmk` 编译。

```bash
python3 app/orchestrator.py ask "@reviewer 审查当前论文" \
  --provider quality-review \
  --agent reviewer \
  -c projects/tju-fyp-reviewer/paper/TJUThesis-2026-Graduation-Bachelor \
  -s tju-thesis-reviewer
```

如果只想做源码审查，暂时不运行 `latexmk`：

```bash
python3 app/orchestrator.py ask "@reviewer 审查当前论文" \
  --provider quality-review \
  --agent reviewer \
  -c projects/tju-fyp-reviewer/paper/TJUThesis-2026-Graduation-Bachelor \
  -s tju-thesis-reviewer \
  --no-compile-preflight
```

`quality-review` 的基础上下文会自动包含 `PROJECT.md`、`MEMORY.md`、`claims.md` 和 `experiments.md`。其中 `claims.md` 用来约束论文结论，`experiments.md` 用来约束实验和结果证据。

## 结果清单

第一版结果自动化只生成实验事实清单，不自动判断 claim 是否成立：

```bash
python3 app/orchestrator.py results-inventory
```

该命令默认扫描活动项目的 `results/`，把观察到的结果文件、表格、日志、图片、模型文件和 metric-like 数值写入 `experiments.md` 的自动管理区块。它不会更新 `claims.md`，也不会把指标解释成“效果好”或“显著优于”。

如果只想预览，不写入 `experiments.md`：

```bash
python3 app/orchestrator.py results-inventory --dry-run
```
