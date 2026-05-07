# TJU-FYP-reviewer

天津大学本科毕业设计（论文）审查工作台，用于组织本地项目事实、论文审查技能和子 agent 提示词。

## 当前状态

- 当前可用的子 agent：`reviewer`
- `planner`、`researcher`、`executor`、`synthesizer` 等其他子 agent 暂为占位，后续待添加完整能力。

## 目录

- `agents/`：子 agent 角色说明。
- `skills/`：论文审查、研究审查、结果核对等本地技能。
- `app/`：本地 orchestrator 和简单 Web UI。
- `PROJECT.md`：项目稳定事实来源。
- `MEMORY.md`：当前审查阶段、已知问题和偏好记录。

## 本地运行

```bash
python3 app/web_app.py
```

也可以直接使用 orchestrator：

```bash
python3 app/orchestrator.py --help
```
