# Project Agent Instructions

## Local Skills

- Use `skills/tju-thesis-reviewer/SKILL.md` when the task involves reviewing, revising, polishing, or pre-submission checking a Tianjin University undergraduate thesis.
- When using that skill, also read its referenced checklist files under `skills/tju-thesis-reviewer/references/` as needed.
- Use `skills/research-review/SKILL.md` for one-shot deep critical review of research ideas, papers, experimental results, or project narratives.
- Use `skills/auto-review-loop/SKILL.md` when the user asks for iterative autonomous review and improvement of a research project or draft.
- Use `skills/paper-claim-audit/SKILL.md` when the task is to verify that paper numbers, comparisons, and scope claims match raw result files.
- Use `skills/result-to-claim/SKILL.md` when experimental results need to be converted into supported, partially supported, or invalidated paper claims.
- Shared ARIS review protocols live under `skills/shared-references/`; read only the referenced files needed by the active skill.

## Review Defaults

- Prioritize submission-blocking format, citation, numbering, and compile issues before prose polishing.
- Keep thesis edits minimal and do not change conclusions, data scope, or chapter structure unless the user asks.
- Report findings by severity, then list changed or recommended files, pending risks, and compile status.
- Write all future paper/research review reports under `projects/<project-name>/outputs/research-review/clean/`, and raw model/log-heavy outputs under `projects/<project-name>/outputs/research-review/raw/`, unless the user gives a different output path.
- Use descriptive filenames that identify the reviewed paper, such as `<arxiv-id>-review.md` or `<paper-slug>-review.md`.
