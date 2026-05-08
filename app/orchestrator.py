#!/usr/bin/env python3
"""Minimal local multi-agent orchestrator.

This script reads project instructions plus `agents/*.md`, builds an agent prompt,
and either prints it or sends it to a supported local provider.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent if APP_DIR.name == "app" else APP_DIR
PROJECTS_DIR = ROOT / "projects"


def active_project_dir() -> Path:
    project_dir = os.environ.get("MUTI_AGENT_PROJECT_DIR")
    if project_dir:
        return Path(project_dir).expanduser().resolve()

    project_name = os.environ.get("MUTI_AGENT_PROJECT", "tju-fyp-reviewer")
    named_project = PROJECTS_DIR / project_name
    if named_project.exists():
        return named_project

    if PROJECTS_DIR.exists():
        projects = sorted(path for path in PROJECTS_DIR.iterdir() if path.is_dir())
        if len(projects) == 1:
            return projects[0]

    return ROOT


ACTIVE_PROJECT_DIR = active_project_dir()
AGENTS_DIR = ROOT / "agents"
SKILLS_DIR = ROOT / "skills"
OUTPUTS_DIR = ACTIVE_PROJECT_DIR / "outputs" / "runs"
RESEARCH_REVIEW_DIR = ACTIVE_PROJECT_DIR / "outputs" / "research-review"
RESEARCH_REVIEW_RAW_DIR = RESEARCH_REVIEW_DIR / "raw"
RESEARCH_REVIEW_CLEAN_DIR = RESEARCH_REVIEW_DIR / "clean"
PROJECT_PROFILE_FILES = ("PROJECT.md", "MEMORY.md", "claims.md", "experiments.md")
CONTEXT_INCLUDE_SUFFIXES = {
    ".tex",
    ".bib",
    ".cls",
    ".sty",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
}
CONTEXT_EXCLUDE_SUFFIXES = {
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".synctex.gz",
    ".toc",
    ".xdv",
}
CONTEXT_EXCLUDE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "outputs",
    "build",
    "dist",
}
MAX_CONTEXT_FILE_BYTES = 256_000
MAX_CONTEXT_FILES_PER_DIR = 80
QUALITY_INDEX_MAX_FILES = 300
QUALITY_TRIAGE_MAX_EXCERPTS = 12
QUALITY_TRIAGE_EXCERPT_BYTES = 12_000
QUALITY_DEEP_MAX_FILES = 14
QUALITY_DEEP_FILE_BYTES = 80_000
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
GEMINI_DEFAULT_TIMEOUT = 300
QUALITY_MANIFEST_SUFFIXES = {
    ".tex",
    ".bib",
    ".cls",
    ".sty",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
}
QUALITY_CONTENT_SUFFIXES = {
    ".tex",
    ".bib",
    ".cls",
    ".sty",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
}


SKILL_RULES = [
    {
        "name": "tju-thesis-reviewer",
        "keywords": [
            "天津大学",
            "天大",
            "本科论文",
            "毕业论文",
            "毕业设计",
            "送审",
            "答辩",
            "latex",
            "tex",
            "格式",
        ],
    },
    {
        "name": "paper-claim-audit",
        "keywords": [
            "数字核对",
            "claim",
            "claims",
            "证据",
            "结果文件",
            "raw result",
            "metrics",
            "实验结果",
            "paper-claim",
        ],
    },
    {
        "name": "auto-review-loop",
        "keywords": [
            "auto-review",
            "auto review",
            "循环审查",
            "多轮审查",
            "自动改进",
            "review until",
            "until passes",
            "反复审",
        ],
    },
    {
        "name": "research-review",
        "keywords": [
            "论文",
            "paper",
            "research",
            "科研",
            "审稿",
            "审查",
            "review",
            "实验",
            "方法",
            "idea",
            "novelty",
        ],
    },
    {
        "name": "result-to-claim",
        "keywords": [
            "result-to-claim",
            "结果转claim",
            "结果转 claim",
            "支持的结论",
            "supported claim",
            "invalidated",
        ],
    },
]


class OrchestratorError(RuntimeError):
    pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def optional_text(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def project_text(filename: str) -> str:
    project_path = ACTIVE_PROJECT_DIR / filename
    if project_path.exists():
        return read_text(project_path)
    return optional_text(ROOT / filename)


def list_agents() -> list[str]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(p.stem for p in AGENTS_DIR.glob("*.md"))


def list_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))


def load_agent(name: str) -> str:
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(list_agents()) or "none"
        raise OrchestratorError(f"Unknown agent '{name}'. Available agents: {available}")
    return read_text(path)


def load_skill(name: str) -> tuple[str, str]:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        available = ", ".join(list_skills()) or "none"
        raise OrchestratorError(f"Unknown skill '{name}'. Available skills: {available}")
    return name, read_text(path)


def infer_skills(task: str, explicit: list[str], auto: bool) -> list[str]:
    selected: list[str] = []
    for name in explicit:
        if name not in selected:
            selected.append(name)
    if not auto:
        return selected

    lowered = task.lower()
    matched: list[str] = []
    for rule in SKILL_RULES:
        if rule["name"] in selected:
            continue
        if any(keyword.lower() in lowered for keyword in rule["keywords"]):
            matched.append(rule["name"])
    general = "research-review"
    specific_matches = [name for name in matched if name != general]
    if specific_matches and general in matched:
        matched.remove(general)
    for name in matched:
        if name not in selected:
            selected.append(name)
    return selected


def should_include_context_file(path: Path) -> bool:
    name = path.name.lower()
    suffixes = "".join(path.suffixes).lower()
    suffix = path.suffix.lower()
    if suffixes in CONTEXT_EXCLUDE_SUFFIXES or suffix in CONTEXT_EXCLUDE_SUFFIXES:
        return False
    if suffix not in CONTEXT_INCLUDE_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= MAX_CONTEXT_FILE_BYTES
    except OSError:
        return False


def iter_context_dir(path: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in sorted(path.rglob("*")):
        if len(files) >= MAX_CONTEXT_FILES_PER_DIR:
            break
        if any(part in CONTEXT_EXCLUDE_DIRS for part in candidate.parts):
            continue
        if candidate.is_file() and should_include_context_file(candidate):
            files.append(candidate)
    return files


def resolve_context_paths(paths: list[str]) -> list[Path]:
    resolved: list[Path] = []
    loaded: list[tuple[str, str]] = []
    for raw in paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if not path.exists():
            raise OrchestratorError(f"Context file not found: {path}")
        if path.is_dir():
            resolved.extend(iter_context_dir(path))
        elif path.is_file():
            if not should_include_context_file(path):
                raise OrchestratorError(f"Unsupported or too large context file: {path}")
            resolved.append(path)
        else:
            raise OrchestratorError(f"Context path is neither file nor directory: {path}")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in resolved:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def load_context_files(paths: list[str]) -> list[tuple[str, str]]:
    loaded: list[tuple[str, str]] = []
    for path in resolve_context_paths(paths):
        loaded.append((str(path), read_text(path)))
    return loaded


def read_text_limited(path: Path, limit: int) -> str:
    data = path.read_bytes()[:limit]
    text = data.decode("utf-8", errors="replace")
    try:
        truncated = path.stat().st_size > limit
    except OSError:
        truncated = False
    if truncated:
        text += f"\n\n[truncated at {limit} bytes]"
    return text


def summarize_context_paths(paths: list[str]) -> dict[str, object]:
    resolved = resolve_context_paths(paths)
    total_bytes = 0
    files: list[dict[str, object]] = []
    for path in resolved:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        files.append({"path": str(path), "bytes": size})
    return {"count": len(files), "total_bytes": total_bytes, "files": files}


def default_project_context() -> list[str]:
    roots: list[str] = []
    for name in ("paper", "results"):
        path = ACTIVE_PROJECT_DIR / name
        if path.exists() and iter_context_dir(path):
            roots.append(str(path))
    return roots or [str(ACTIVE_PROJECT_DIR)]


def context_roots(paths: list[str]) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not path.exists():
            raise OrchestratorError(f"Context path not found: {path}")
        root = path if path.is_dir() else path.parent
        if root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def should_index_quality_file(path: Path) -> bool:
    suffixes = "".join(path.suffixes).lower()
    suffix = path.suffix.lower()
    if suffixes in CONTEXT_EXCLUDE_SUFFIXES or suffix in CONTEXT_EXCLUDE_SUFFIXES:
        return False
    if any(part in CONTEXT_EXCLUDE_DIRS for part in path.parts):
        return False
    return suffix in QUALITY_MANIFEST_SUFFIXES


def iter_quality_paths(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not path.exists():
            raise OrchestratorError(f"Context path not found: {path}")
        if path.is_file():
            if should_index_quality_file(path):
                files.append(path)
            continue
        if not path.is_dir():
            raise OrchestratorError(f"Context path is neither file nor directory: {path}")
        for candidate in sorted(path.rglob("*")):
            if len(files) >= QUALITY_INDEX_MAX_FILES:
                break
            if candidate.is_file() and should_index_quality_file(candidate):
                files.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def relative_to_any(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def extract_tex_outline(text: str, max_items: int = 36) -> list[str]:
    outline: list[str] = []
    pattern = re.compile(r"\\(part|chapter|section|subsection|subsubsection)\*?\{([^{}]+)\}")
    for match in pattern.finditer(text):
        heading = re.sub(r"\s+", " ", match.group(2)).strip()
        if heading:
            outline.append(f"{match.group(1)}: {heading}")
        if len(outline) >= max_items:
            break
    return outline


def build_quality_index(context_paths: list[str]) -> dict[str, object]:
    roots = context_roots(context_paths)
    files = iter_quality_paths(context_paths)
    entries: list[dict[str, object]] = []
    main_tex: list[str] = []
    for path in files:
        suffix = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entry: dict[str, object] = {
            "path": str(path),
            "relative_path": relative_to_any(path, roots),
            "suffix": suffix,
            "bytes": size,
        }
        if suffix == ".tex":
            sample = read_text_limited(path, min(size or QUALITY_TRIAGE_EXCERPT_BYTES, QUALITY_TRIAGE_EXCERPT_BYTES))
            outline = extract_tex_outline(sample)
            if outline:
                entry["outline"] = outline
            if "\\documentclass" in sample:
                entry["signals"] = ["main-tex-candidate"]
                main_tex.append(str(path))
        elif suffix in {".csv", ".tsv", ".json", ".yaml", ".yml"}:
            entry["signals"] = ["data-or-experiment-file"]
        elif suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
            entry["signals"] = ["asset-listed-only"]
        entries.append(entry)

    total_bytes = sum(int(entry["bytes"]) for entry in entries)
    return {
        "roots": [str(root) for root in roots],
        "count": len(entries),
        "total_bytes": total_bytes,
        "main_tex_candidates": main_tex,
        "files": entries,
        "truncated": len(files) >= QUALITY_INDEX_MAX_FILES,
        "limits": {
            "index_max_files": QUALITY_INDEX_MAX_FILES,
            "triage_max_excerpts": QUALITY_TRIAGE_MAX_EXCERPTS,
            "deep_max_files": QUALITY_DEEP_MAX_FILES,
            "deep_file_bytes": QUALITY_DEEP_FILE_BYTES,
        },
    }


def preferred_quality_excerpts(index: dict[str, object]) -> list[Path]:
    entries = index.get("files") or []
    scored: list[tuple[int, Path]] = []
    for raw in entries:
        entry = raw if isinstance(raw, dict) else {}
        path = Path(str(entry.get("path", "")))
        suffix = str(entry.get("suffix", "")).lower()
        rel = str(entry.get("relative_path", "")).lower()
        score = 0
        if suffix == ".tex":
            score += 30
        if "main-tex-candidate" in entry.get("signals", []):
            score += 80
        if any(token in rel for token in ("main", "thesis", "paper", "chapter", "section", "body", "content")):
            score += 20
        if suffix == ".bib":
            score += 12
        if suffix in {".md", ".txt"}:
            score += 10
        if suffix in {".csv", ".tsv", ".json", ".yaml", ".yml"}:
            score += 8
        if score and path.exists() and suffix in QUALITY_CONTENT_SUFFIXES:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return [path for _, path in scored[:QUALITY_TRIAGE_MAX_EXCERPTS]]


def render_context_blocks(paths: list[Path], byte_limit: int) -> str:
    blocks: list[str] = []
    for path in paths:
        blocks.append(f"## CONTEXT FILE: {path}\n\n{read_text_limited(path, byte_limit).strip()}")
    return "\n\n---\n\n".join(blocks) if blocks else "No context file excerpts were selected."


def build_project_bootstrap_prompt(context_paths: list[str]) -> str:
    context_summary = summarize_context_paths(context_paths)
    excerpts = render_context_blocks(resolve_context_paths(context_paths), 24_000)
    existing = render_blocks(
        [(f"EXISTING {filename}", optional_text(ACTIVE_PROJECT_DIR / filename)) for filename in PROJECT_PROFILE_FILES]
    )
    schema = {filename: f"complete replacement Markdown for {filename}" for filename in PROJECT_PROFILE_FILES}
    blocks = [
        ("GLOBAL PROJECT INSTRUCTIONS", optional_text(ROOT / "AGENTS.md")),
        ("TASK", "Infer and update the active research project profile from the supplied files."),
        ("ACTIVE PROJECT DIRECTORY", str(ACTIVE_PROJECT_DIR)),
        ("CONTEXT SUMMARY", json.dumps(context_summary, ensure_ascii=False, indent=2)),
        ("EXISTING PROJECT PROFILE FILES", existing),
        ("SOURCE FILE EXCERPTS", excerpts),
        (
            "BOOTSTRAP RULES",
            "Return ONLY a JSON object whose keys are exactly PROJECT.md, MEMORY.md, claims.md, and experiments.md. "
            "Each value must be complete Markdown file content. Preserve useful existing sections. Fill only what is supported by source files. "
            "Mark uncertain information as blank, TODO, or 人工确认. Do not invent paper titles, results, claims, commands, data sources, or advisor preferences. "
            "Keep conclusions conservative and evidence-grounded.",
        ),
        ("REQUIRED JSON SHAPE", json.dumps(schema, ensure_ascii=False, indent=2)),
    ]
    return render_blocks(blocks)


def make_run_dir(agent: str, task: str) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", task.strip())[:40].strip("-") or "task"
    base = OUTPUTS_DIR / f"{timestamp}-{agent}-{slug}"
    for index in range(100):
        run_dir = base if index == 0 else OUTPUTS_DIR / f"{timestamp}-{agent}-{slug}-{index + 1}"
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return run_dir
    raise OrchestratorError(f"Could not create a unique run directory for {base}")


def detect_agent_from_message(message: str, fallback: str = "planner") -> str:
    match = re.search(r"@([A-Za-z0-9_-]+)", message)
    return match.group(1) if match else fallback


def build_prompt(agent: str, task: str, context_paths: list[str], skills: list[str]) -> str:
    global_rules = optional_text(ROOT / "AGENTS.md")
    project = project_text("PROJECT.md")
    memory = project_text("MEMORY.md")
    role = load_agent(agent)
    extra_context = load_context_files(context_paths)
    loaded_skills = [load_skill(name) for name in skills]

    blocks = [
        ("GLOBAL PROJECT INSTRUCTIONS", global_rules),
        ("PROJECT FACTS", project),
        ("PROJECT MEMORY", memory),
        (f"AGENT ROLE: {agent}", role),
    ]
    for name, content in loaded_skills:
        blocks.append((f"ACTIVE SKILL: {name}", content))
    for path, content in extra_context:
        blocks.append((f"CONTEXT FILE: {path}", content))
    blocks.append(("USER TASK", task))

    rendered = []
    for title, content in blocks:
        if content.strip():
            rendered.append(f"## {title}\n\n{content.strip()}")
    rendered.append(
        "## EXECUTION INSTRUCTIONS\n\n"
        "Follow the selected agent role. Be concrete, evidence-grounded, and concise. "
        "If you cannot complete the task from the provided context, state exactly what is missing."
    )
    return "\n\n---\n\n".join(rendered) + "\n"


def base_instruction_blocks(agent: str, skills: list[str]) -> list[tuple[str, str]]:
    blocks = [
        ("GLOBAL PROJECT INSTRUCTIONS", optional_text(ROOT / "AGENTS.md")),
        ("PROJECT FACTS", project_text("PROJECT.md")),
        ("PROJECT MEMORY", project_text("MEMORY.md")),
        (f"AGENT ROLE: {agent}", load_agent(agent)),
    ]
    for name in skills:
        skill_name, content = load_skill(name)
        blocks.append((f"ACTIVE SKILL: {skill_name}", content))
    return blocks


def render_blocks(blocks: list[tuple[str, str]]) -> str:
    rendered: list[str] = []
    for title, content in blocks:
        if content.strip():
            rendered.append(f"## {title}\n\n{content.strip()}")
    return "\n\n---\n\n".join(rendered) + "\n"


def build_quality_triage_prompt(agent: str, task: str, skills: list[str], index: dict[str, object]) -> str:
    excerpts = render_context_blocks(preferred_quality_excerpts(index), QUALITY_TRIAGE_EXCERPT_BYTES)
    blocks = base_instruction_blocks(agent, skills)
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX", json.dumps(index, ensure_ascii=False, indent=2)),
            ("SMALL TRIAGE EXCERPTS", excerpts),
            ("USER TASK", task),
            (
                "TRIAGE INSTRUCTIONS",
                "Choose the smallest set of files needed for a high-quality deep review. "
                f"Select at most {QUALITY_DEEP_MAX_FILES} text files. Prefer main .tex files, core chapters, "
                "bibliography, and data/result files when relevant. Do not select binary assets. "
                "Return concise reasoning, then include a JSON object fenced as ```json with this shape: "
                "{\"selected_files\":[\"/absolute/path/or/index/path\"],\"review_focus\":[\"...\"]}.",
            ),
        ]
    )
    return render_blocks(blocks)


def iter_json_objects(text: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, object]] = []
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            objects.append(data)
    return objects


def extract_json_object(
    text: str,
    required_keys: set[str] | None = None,
    prefer_last: bool = False,
) -> dict[str, object] | None:
    required = required_keys or set()
    matches = [data for data in iter_json_objects(text) if required.issubset(data.keys())]
    if not matches:
        return None
    return matches[-1] if prefer_last else matches[0]


def clean_codex_cli_output(output: str) -> str:
    text = output.strip()
    parts = re.split(r"\ncodex\n", text)
    if len(parts) > 1:
        text = parts[-1].strip()
    token_match = re.search(r"\ntokens used\n[\d,]+\s*(?:\n|$)", text)
    if token_match:
        text = text[: token_match.start()].strip()
    return text or output.strip()


def select_deep_review_files(triage_output: str, index: dict[str, object]) -> list[Path]:
    entries = index.get("files") or []
    by_abs: dict[str, Path] = {}
    by_rel: dict[str, Path] = {}
    for raw in entries:
        entry = raw if isinstance(raw, dict) else {}
        path = Path(str(entry.get("path", "")))
        if not path.exists() or path.suffix.lower() not in QUALITY_CONTENT_SUFFIXES:
            continue
        by_abs[str(path)] = path
        by_rel[str(entry.get("relative_path", ""))] = path

    selected: list[Path] = []
    payload = extract_json_object(triage_output, {"selected_files"}, prefer_last=True) or {}
    selected_items = payload.get("selected_files", [])
    if not isinstance(selected_items, list):
        selected_items = []
    for item in selected_items:
        key = str(item)
        path = by_abs.get(key) or by_rel.get(key)
        if path and path not in selected:
            selected.append(path)
        if len(selected) >= QUALITY_DEEP_MAX_FILES:
            break

    if selected:
        return selected
    return preferred_quality_excerpts(index)[:QUALITY_DEEP_MAX_FILES]


def build_quality_grammar_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED GRAMMAR REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("GRAMMAR REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "DEEPSEEK GRAMMAR REVIEW INSTRUCTIONS",
                "Focus only on grammar, wording, clarity, terminology consistency, awkward phrasing, and bilingual expression issues. "
                "Do not judge thesis formatting, template compliance, contribution novelty, or experiment design. "
                "Cite file paths and quote only short snippets when needed. Provide concrete rewrite suggestions.",
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_format_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED FORMAT REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("FORMAT REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "CODEX FORMAT REVIEW INSTRUCTIONS",
                "Focus only on LaTeX/thesis formatting, structure, citation style, numbering, figures/tables, template compliance, "
                "and likely compile or submission-blocking issues. Do not spend time polishing grammar except when it affects required fields. "
                "Be evidence-grounded and cite file paths.",
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_readability_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED READABILITY REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("READABILITY REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "GEMINI READABILITY REVIEW INSTRUCTIONS",
                "Focus only on reader experience: narrative flow, section transitions, argument clarity, term consistency, "
                "abstract/introduction readability, conclusion framing, and whether figures/tables are explained in a way readers can follow. "
                "Do not duplicate detailed grammar checking or LaTeX template compliance. Cite file paths and give concise, actionable rewrites or restructuring suggestions.",
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_synthesis_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    triage_output: str,
    grammar_output: str,
    format_output: str,
    readability_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    summary = {
        "roots": index.get("roots", []),
        "indexed_files": index.get("count", 0),
        "indexed_bytes": index.get("total_bytes", 0),
        "selected_files": [str(path) for path in selected_files],
        "not_deep_reviewed_count": max(0, int(index.get("count", 0)) - len(selected_files)),
    }
    blocks.extend(
        [
            ("QUALITY REVIEW COVERAGE", json.dumps(summary, ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("DEEPSEEK GRAMMAR OUTPUT", grammar_output),
            ("CODEX FORMAT OUTPUT", format_output),
            ("GEMINI READABILITY OUTPUT", readability_output),
            ("USER TASK", task),
            (
                "SYNTHESIS INSTRUCTIONS",
                "Produce the final review report by merging the DeepSeek grammar review, Codex format review, and Gemini readability review. "
                "Separate format/template findings, grammar/wording findings, and readability/narrative findings. Start with submission-blocking issues, "
                "then important language and structure fixes, then coverage limitations. Explicitly list reviewed files and mention that "
                "unselected files were indexed but not deeply reviewed.",
            ),
        ]
    )
    return render_blocks(blocks)


def run_provider(provider: str, prompt: str) -> str:
    if provider == "echo":
        return prompt
    if provider == "codex-cli":
        codex = os.environ.get("CODEX_CLI", "/Applications/Codex.app/Contents/Resources/codex")
        cmd = [codex, "exec", "-", "--skip-git-repo-check"]
        try:
            result = subprocess.run(
                cmd,
                cwd=ROOT,
                check=False,
                text=True,
                input=prompt,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise OrchestratorError(f"Codex CLI not found: {codex}") from exc
        return result.stdout
    raise OrchestratorError(f"Unsupported provider: {provider}")


def run_deepseek(prompt: str, api_key: str | None = None) -> str:
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return (
            "DeepSeek grammar review skipped: missing DEEPSEEK_API_KEY. "
            "Set the environment variable or enter the key in the local web form."
        )
    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a precise academic grammar and wording reviewer. Return concise, evidence-grounded findings.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return f"DeepSeek grammar review failed: HTTP {exc.code}\n{detail}"
    except urllib.error.URLError as exc:
        return f"DeepSeek grammar review failed: {exc.reason}"
    except TimeoutError:
        return "DeepSeek grammar review failed: request timed out."

    choices = data.get("choices") or []
    if not choices:
        return f"DeepSeek grammar review failed: empty response\n{json.dumps(data, ensure_ascii=False)[:2000]}"
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def run_gemini(prompt: str, enabled: bool = True) -> str:
    if not enabled:
        return "Gemini readability review skipped: disabled by request."
    gemini = os.environ.get("GEMINI_CLI", "gemini")
    cmd = [gemini, "-p", prompt]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=int(os.environ.get("GEMINI_TIMEOUT", GEMINI_DEFAULT_TIMEOUT)),
        )
    except FileNotFoundError:
        return "Gemini readability review skipped: gemini CLI not found in PATH."
    except subprocess.TimeoutExpired:
        return "Gemini readability review failed: request timed out."
    return result.stdout.strip()


def write_run(agent: str, task: str, provider: str, skills: list[str], prompt: str, output: str) -> Path:
    run_dir = make_run_dir(agent, task)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "output.md").write_text(output, encoding="utf-8")
    meta = {
        "agent": agent,
        "skills": skills,
        "provider": provider,
        "task": task,
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    (run_dir / "trace.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def write_quality_report(task: str, final_report: str, raw_report: str | None = None) -> tuple[Path, Path | None]:
    RESEARCH_REVIEW_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", task.strip())[:48].strip("-") or "quality-review"
    clean_path = RESEARCH_REVIEW_CLEAN_DIR / f"{timestamp}-{slug}.md"
    clean_path.write_text(final_report, encoding="utf-8")
    raw_path = None
    if raw_report is not None:
        RESEARCH_REVIEW_RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = RESEARCH_REVIEW_RAW_DIR / f"{timestamp}-{slug}.raw.md"
        raw_path.write_text(raw_report, encoding="utf-8")
    return clean_path, raw_path


def run_quality_review(
    task: str,
    agent: str,
    context: list[str],
    skills: list[str],
    deepseek_api_key: str | None = None,
    use_gemini: bool = True,
) -> dict[str, object]:
    if not context:
        raise OrchestratorError("Quality Review needs at least one context file or folder.")
    run_dir = make_run_dir(agent, task)
    index = build_quality_index(context)
    (run_dir / "01-index.md").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    triage_prompt = build_quality_triage_prompt(agent, task, skills, index)
    (run_dir / "02-triage-prompt.md").write_text(triage_prompt, encoding="utf-8")
    triage_raw_output = run_provider("codex-cli", triage_prompt)
    (run_dir / "02-triage-output-raw.md").write_text(triage_raw_output, encoding="utf-8")
    triage_output = clean_codex_cli_output(triage_raw_output)
    (run_dir / "02-triage-output.md").write_text(triage_output, encoding="utf-8")

    selected_files = select_deep_review_files(triage_output, index)
    grammar_prompt = build_quality_grammar_prompt(agent, task, skills, index, triage_output, selected_files)
    format_prompt = build_quality_format_prompt(agent, task, skills, index, triage_output, selected_files)
    readability_prompt = build_quality_readability_prompt(agent, task, skills, index, triage_output, selected_files)
    (run_dir / "03a-deepseek-grammar-prompt.md").write_text(grammar_prompt, encoding="utf-8")
    (run_dir / "03b-codex-format-prompt.md").write_text(format_prompt, encoding="utf-8")
    (run_dir / "03c-gemini-readability-prompt.md").write_text(readability_prompt, encoding="utf-8")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        grammar_future = executor.submit(run_deepseek, grammar_prompt, deepseek_api_key)
        format_future = executor.submit(run_provider, "codex-cli", format_prompt)
        readability_future = executor.submit(run_gemini, readability_prompt, use_gemini)
        grammar_output = grammar_future.result()
        format_raw_output = format_future.result()
        format_output = clean_codex_cli_output(format_raw_output)
        readability_output = readability_future.result()

    (run_dir / "03a-deepseek-grammar-output.md").write_text(grammar_output, encoding="utf-8")
    (run_dir / "03b-codex-format-output-raw.md").write_text(format_raw_output, encoding="utf-8")
    (run_dir / "03b-codex-format-output.md").write_text(format_output, encoding="utf-8")
    (run_dir / "03c-gemini-readability-output.md").write_text(readability_output, encoding="utf-8")

    synthesis_prompt = build_quality_synthesis_prompt(
        agent,
        task,
        skills,
        index,
        triage_output,
        grammar_output,
        format_output,
        readability_output,
        selected_files,
    )
    (run_dir / "04-synthesis-prompt.md").write_text(synthesis_prompt, encoding="utf-8")
    raw_final_report = run_provider("codex-cli", synthesis_prompt)
    (run_dir / "final-report-raw.md").write_text(raw_final_report, encoding="utf-8")
    final_report = clean_codex_cli_output(raw_final_report)
    (run_dir / "final-report.md").write_text(final_report, encoding="utf-8")
    report_path, raw_report_path = write_quality_report(task, final_report, raw_final_report)

    meta = {
        "agent": agent,
        "skills": skills,
        "provider": "quality-review",
        "task": task,
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "context_roots": index.get("roots", []),
        "indexed_files": index.get("count", 0),
        "indexed_bytes": index.get("total_bytes", 0),
        "selected_files": [str(path) for path in selected_files],
        "report_path": str(report_path),
        "raw_research_review_path": str(raw_report_path) if raw_report_path else None,
        "raw_report_path": str(run_dir / "final-report-raw.md"),
        "parallel_review": {
            "grammar_provider": "deepseek",
            "format_provider": "codex-cli",
            "readability_provider": "gemini-cli" if use_gemini else "disabled",
            "deepseek_key_source": "request" if deepseek_api_key else ("env" if os.environ.get("DEEPSEEK_API_KEY") else "missing"),
        },
    }
    (run_dir / "trace.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    output = (
        "Quality Review complete.\n\n"
        f"Indexed files: {index.get('count', 0)}\n"
        f"Selected for deep review: {len(selected_files)}\n"
        "Parallel branches: DeepSeek grammar + Codex format + Gemini readability\n"
        f"Run saved: {run_dir}\n"
        f"Final report: {report_path}\n\n"
        f"{final_report}"
    )
    return {
        "agent": agent,
        "skills": skills,
        "provider": "quality-review",
        "task": task,
        "context_summary": {
            "count": index.get("count", 0),
            "total_bytes": index.get("total_bytes", 0),
            "files": index.get("files", []),
        },
        "output": output,
        "run_dir": str(run_dir),
        "report_path": str(report_path),
        "quality": {
            "indexed_files": index.get("count", 0),
            "selected_files": [str(path) for path in selected_files],
            "report_path": str(report_path),
            "parallel_branches": ["deepseek-grammar", "codex-format", "gemini-readability" if use_gemini else "gemini-disabled"],
        },
    }


def bootstrap_project(
    context: list[str] | None = None,
    provider: str = "codex-cli",
    dry_run: bool = False,
) -> dict[str, object]:
    context_paths = context or default_project_context()
    prompt = build_project_bootstrap_prompt(context_paths)
    context_summary = summarize_context_paths(context_paths)
    run_dir = make_run_dir("planner", "bootstrap-project-profile")
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    if dry_run or provider == "echo":
        (run_dir / "output.md").write_text(prompt, encoding="utf-8")
        return {
            "agent": "planner",
            "skills": [],
            "provider": "bootstrap",
            "task": "bootstrap project profile",
            "context_summary": context_summary,
            "output": (
                "Bootstrap dry run complete.\n\n"
                f"Active project: {ACTIVE_PROJECT_DIR}\n"
                f"Context files: {context_summary.get('count', 0)}\n"
                f"Prompt saved: {run_dir / 'prompt.md'}"
            ),
            "run_dir": str(run_dir),
            "updated_files": [],
        }

    raw_output = run_provider(provider, prompt)
    (run_dir / "output-raw.md").write_text(raw_output, encoding="utf-8")
    output = clean_codex_cli_output(raw_output)
    (run_dir / "output.md").write_text(output, encoding="utf-8")
    data = extract_json_object(output, set(PROJECT_PROFILE_FILES), prefer_last=True)
    if data is None:
        data = extract_json_object(raw_output, set(PROJECT_PROFILE_FILES), prefer_last=True)
    if data is None:
        raise OrchestratorError("Bootstrap output did not contain a JSON object.")

    updated_files: list[str] = []
    for filename in PROJECT_PROFILE_FILES:
        content = data.get(filename)
        if not isinstance(content, str) or not content.strip():
            raise OrchestratorError(f"Bootstrap output missing non-empty string for {filename}.")
        path = ACTIVE_PROJECT_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        updated_files.append(str(path))

    meta = {
        "agent": "planner",
        "provider": f"bootstrap:{provider}",
        "task": "bootstrap project profile",
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "context": context_summary,
        "updated_files": updated_files,
        "raw_output_path": str(run_dir / "output-raw.md"),
    }
    (run_dir / "trace.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "agent": "planner",
        "skills": [],
        "provider": "bootstrap",
        "task": "bootstrap project profile",
        "context_summary": context_summary,
        "output": (
            "Project bootstrap complete.\n\n"
            f"Active project: {ACTIVE_PROJECT_DIR}\n"
            f"Context files: {context_summary.get('count', 0)}\n"
            "Updated files:\n"
            + "\n".join(f"- {path}" for path in updated_files)
            + f"\n\nRun saved: {run_dir}"
        ),
        "run_dir": str(run_dir),
        "updated_files": updated_files,
    }


def run_task(
    task: str,
    agent: str | None = None,
    context: list[str] | None = None,
    skill: list[str] | None = None,
    provider: str = "echo",
    auto_skill: bool = True,
    deepseek_api_key: str | None = None,
    use_gemini: bool = True,
) -> dict[str, object]:
    if provider == "bootstrap":
        return bootstrap_project(context=context, provider="codex-cli")
    selected_agent = agent or detect_agent_from_message(task)
    selected_skills = infer_skills(task, skill or [], auto_skill)
    if provider == "quality-review":
        return run_quality_review(task, selected_agent, context or [], selected_skills, deepseek_api_key, use_gemini)
    context_summary = summarize_context_paths(context or [])
    prompt = build_prompt(selected_agent, task, context or [], selected_skills)
    output = run_provider(provider, prompt)
    run_dir = write_run(selected_agent, task, provider, selected_skills, prompt, output)
    return {
        "agent": selected_agent,
        "skills": selected_skills,
        "provider": provider,
        "task": task,
        "context_summary": context_summary,
        "prompt": prompt,
        "output": output,
        "run_dir": str(run_dir),
    }


def cmd_list(_: argparse.Namespace) -> int:
    print("Agents:")
    for name in list_agents():
        print(f"  {name}")
    print("\nSkills:")
    for name in list_skills():
        print(f"  {name}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    agent = args.agent or detect_agent_from_message(args.task)
    skills = infer_skills(args.task, args.skill, not args.no_auto_skill)
    print(build_prompt(agent, args.task, args.context, skills))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    result = run_task(
        task=args.task,
        agent=args.agent,
        context=args.context,
        skill=args.skill,
        provider=args.provider,
        auto_skill=not args.no_auto_skill,
        deepseek_api_key=args.deepseek_api_key,
        use_gemini=not args.no_gemini,
    )
    print(result["output"])
    print(f"\n[run saved] {result['run_dir']}")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    result = bootstrap_project(
        context=args.context or None,
        provider=args.provider,
        dry_run=args.dry_run,
    )
    print(result["output"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local multi-agent orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List available agents")
    p_list.set_defaults(func=cmd_list)

    p_render = sub.add_parser("render", help="Render the prompt for an agent task")
    p_render.add_argument("task")
    p_render.add_argument("--agent", "-a")
    p_render.add_argument("--context", "-c", action="append", default=[])
    p_render.add_argument("--skill", "-s", action="append", default=[])
    p_render.add_argument("--no-auto-skill", action="store_true")
    p_render.set_defaults(func=cmd_render)

    p_ask = sub.add_parser("ask", help="Run an agent task")
    p_ask.add_argument("task")
    p_ask.add_argument("--agent", "-a")
    p_ask.add_argument("--context", "-c", action="append", default=[])
    p_ask.add_argument("--skill", "-s", action="append", default=[])
    p_ask.add_argument("--no-auto-skill", action="store_true")
    p_ask.add_argument("--provider", choices=["echo", "codex-cli", "quality-review", "bootstrap"], default="echo")
    p_ask.add_argument("--deepseek-api-key")
    p_ask.add_argument("--no-gemini", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_bootstrap = sub.add_parser("bootstrap", help="Infer and write active project profile files")
    p_bootstrap.add_argument("--context", "-c", action="append", default=[], help="Source file or folder to inspect")
    p_bootstrap.add_argument("--provider", choices=["codex-cli", "echo"], default="codex-cli")
    p_bootstrap.add_argument("--dry-run", action="store_true", help="Write only the bootstrap prompt, not project files")
    p_bootstrap.set_defaults(func=cmd_bootstrap)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OrchestratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
