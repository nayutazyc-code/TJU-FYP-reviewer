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
