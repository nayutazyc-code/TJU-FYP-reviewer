#!/usr/bin/env python3
"""Minimal local multi-agent orchestrator.

This script reads project instructions plus `agents/*.md`, builds an agent prompt,
and either prints it or sends it to a supported local provider.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import io
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


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


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
QUALITY_COMPILE_TIMEOUT = 180
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
BOOTSTRAP_EXCLUDE_DIRS = CONTEXT_EXCLUDE_DIRS | {
    ".codex",
    "docx导出",
}
BOOTSTRAP_MANIFEST_SUFFIXES = QUALITY_MANIFEST_SUFFIXES
BOOTSTRAP_CONTENT_SUFFIXES = QUALITY_CONTENT_SUFFIXES
BOOTSTRAP_ASSET_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
BOOTSTRAP_INDEX_MAX_FILES = 300
BOOTSTRAP_EXCERPT_MAX_FILES = 18
BOOTSTRAP_EXCERPT_BYTES = 32_000
BOOTSTRAP_TEXT_READ_BYTES = 1_000_000
PLACEHOLDER_PATTERNS = (
    "TODO",
    "人工确认",
    "此处",
    "模板",
    "示例",
    "样例",
    "关键词1",
    "英文摘要应",
)
INTERNAL_TRACE_PATTERNS = (
    "/Users/",
    "/private/",
    "docx导出",
    "ai-review-bundle",
    "outputs/runs",
    "localhost",
    "127.0.0.1",
)
STRUCTURED_FINDING_SCHEMA = {
    "severity": "Blocking | Major | Minor | Note",
    "category": "format | citation | compile | claim | experiment | prose | asset | structure",
    "file": "/absolute/path or null",
    "line": "line number or null",
    "title": "short issue title",
    "evidence": "short source-grounded evidence",
    "why_it_matters": "submission/review risk",
    "minimum_fix": "smallest concrete fix",
    "status": "confirmed | likely | needs_human_confirmation",
}
RESULT_INVENTORY_SUFFIXES = {
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".txt",
    ".log",
    ".md",
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".pt",
    ".pth",
    ".pkl",
    ".npy",
    ".npz",
}
RESULT_TEXT_SUFFIXES = {
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".txt",
    ".log",
    ".md",
}
RESULT_METRIC_NAME_RE = re.compile(
    r"(?:acc(?:uracy)?|precision|recall|f1|auc|ap|map|mae|mse|rmse|r2|loss|val_loss|train_loss|reward|score|epoch|steps?)",
    re.IGNORECASE,
)
RESULT_METRIC_PAIR_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.\-/ ]{0,64})\s*[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE,
)
RESULT_INVENTORY_START = "<!-- BEGIN AUTO RESULT INVENTORY -->"
RESULT_INVENTORY_END = "<!-- END AUTO RESULT INVENTORY -->"
MAX_RESULT_FILES = 300
MAX_RESULT_FILE_BYTES = 1_000_000
MAX_RESULT_METRICS_PER_FILE = 30


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


def relative_to_any(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


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


def should_index_bootstrap_file(path: Path) -> bool:
    suffixes = "".join(path.suffixes).lower()
    suffix = path.suffix.lower()
    if (suffixes in CONTEXT_EXCLUDE_SUFFIXES or suffix in CONTEXT_EXCLUDE_SUFFIXES) and suffix not in BOOTSTRAP_ASSET_SUFFIXES:
        return False
    if any(part in BOOTSTRAP_EXCLUDE_DIRS for part in path.parts):
        return False
    return suffix in BOOTSTRAP_MANIFEST_SUFFIXES


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


def iter_bootstrap_paths(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not path.exists():
            raise OrchestratorError(f"Context path not found: {path}")
        if path.is_file():
            if should_index_bootstrap_file(path):
                files.append(path)
            continue
        if not path.is_dir():
            raise OrchestratorError(f"Context path is neither file nor directory: {path}")
        for candidate in sorted(path.rglob("*")):
            if len(files) >= BOOTSTRAP_INDEX_MAX_FILES:
                break
            if candidate.is_file() and should_index_bootstrap_file(candidate):
                files.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def summarize_bootstrap_paths(paths: list[str]) -> dict[str, object]:
    roots = context_roots(paths)
    files = iter_bootstrap_paths(paths)
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        entries.append(
            {
                "path": str(path),
                "relative_path": relative_to_any(path, roots),
                "bytes": size,
                "suffix": path.suffix.lower(),
            }
        )
    return {
        "roots": [str(root) for root in roots],
        "count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
        "excluded_dirs": sorted(BOOTSTRAP_EXCLUDE_DIRS),
        "truncated": len(files) >= BOOTSTRAP_INDEX_MAX_FILES,
    }


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


def clean_latex_inline(value: str) -> str:
    value = re.sub(r"%.*", "", value)
    value = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", "", value)
    value = value.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", value).strip()


def latex_command_values(text: str, command: str) -> list[str]:
    pattern = re.compile(rf"\\{re.escape(command)}(?:\[[^\]]*\])?\{{([^{{}}]*)\}}", re.DOTALL)
    return [clean_latex_inline(match.group(1)) for match in pattern.finditer(text)]


def resolve_latex_reference(base: Path, value: str) -> Path | None:
    raw = value.strip()
    if not raw or raw.startswith("%"):
        return None
    raw = raw.split("%", 1)[0].strip()
    path = (base.parent / raw).resolve()
    candidates = [path]
    if not path.suffix:
        candidates.append(path.with_suffix(".tex"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1] if candidates else None


def extract_latex_refs(text: str, command: str, base: Path) -> list[str]:
    refs: list[str] = []
    pattern = re.compile(rf"\\{re.escape(command)}(?:\[[^\]]*\])?\{{([^{{}}]+)\}}")
    for match in pattern.finditer(text):
        resolved = resolve_latex_reference(base, match.group(1))
        if resolved:
            refs.append(str(resolved))
    return refs


def extract_citation_keys(text: str) -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?(?:\[[^\]]*\])*\{([^{}]+)\}")
    for match in pattern.finditer(text):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key != "*":
                keys.add(key)
    return keys


def extract_bib_keys(text: str) -> set[str]:
    return {match.group(1).strip() for match in re.finditer(r"@\w+\s*\{\s*([^,\s]+)", text)}


def placeholder_hits(path: Path, text: str) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for pattern in PLACEHOLDER_PATTERNS:
        count = text.count(pattern)
        if count:
            hits.append({"path": str(path), "pattern": pattern, "count": count})
    return hits


def build_bootstrap_inventory(context_paths: list[str]) -> dict[str, object]:
    roots = context_roots(context_paths)
    files = iter_bootstrap_paths(context_paths)
    entries: list[dict[str, object]] = []
    main_tex_candidates: list[str] = []
    included_files: set[str] = set()
    bibliography_files: set[str] = set()
    citation_keys: set[str] = set()
    bib_keys: set[str] = set()
    bib_texts: dict[str, str] = {}
    outlines: dict[str, list[str]] = {}
    metadata: dict[str, str] = {}
    placeholders: list[dict[str, object]] = []
    assets: list[str] = []
    result_candidates: list[str] = []

    metadata_commands = {
        "cheading": "页眉 / 届别",
        "ctitle": "论文题目",
        "caffil": "学院",
        "csubject": "专业",
        "cgrade": "年级",
        "cauthor": "作者",
        "cnumber": "学号",
        "csupervisor": "指导教师",
    }

    for path in files:
        suffix = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rel = relative_to_any(path, roots)
        signals: list[str] = []
        if suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
            signals.append("listed-binary-asset")
            assets.append(str(path))
        if suffix in {".csv", ".tsv", ".json", ".yaml", ".yml"} or "results" in path.parts:
            signals.append("possible-result-or-data-file")
            result_candidates.append(str(path))
        entry: dict[str, object] = {
            "path": str(path),
            "relative_path": rel,
            "suffix": suffix,
            "bytes": size,
        }

        if suffix in BOOTSTRAP_CONTENT_SUFFIXES:
            text = read_text_limited(path, min(size or BOOTSTRAP_TEXT_READ_BYTES, BOOTSTRAP_TEXT_READ_BYTES))
            placeholders.extend(placeholder_hits(path, text))
            if suffix in {".tex", ".cls", ".sty"}:
                if suffix == ".tex":
                    outline = extract_tex_outline(text)
                    if outline:
                        outlines[str(path)] = outline
                    if "\\documentclass" in text:
                        signals.append("main-tex-candidate")
                        main_tex_candidates.append(str(path))
                for command in ("include", "input"):
                    included_files.update(extract_latex_refs(text, command, path))
                for command in ("addbibresource", "bibliography"):
                    bibliography_files.update(extract_latex_refs(text, command, path))
                if suffix == ".tex":
                    citation_keys.update(extract_citation_keys(text))
                    for command, label in metadata_commands.items():
                        values = latex_command_values(text, command)
                        if values and label not in metadata:
                            metadata[label] = values[0]
            elif suffix == ".bib":
                bib_texts[str(path)] = text

        if signals:
            entry["signals"] = signals
        entries.append(entry)

    if bibliography_files:
        for path in bibliography_files:
            text = bib_texts.get(path)
            if text:
                bib_keys.update(extract_bib_keys(text))
    else:
        for text in bib_texts.values():
            bib_keys.update(extract_bib_keys(text))
    missing_citations = sorted(citation_keys - bib_keys)
    unused_bib_keys = sorted(bib_keys - citation_keys)
    return {
        "roots": [str(root) for root in roots],
        "indexed_files": len(entries),
        "main_tex_candidates": main_tex_candidates,
        "included_tex_files": sorted(path for path in included_files if path.endswith(".tex")),
        "bibliography_files": sorted(bibliography_files),
        "metadata_extracted_from_latex": metadata,
        "chapter_outline_by_file": outlines,
        "citations": {
            "cited_key_count": len(citation_keys),
            "bib_entry_count": len(bib_keys),
            "missing_bib_entries_for_cited_keys": missing_citations,
            "uncited_bib_entries": unused_bib_keys[:50],
            "uncited_bib_entries_truncated": len(unused_bib_keys) > 50,
        },
        "placeholder_or_template_signals": placeholders[:80],
        "placeholder_or_template_signals_truncated": len(placeholders) > 80,
        "assets_listed_only": assets[:80],
        "assets_listed_only_truncated": len(assets) > 80,
        "result_or_data_candidates": result_candidates[:80],
        "result_or_data_candidates_truncated": len(result_candidates) > 80,
        "files": entries,
        "limits": {
            "bootstrap_index_max_files": BOOTSTRAP_INDEX_MAX_FILES,
            "bootstrap_excerpt_max_files": BOOTSTRAP_EXCERPT_MAX_FILES,
            "bootstrap_excerpt_bytes": BOOTSTRAP_EXCERPT_BYTES,
        },
    }


def source_line_for_pattern(text: str, pattern: str) -> int | None:
    index = text.find(pattern)
    if index < 0:
        return None
    return text.count("\n", 0, index) + 1


def source_line_for_regex(text: str, match: re.Match[str]) -> int:
    return text.count("\n", 0, match.start()) + 1


def make_finding(
    severity: str,
    category: str,
    title: str,
    evidence: str,
    minimum_fix: str,
    file: str | None = None,
    line: int | None = None,
    why_it_matters: str = "",
    status: str = "confirmed",
) -> dict[str, object]:
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "title": title,
        "evidence": evidence,
        "why_it_matters": why_it_matters,
        "minimum_fix": minimum_fix,
        "status": status,
    }


def extract_graphics_refs(text: str) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []
    pattern = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}")
    for match in pattern.finditer(text):
        refs.append((match.group(1).strip(), source_line_for_regex(text, match)))
    return refs


def extract_label_defs(text: str) -> set[str]:
    return {match.group(1).strip() for match in re.finditer(r"\\label\{([^{}]+)\}", text)}


def extract_label_refs(text: str) -> list[tuple[str, int, str]]:
    refs: list[tuple[str, int, str]] = []
    pattern = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref)\{([^{}]+)\}")
    for match in pattern.finditer(text):
        refs.append((match.group(1).strip(), source_line_for_regex(text, match), match.group(0)))
    return refs


def resolve_graphics_reference(base: Path, raw_value: str, roots: list[Path]) -> Path | None:
    value = raw_value.split("%", 1)[0].strip()
    if not value:
        return None
    raw_path = Path(value)
    bases = [base.parent] + roots
    candidates: list[Path] = []
    for root in bases:
        path = raw_path if raw_path.is_absolute() else root / raw_path
        candidates.append(path)
        if not path.suffix:
            for suffix in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
                candidates.append(path.with_suffix(suffix))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def render_preflight_markdown(preflight: dict[str, object]) -> str:
    findings = preflight.get("findings") or []
    lines = [
        "# Deterministic Preflight",
        "",
        f"- Status: {preflight.get('status', 'unknown')}",
        f"- Indexed files: {preflight.get('indexed_files', 0)}",
        f"- Main TeX candidates: {len(preflight.get('main_tex_candidates') or [])}",
        f"- Compile status: {(preflight.get('compile') or {}).get('status', 'not_run')}",
        f"- Findings: {len(findings)}",
        "",
        "## Findings",
        "",
    ]
    if not findings:
        lines.append("- No deterministic findings.")
    for index, raw in enumerate(findings, start=1):
        finding = raw if isinstance(raw, dict) else {}
        location = str(finding.get("file") or "project")
        if finding.get("line"):
            location += f":{finding.get('line')}"
        lines.extend(
            [
                f"### P{index}: {finding.get('severity')} / {finding.get('category')} / {finding.get('title')}",
                "",
                f"- Location: `{location}`",
                f"- Status: {finding.get('status')}",
                f"- Evidence: {finding.get('evidence')}",
                f"- Why it matters: {finding.get('why_it_matters')}",
                f"- Minimum fix: {finding.get('minimum_fix')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_compile_preflight(main_tex: Path | None, enabled: bool = True) -> dict[str, object]:
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    if main_tex is None:
        return {"status": "skipped", "reason": "no main TeX candidate"}
    cmd = ["latexmk", "-xelatex", "-interaction=nonstopmode", "-halt-on-error", main_tex.name]
    try:
        result = subprocess.run(
            cmd,
            cwd=main_tex.parent,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=QUALITY_COMPILE_TIMEOUT,
        )
    except FileNotFoundError:
        return {"status": "skipped", "reason": "latexmk not found", "command": " ".join(cmd), "cwd": str(main_tex.parent)}
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return {
            "status": "timeout",
            "reason": f"latexmk exceeded {QUALITY_COMPILE_TIMEOUT}s",
            "command": " ".join(cmd),
            "cwd": str(main_tex.parent),
            "output_tail": output[-4000:],
        }
    output = result.stdout or ""
    warning_patterns = [
        "LaTeX Warning: Citation",
        "LaTeX Warning: Reference",
        "There were undefined references",
        "Package biblatex Warning",
        "Undefined control sequence",
        "LaTeX Error:",
    ]
    warnings = [line.strip() for line in output.splitlines() if any(pattern in line for pattern in warning_patterns)]
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "returncode": result.returncode,
        "command": " ".join(cmd),
        "cwd": str(main_tex.parent),
        "warnings": warnings[:80],
        "warnings_truncated": len(warnings) > 80,
        "output_tail": output[-4000:],
    }


def build_quality_preflight(
    context_paths: list[str],
    index: dict[str, object],
    run_compile: bool = True,
) -> dict[str, object]:
    roots = [Path(str(root)) for root in index.get("roots", [])]
    inventory = build_bootstrap_inventory(context_paths)
    files = iter_bootstrap_paths(context_paths)
    findings: list[dict[str, object]] = []
    tex_texts: dict[Path, str] = {}
    label_defs: set[str] = set()
    label_refs: list[tuple[Path, str, int, str]] = []
    main_candidates = [Path(str(path)) for path in inventory.get("main_tex_candidates", [])]
    main_tex = main_candidates[0] if main_candidates else None

    if not main_candidates:
        findings.append(
            make_finding(
                "Blocking",
                "compile",
                "No LaTeX main file detected",
                "No indexed .tex file contained \\documentclass.",
                "Add or point quality-review context at the thesis main .tex file.",
                why_it_matters="The reviewer cannot verify document order, compilation, or bibliography wiring without an entry file.",
            )
        )
    elif len(main_candidates) > 1:
        findings.append(
            make_finding(
                "Major",
                "compile",
                "Multiple LaTeX main files detected",
                ", ".join(str(path) for path in main_candidates),
                "Pass the intended thesis root or remove stale main-file candidates from review context.",
                why_it_matters="Ambiguous entry files can cause the reviewer to inspect a stale draft.",
                status="likely",
            )
        )

    for raw_path in inventory.get("included_tex_files", []):
        path = Path(str(raw_path))
        if not path.exists():
            findings.append(
                make_finding(
                    "Blocking",
                    "structure",
                    "Included TeX file is missing",
                    str(path),
                    "Create the included file or remove the stale \\include/\\input reference.",
                    file=str(path),
                    why_it_matters="LaTeX compilation will fail or silently omit expected thesis content.",
                )
            )

    citations = inventory.get("citations") or {}
    missing_citations = citations.get("missing_bib_entries_for_cited_keys") or []
    if missing_citations:
        findings.append(
            make_finding(
                "Blocking",
                "citation",
                "Cited keys missing from bibliography",
                ", ".join(str(key) for key in missing_citations),
                "Add matching BibLaTeX entries to the active bibliography or correct the cite keys.",
                file=str((inventory.get("bibliography_files") or [None])[0]) if inventory.get("bibliography_files") else None,
                why_it_matters="Undefined citations are a submission-blocking bibliography defect.",
            )
        )

    for raw_path in inventory.get("bibliography_files", []):
        path = Path(str(raw_path))
        if not path.exists():
            findings.append(
                make_finding(
                    "Blocking",
                    "citation",
                    "Bibliography file is missing",
                    str(path),
                    "Create the bibliography file or update \\addbibresource / \\bibliography.",
                    file=str(path),
                    why_it_matters="References cannot compile without the declared bibliography file.",
                )
            )

    for path in files:
        if path.suffix.lower() not in {".tex", ".cls", ".sty", ".bib"}:
            continue
        text = read_text_limited(path, QUALITY_DEEP_FILE_BYTES)
        if path.suffix.lower() == ".tex":
            tex_texts[path] = text
            label_defs.update(extract_label_defs(text))
            for label, line, raw_ref in extract_label_refs(text):
                label_refs.append((path, label, line, raw_ref))
            for graphic, line in extract_graphics_refs(text):
                if resolve_graphics_reference(path, graphic, roots) is None:
                    findings.append(
                        make_finding(
                            "Major",
                            "asset",
                            "Figure asset referenced by LaTeX is missing",
                            f"\\includegraphics{{{graphic}}}",
                            "Add the figure file at the referenced path or update the figure path.",
                            file=str(path),
                            line=line,
                            why_it_matters="Missing figures cause compile failures or blank thesis figures.",
                        )
                    )
        for pattern in INTERNAL_TRACE_PATTERNS:
            line = source_line_for_pattern(text, pattern)
            if line is not None:
                findings.append(
                    make_finding(
                        "Major",
                        "format",
                        "Internal path or process trace appears in source",
                        pattern,
                        "Remove internal paths, export bundle names, localhost URLs, and process traces from submission-facing text.",
                        file=str(path),
                        line=line,
                        why_it_matters="Submission drafts should not expose local machine paths or internal review artifacts.",
                    )
                )

    for path, text in tex_texts.items():
        for pattern in PLACEHOLDER_PATTERNS:
            line = source_line_for_pattern(text, pattern)
            if line is not None:
                severity = "Blocking" if path.name in {"introduction.tex", "appendix.tex", "acknowledgements.tex"} else "Major"
                findings.append(
                    make_finding(
                        severity,
                        "format",
                        "Template or placeholder text remains in thesis source",
                        pattern,
                        "Replace template text with final thesis content or delete demo material.",
                        file=str(path),
                        line=line,
                        why_it_matters="Template remnants are visible submission defects.",
                    )
                )

    missing_labels = sorted({label for _, label, _, _ in label_refs if label not in label_defs})
    if missing_labels:
        examples = []
        for path, label, line, raw_ref in label_refs:
            if label in missing_labels:
                examples.append(f"{path}:{line} {raw_ref}")
            if len(examples) >= 8:
                break
        findings.append(
            make_finding(
                "Major",
                "structure",
                "References point to missing labels",
                "; ".join(examples),
                "Add the missing \\label entries or correct the \\ref/\\eqref keys.",
                why_it_matters="Undefined cross-references produce broken numbering in the compiled thesis.",
            )
        )

    compile_result = run_compile_preflight(main_tex, run_compile)
    if compile_result.get("status") == "fail":
        findings.append(
            make_finding(
                "Blocking",
                "compile",
                "LaTeX compile failed",
                str(compile_result.get("output_tail", "")).strip()[-1200:],
                "Fix the first LaTeX error, then rerun quality-review.",
                file=str(main_tex) if main_tex else None,
                why_it_matters="A thesis that does not compile is not submission-ready.",
            )
        )
    elif compile_result.get("status") == "timeout":
        findings.append(
            make_finding(
                "Major",
                "compile",
                "LaTeX compile timed out",
                str(compile_result.get("reason", "")),
                "Run latexmk manually and inspect the current compile blocker.",
                file=str(main_tex) if main_tex else None,
                why_it_matters="The automated review could not confirm a stable build.",
                status="needs_human_confirmation",
            )
        )
    elif compile_result.get("status") == "skipped":
        findings.append(
            make_finding(
                "Note",
                "compile",
                "LaTeX compile check skipped",
                str(compile_result.get("reason", "")),
                "Run latexmk in the thesis root before submission.",
                file=str(main_tex) if main_tex else None,
                why_it_matters="The review cannot prove build readiness without a compile check.",
                status="needs_human_confirmation",
            )
        )
    elif compile_result.get("warnings"):
        findings.append(
            make_finding(
                "Major",
                "compile",
                "LaTeX compile completed with warnings",
                "; ".join(str(item) for item in compile_result.get("warnings", [])[:8]),
                "Resolve undefined references, undefined citations, and biblatex warnings.",
                file=str(main_tex) if main_tex else None,
                why_it_matters="Warnings often surface broken references or bibliography defects even when PDF generation succeeds.",
            )
        )

    severity_order = {"Blocking": 0, "Major": 1, "Minor": 2, "Note": 3}
    findings.sort(key=lambda item: (severity_order.get(str(item.get("severity")), 9), str(item.get("file") or ""), int(item.get("line") or 0)))
    blocking_count = sum(1 for finding in findings if finding.get("severity") == "Blocking")
    return {
        "status": "blocked" if blocking_count else "pass_with_findings" if findings else "pass",
        "indexed_files": index.get("count", 0),
        "main_tex_candidates": [str(path) for path in main_candidates],
        "inventory": {
            "included_tex_files": inventory.get("included_tex_files", []),
            "bibliography_files": inventory.get("bibliography_files", []),
            "citations": inventory.get("citations", {}),
            "assets_listed_only": inventory.get("assets_listed_only", []),
            "result_or_data_candidates": inventory.get("result_or_data_candidates", []),
        },
        "compile": compile_result,
        "finding_schema": STRUCTURED_FINDING_SCHEMA,
        "findings": findings,
    }


def preferred_bootstrap_excerpts(context_paths: list[str], inventory: dict[str, object]) -> list[Path]:
    files = iter_bootstrap_paths(context_paths)
    main_tex = set(str(path) for path in inventory.get("main_tex_candidates", []))
    included = set(str(path) for path in inventory.get("included_tex_files", []))
    bibliography = set(str(path) for path in inventory.get("bibliography_files", []))
    scored: list[tuple[int, Path]] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix not in BOOTSTRAP_CONTENT_SUFFIXES:
            continue
        path_str = str(path)
        rel = path_str.lower()
        score = 0
        if path_str in main_tex:
            score += 120
        if path_str in included:
            score += 90
        if path_str in bibliography:
            score += 70
        if suffix == ".tex":
            score += 40
        if suffix == ".bib":
            score += 35
        if path.name.lower() in {"readme.md", "agents.md"}:
            score += 22
        if any(token in rel for token in ("introduction", "chapter", "contents", "reference")):
            score += 18
        if "results" in path.parts or suffix in {".csv", ".tsv", ".json", ".yaml", ".yml"}:
            score += 30
        if score:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return [path for _, path in scored[:BOOTSTRAP_EXCERPT_MAX_FILES]]


def build_project_bootstrap_prompt(context_paths: list[str]) -> str:
    context_summary = summarize_bootstrap_paths(context_paths)
    inventory = build_bootstrap_inventory(context_paths)
    excerpts = render_context_blocks(preferred_bootstrap_excerpts(context_paths, inventory), BOOTSTRAP_EXCERPT_BYTES)
    existing = render_blocks(
        [(f"EXISTING {filename}", optional_text(ACTIVE_PROJECT_DIR / filename)) for filename in PROJECT_PROFILE_FILES]
    )
    schema = {filename: f"complete replacement Markdown for {filename}" for filename in PROJECT_PROFILE_FILES}
    blocks = [
        ("GLOBAL PROJECT INSTRUCTIONS", optional_text(ROOT / "AGENTS.md")),
        ("TASK", "Infer and update the active research project profile from the supplied files."),
        ("ACTIVE PROJECT DIRECTORY", str(ACTIVE_PROJECT_DIR)),
        ("CONTEXT SUMMARY", json.dumps(context_summary, ensure_ascii=False, indent=2)),
        ("DETERMINISTIC PROJECT INVENTORY", json.dumps(inventory, ensure_ascii=False, indent=2)),
        ("EXISTING PROJECT PROFILE FILES", existing),
        ("CORE SOURCE FILE EXCERPTS", excerpts),
        (
            "BOOTSTRAP RULES",
            "Return ONLY a JSON object whose keys are exactly PROJECT.md, MEMORY.md, claims.md, and experiments.md. "
            "Each value must be complete Markdown file content. Preserve useful existing sections, but replace stale or contradicted details. "
            "PROJECT.md is the stable fact source: write only facts supported by source files or the existing confirmed profile; mark uncertain fields as TODO / 人工确认. "
            "MEMORY.md is dynamic memory: put current stage, known issues, review focus, risks, and human-confirmation needs there. "
            "claims.md should list only claims visible in the thesis/profile, with evidence status Supported, Partially supported, Unsupported/Risky, or Needs human confirmation. "
            "experiments.md should list observed experiment artifacts, commands, parameters, outputs, and reproducibility status; if a command/result is not visible, mark it as TODO / 人工确认. "
            "Do not invent paper titles, numeric results, claims, commands, data sources, advisor preferences, or experiment outcomes. "
            "Treat docx export bundles, prompts, review logs, local absolute paths, and previous AI review reports as internal material, not thesis evidence. "
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
    claims = project_text("claims.md")
    experiments = project_text("experiments.md")
    role = load_agent(agent)
    extra_context = load_context_files(context_paths)
    loaded_skills = [load_skill(name) for name in skills]

    blocks = [
        ("GLOBAL PROJECT INSTRUCTIONS", global_rules),
        ("PROJECT FACTS", project),
        ("PROJECT MEMORY", memory),
        ("PROJECT CLAIMS", claims),
        ("PROJECT EXPERIMENTS", experiments),
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
        ("PROJECT CLAIMS", project_text("claims.md")),
        ("PROJECT EXPERIMENTS", project_text("experiments.md")),
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


def preflight_prompt_summary(preflight: dict[str, object]) -> dict[str, object]:
    compile_result = preflight.get("compile") or {}
    findings = preflight.get("findings") or []
    return {
        "status": preflight.get("status"),
        "main_tex_candidates": preflight.get("main_tex_candidates", []),
        "compile": {
            "status": compile_result.get("status"),
            "returncode": compile_result.get("returncode"),
            "reason": compile_result.get("reason"),
            "command": compile_result.get("command"),
            "cwd": compile_result.get("cwd"),
            "warnings": compile_result.get("warnings", [])[:20],
        },
        "inventory": preflight.get("inventory", {}),
        "finding_schema": preflight.get("finding_schema", STRUCTURED_FINDING_SCHEMA),
        "findings": findings,
    }


def structured_findings_contract(branch_name: str) -> str:
    return (
        f"Return {branch_name} findings in a structured list. Each actionable issue must include these fields: "
        f"{json.dumps(STRUCTURED_FINDING_SCHEMA, ensure_ascii=False)}. "
        "Order by severity: Blocking, Major, Minor, Note. "
        "Use status=confirmed only when the supplied source or deterministic preflight proves the issue; otherwise use likely or needs_human_confirmation. "
        "For non-actionable context, keep it under a short 'Coverage / Residual risk' section instead of mixing it into findings."
    )


def build_quality_triage_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    preflight: dict[str, object],
) -> str:
    excerpts = render_context_blocks(preferred_quality_excerpts(index), QUALITY_TRIAGE_EXCERPT_BYTES)
    blocks = base_instruction_blocks(agent, skills)
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX", json.dumps(index, ensure_ascii=False, indent=2)),
            ("DETERMINISTIC PREFLIGHT", json.dumps(preflight_prompt_summary(preflight), ensure_ascii=False, indent=2)),
            ("SMALL TRIAGE EXCERPTS", excerpts),
            ("USER TASK", task),
            (
                "TRIAGE INSTRUCTIONS",
                "Choose the smallest set of files needed for a high-quality deep review. "
                f"Select at most {QUALITY_DEEP_MAX_FILES} text files. Prefer main .tex files, core chapters, "
                "bibliography, and data/result files when relevant. Do not select binary assets. "
                "Always include files needed to investigate Blocking or Major deterministic preflight findings. "
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
    preflight: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("DETERMINISTIC PREFLIGHT", json.dumps(preflight_prompt_summary(preflight), ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED GRAMMAR REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("GRAMMAR REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "DEEPSEEK GRAMMAR REVIEW INSTRUCTIONS",
                "Focus only on grammar, wording, clarity, terminology consistency, awkward phrasing, and bilingual expression issues. "
                "Do not judge thesis formatting, template compliance, contribution novelty, or experiment design. "
                "Cite file paths and quote only short snippets when needed. Provide concrete rewrite suggestions. "
                + structured_findings_contract("grammar/prose"),
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_format_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    preflight: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("DETERMINISTIC PREFLIGHT", json.dumps(preflight_prompt_summary(preflight), ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED FORMAT REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("FORMAT REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "CODEX FORMAT REVIEW INSTRUCTIONS",
                "Focus only on LaTeX/thesis formatting, structure, citation style, numbering, figures/tables, template compliance, "
                "and likely compile or submission-blocking issues. Do not spend time polishing grammar except when it affects required fields. "
                "Treat deterministic preflight findings as already-proven unless you see direct contradictory source evidence. "
                "Be evidence-grounded and cite file paths. "
                + structured_findings_contract("format/compile/citation"),
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_readability_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    preflight: dict[str, object],
    triage_output: str,
    selected_files: list[Path],
) -> str:
    blocks = base_instruction_blocks(agent, skills)
    deep_context = render_context_blocks(selected_files, QUALITY_DEEP_FILE_BYTES)
    selected = [str(path) for path in selected_files]
    blocks.extend(
        [
            ("QUALITY REVIEW INDEX SUMMARY", json.dumps({k: index[k] for k in ("roots", "count", "total_bytes", "main_tex_candidates", "limits")}, ensure_ascii=False, indent=2)),
            ("DETERMINISTIC PREFLIGHT", json.dumps(preflight_prompt_summary(preflight), ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("SELECTED READABILITY REVIEW FILES", json.dumps(selected, ensure_ascii=False, indent=2)),
            ("READABILITY REVIEW CONTEXT", deep_context),
            ("USER TASK", task),
            (
                "GEMINI READABILITY REVIEW INSTRUCTIONS",
                "Focus only on reader experience: narrative flow, section transitions, argument clarity, term consistency, "
                "abstract/introduction readability, conclusion framing, and whether figures/tables are explained in a way readers can follow. "
                "Do not duplicate detailed grammar checking or LaTeX template compliance. Cite file paths and give concise, actionable rewrites or restructuring suggestions. "
                + structured_findings_contract("readability/narrative"),
            ),
        ]
    )
    return render_blocks(blocks)


def build_quality_synthesis_prompt(
    agent: str,
    task: str,
    skills: list[str],
    index: dict[str, object],
    preflight: dict[str, object],
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
            ("DETERMINISTIC PREFLIGHT", json.dumps(preflight_prompt_summary(preflight), ensure_ascii=False, indent=2)),
            ("TRIAGE OUTPUT", triage_output),
            ("DEEPSEEK GRAMMAR OUTPUT", grammar_output),
            ("CODEX FORMAT OUTPUT", format_output),
            ("GEMINI READABILITY OUTPUT", readability_output),
            ("USER TASK", task),
            (
                "SYNTHESIS INSTRUCTIONS",
                "Produce the final review report by merging the DeepSeek grammar review, Codex format review, and Gemini readability review. "
                "Start with a 'Structured Findings' section. Every actionable issue in that section must follow the exact schema from deterministic preflight. "
                "Preserve all Blocking and Major deterministic preflight findings unless a reviewer branch proves they are obsolete. "
                "Separate format/template findings, grammar/wording findings, and readability/narrative findings after the structured list if useful. "
                "Then include minimum fixes, open questions, coverage limitations, reviewed files, and compile result. Mention that unselected files were indexed but not deeply reviewed. "
                + structured_findings_contract("final synthesized"),
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
    compile_preflight: bool = True,
) -> dict[str, object]:
    if not context:
        raise OrchestratorError("Quality Review needs at least one context file or folder.")
    run_dir = make_run_dir(agent, task)
    index = build_quality_index(context)
    (run_dir / "01-index.md").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    preflight = build_quality_preflight(context, index, run_compile=compile_preflight)
    (run_dir / "00-preflight.json").write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "00-preflight.md").write_text(render_preflight_markdown(preflight), encoding="utf-8")

    triage_prompt = build_quality_triage_prompt(agent, task, skills, index, preflight)
    (run_dir / "02-triage-prompt.md").write_text(triage_prompt, encoding="utf-8")
    triage_raw_output = run_provider("codex-cli", triage_prompt)
    (run_dir / "02-triage-output-raw.md").write_text(triage_raw_output, encoding="utf-8")
    triage_output = clean_codex_cli_output(triage_raw_output)
    (run_dir / "02-triage-output.md").write_text(triage_output, encoding="utf-8")

    selected_files = select_deep_review_files(triage_output, index)
    grammar_prompt = build_quality_grammar_prompt(agent, task, skills, index, preflight, triage_output, selected_files)
    format_prompt = build_quality_format_prompt(agent, task, skills, index, preflight, triage_output, selected_files)
    readability_prompt = build_quality_readability_prompt(agent, task, skills, index, preflight, triage_output, selected_files)
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
        preflight,
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
        "preflight_path": str(run_dir / "00-preflight.json"),
        "preflight_status": preflight.get("status"),
        "preflight_findings": len(preflight.get("findings") or []),
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
        f"Preflight status: {preflight.get('status')} ({len(preflight.get('findings') or [])} findings)\n"
        f"Compile status: {(preflight.get('compile') or {}).get('status')}\n"
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
            "preflight": {
                "status": preflight.get("status"),
                "findings": len(preflight.get("findings") or []),
                "compile_status": (preflight.get("compile") or {}).get("status"),
            },
        },
        "output": output,
        "run_dir": str(run_dir),
        "report_path": str(report_path),
        "quality": {
            "indexed_files": index.get("count", 0),
            "preflight_status": preflight.get("status"),
            "preflight_findings": len(preflight.get("findings") or []),
            "preflight_path": str(run_dir / "00-preflight.json"),
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
    context_summary = summarize_bootstrap_paths(context_paths)
    run_dir = make_run_dir("planner", "bootstrap-project-profile")
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    backup_dir = run_dir / "existing-profile"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up_files: list[str] = []
    for filename in PROJECT_PROFILE_FILES:
        path = ACTIVE_PROJECT_DIR / filename
        if path.exists():
            backup_path = backup_dir / filename
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            backed_up_files.append(str(backup_path))

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
                f"Existing profile backup: {backup_dir}\n"
                f"Prompt saved: {run_dir / 'prompt.md'}"
            ),
            "run_dir": str(run_dir),
            "updated_files": [],
            "backed_up_files": backed_up_files,
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
        "backed_up_files": backed_up_files,
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
            f"Existing profile backup: {backup_dir}\n"
            "Updated files:\n"
            + "\n".join(f"- {path}" for path in updated_files)
            + f"\n\nRun saved: {run_dir}"
        ),
        "run_dir": str(run_dir),
        "updated_files": updated_files,
        "backed_up_files": backed_up_files,
    }


def default_results_context() -> list[str]:
    results_dir = ACTIVE_PROJECT_DIR / "results"
    return [str(results_dir)]


def should_include_result_file(path: Path) -> bool:
    if path.name == ".gitkeep":
        return False
    if any(part in CONTEXT_EXCLUDE_DIRS for part in path.parts):
        return False
    return path.suffix.lower() in RESULT_INVENTORY_SUFFIXES


def resolve_result_paths(paths: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    missing: list[str] = []
    for raw in paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not path.exists():
            missing.append(str(path))
            continue
        if path.is_file():
            if should_include_result_file(path):
                files.append(path)
            continue
        if not path.is_dir():
            continue
        for candidate in sorted(path.rglob("*")):
            if len(files) >= MAX_RESULT_FILES:
                break
            if candidate.is_file() and should_include_result_file(candidate):
                files.append(candidate)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique, missing


def result_artifact_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "figure"
    if suffix == ".pdf":
        return "document"
    if suffix in {".pt", ".pth", ".pkl", ".npy", ".npz"}:
        return "model-or-array"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if suffix in {".json", ".jsonl", ".yaml", ".yml"}:
        return "structured-data"
    if suffix in {".txt", ".log", ".md"}:
        return "text-log"
    return "artifact"


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def metric_record(name: str, value: object, source: str, note: str = "") -> dict[str, object]:
    return {
        "name": re.sub(r"\s+", " ", name).strip(),
        "value": value,
        "source": source,
        "note": note,
    }


def collect_json_metrics(value: object, prefix: str = "") -> list[dict[str, object]]:
    metrics: list[dict[str, object]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if is_number(child) and RESULT_METRIC_NAME_RE.search(name):
                metrics.append(metric_record(name, child, "json"))
            elif isinstance(child, (dict, list)):
                metrics.extend(collect_json_metrics(child, name))
            if len(metrics) >= MAX_RESULT_METRICS_PER_FILE:
                break
    elif isinstance(value, list):
        for index, child in enumerate(value[:50]):
            name = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if isinstance(child, (dict, list)):
                metrics.extend(collect_json_metrics(child, name))
            if len(metrics) >= MAX_RESULT_METRICS_PER_FILE:
                break
    return metrics[:MAX_RESULT_METRICS_PER_FILE]


def parse_json_metrics(text: str, suffix: str) -> tuple[list[dict[str, object]], str]:
    metrics: list[dict[str, object]] = []
    try:
        if suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()][:50]
            metrics = collect_json_metrics(rows)
            return metrics, f"jsonl rows inspected: {len(rows)}"
        data = json.loads(text)
        metrics = collect_json_metrics(data)
        return metrics, "json parsed"
    except json.JSONDecodeError as exc:
        return [], f"json parse failed: {exc.msg}"


def parse_delimited_metrics(text: str, suffix: str) -> tuple[list[dict[str, object]], str]:
    delimiter = "\t" if suffix == ".tsv" else ","
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        dialect.delimiter = delimiter
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for index, row in enumerate(reader):
        if index >= 500:
            break
        rows.append(row)
    if not rows:
        return [], "no data rows detected"
    metrics: list[dict[str, object]] = []
    for field in reader.fieldnames or []:
        if not field or not RESULT_METRIC_NAME_RE.search(field):
            continue
        numeric_values: list[float] = []
        for row in rows:
            raw = (row.get(field) or "").strip()
            try:
                numeric_values.append(float(raw))
            except ValueError:
                continue
        if numeric_values:
            metrics.append(
                metric_record(
                    field,
                    numeric_values[-1],
                    "csv-last",
                    f"rows={len(rows)}, min={min(numeric_values):.6g}, max={max(numeric_values):.6g}",
                )
            )
        if len(metrics) >= MAX_RESULT_METRICS_PER_FILE:
            break
    return metrics, f"rows inspected: {len(rows)}"


def parse_text_metrics(text: str) -> tuple[list[dict[str, object]], str]:
    metrics: list[dict[str, object]] = []
    for match in RESULT_METRIC_PAIR_RE.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
        if not RESULT_METRIC_NAME_RE.search(name):
            continue
        try:
            value: object = float(match.group(2))
        except ValueError:
            value = match.group(2)
        metrics.append(metric_record(name, value, "text"))
        if len(metrics) >= MAX_RESULT_METRICS_PER_FILE:
            break
    command_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(python|python3|conda|bash|sh|uv|pip|CUDA_VISIBLE_DEVICES=|torchrun)\b", stripped):
            command_lines.append(stripped)
        if len(command_lines) >= 5:
            break
    note = "metric-like key/value pairs detected"
    if command_lines:
        note += f"; command candidates: {' | '.join(command_lines)}"
    return metrics, note


def inspect_result_file(path: Path, roots: list[Path]) -> dict[str, object]:
    suffix = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    entry: dict[str, object] = {
        "path": str(path),
        "relative_path": relative_to_any(path, roots),
        "type": result_artifact_type(path),
        "suffix": suffix,
        "bytes": size,
        "metrics": [],
        "notes": [],
    }
    if suffix not in RESULT_TEXT_SUFFIXES:
        entry["notes"] = ["listed only; binary or non-text artifact"]
        return entry
    if size > MAX_RESULT_FILE_BYTES:
        entry["notes"] = [f"skipped text parsing because file exceeds {MAX_RESULT_FILE_BYTES} bytes"]
        return entry
    text = read_text_limited(path, MAX_RESULT_FILE_BYTES)
    if suffix in {".json", ".jsonl"}:
        metrics, note = parse_json_metrics(text, suffix)
    elif suffix in {".csv", ".tsv"}:
        metrics, note = parse_delimited_metrics(text, suffix)
    else:
        metrics, note = parse_text_metrics(text)
    entry["metrics"] = metrics
    entry["notes"] = [note] if note else []
    if not metrics and suffix in RESULT_TEXT_SUFFIXES:
        entry["notes"].append("no obvious metric-like fields detected")
    return entry


def build_results_inventory(context: list[str] | None = None) -> dict[str, object]:
    context_paths = context or default_results_context()
    roots = []
    for raw in context_paths:
        path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        roots.append(path if path.is_dir() else path.parent)
    files, missing = resolve_result_paths(context_paths)
    artifacts = [inspect_result_file(path, roots) for path in files]
    metric_count = sum(len(item.get("metrics") or []) for item in artifacts)
    return {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "roots": [str(root) for root in roots],
        "missing_roots": missing,
        "artifact_count": len(artifacts),
        "metric_count": metric_count,
        "truncated": len(files) >= MAX_RESULT_FILES,
        "limits": {
            "max_result_files": MAX_RESULT_FILES,
            "max_result_file_bytes": MAX_RESULT_FILE_BYTES,
            "max_metrics_per_file": MAX_RESULT_METRICS_PER_FILE,
        },
        "artifacts": artifacts,
        "scope_note": (
            "This inventory records observable result artifacts and metric-like values only. "
            "It does not judge whether a paper claim is supported."
        ),
    }


def render_results_inventory_markdown(inventory: dict[str, object]) -> str:
    lines = [
        RESULT_INVENTORY_START,
        "## Auto Result Inventory",
        "",
        f"- Generated at: `{inventory.get('created_at')}`",
        f"- Artifact count: {inventory.get('artifact_count', 0)}",
        f"- Metric-like values detected: {inventory.get('metric_count', 0)}",
        f"- Scope: {inventory.get('scope_note')}",
        "",
    ]
    missing = inventory.get("missing_roots") or []
    if missing:
        lines.append("### Missing Result Roots")
        lines.append("")
        for path in missing:
            lines.append(f"- `{path}`")
        lines.append("")
    artifacts = inventory.get("artifacts") or []
    if not artifacts:
        lines.extend(
            [
                "### Observed Artifacts",
                "",
                "- No result artifacts were detected. Only files such as `.gitkeep` may be present, or the configured results path is empty.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "### Observed Artifacts",
                "",
                "| Path | Type | Size | Detected Content | Notes |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for raw in artifacts:
            item = raw if isinstance(raw, dict) else {}
            metrics = item.get("metrics") or []
            detected = "no metric-like values"
            if metrics:
                detected = "; ".join(f"{metric.get('name')}={metric.get('value')}" for metric in metrics[:6])
                if len(metrics) > 6:
                    detected += f"; ... {len(metrics) - 6} more"
            notes = "; ".join(str(note) for note in item.get("notes") or [])
            lines.append(
                f"| `{item.get('relative_path')}` | {item.get('type')} | {item.get('bytes')} | {detected} | {notes} |"
            )
        lines.append("")
    lines.extend(
        [
            "### Evidence Use Rules",
            "",
            "- Treat these rows as raw artifact facts, not validated thesis claims.",
            "- A metric-like value supports a claim only after matching the exact experiment setup, dataset, baseline, and figure/table in the thesis.",
            "- Binary figures and model files are listed for traceability but need human or reviewer interpretation before becoming evidence.",
            RESULT_INVENTORY_END,
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def replace_managed_section(existing: str, section: str) -> str:
    if RESULT_INVENTORY_START in existing and RESULT_INVENTORY_END in existing:
        pattern = re.compile(
            re.escape(RESULT_INVENTORY_START) + r".*?" + re.escape(RESULT_INVENTORY_END),
            re.DOTALL,
        )
        return pattern.sub(section.strip(), existing).rstrip() + "\n"
    return existing.rstrip() + "\n\n" + section.strip() + "\n"


def update_experiments_with_inventory(markdown: str) -> tuple[Path, str]:
    path = ACTIVE_PROJECT_DIR / "experiments.md"
    existing = optional_text(path)
    if not existing.strip():
        existing = "# Experiments\n\n本文件记录实验计划、运行命令、参数、结果路径和复核状态。\n"
    updated = replace_managed_section(existing, markdown)
    path.write_text(updated, encoding="utf-8")
    return path, updated


def results_inventory_project(context: list[str] | None = None, dry_run: bool = False) -> dict[str, object]:
    inventory = build_results_inventory(context)
    markdown = render_results_inventory_markdown(inventory)
    run_dir = make_run_dir("planner", "results-inventory")
    (run_dir / "results-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "results-inventory.md").write_text(markdown, encoding="utf-8")
    updated_file = None
    backup_path = None
    if not dry_run:
        experiments_path = ACTIVE_PROJECT_DIR / "experiments.md"
        if experiments_path.exists():
            backup_dir = run_dir / "existing-profile"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / "experiments.md"
            backup_path.write_text(experiments_path.read_text(encoding="utf-8"), encoding="utf-8")
        updated_file, _ = update_experiments_with_inventory(markdown)
    meta = {
        "provider": "results-inventory",
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "dry_run": dry_run,
        "artifact_count": inventory.get("artifact_count", 0),
        "metric_count": inventory.get("metric_count", 0),
        "updated_file": str(updated_file) if updated_file else None,
        "backup_path": str(backup_path) if backup_path else None,
    }
    (run_dir / "trace.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    output = (
        "Results inventory complete.\n\n"
        f"Artifact count: {inventory.get('artifact_count', 0)}\n"
        f"Metric-like values: {inventory.get('metric_count', 0)}\n"
        f"Run saved: {run_dir}\n"
    )
    if updated_file:
        output += f"Updated: {updated_file}\n"
    else:
        output += "No project file updated because dry-run was enabled.\n"
    if backup_path:
        output += f"Backup: {backup_path}\n"
    output += "\n" + markdown
    return {
        "provider": "results-inventory",
        "project_dir": str(ACTIVE_PROJECT_DIR),
        "run_dir": str(run_dir),
        "inventory": inventory,
        "updated_file": str(updated_file) if updated_file else None,
        "output": output,
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
    compile_preflight: bool = True,
) -> dict[str, object]:
    if provider == "bootstrap":
        return bootstrap_project(context=context, provider="codex-cli")
    selected_agent = agent or detect_agent_from_message(task)
    selected_skills = infer_skills(task, skill or [], auto_skill)
    if provider == "quality-review":
        return run_quality_review(
            task,
            selected_agent,
            context or [],
            selected_skills,
            deepseek_api_key,
            use_gemini,
            compile_preflight,
        )
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
        compile_preflight=not args.no_compile_preflight,
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


def cmd_results_inventory(args: argparse.Namespace) -> int:
    result = results_inventory_project(
        context=args.context or None,
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
    p_ask.add_argument("--no-compile-preflight", action="store_true", help="Skip latexmk during quality-review preflight")
    p_ask.set_defaults(func=cmd_ask)

    p_bootstrap = sub.add_parser("bootstrap", help="Infer and write active project profile files")
    p_bootstrap.add_argument("--context", "-c", action="append", default=[], help="Source file or folder to inspect")
    p_bootstrap.add_argument("--provider", choices=["codex-cli", "echo"], default="codex-cli")
    p_bootstrap.add_argument("--dry-run", action="store_true", help="Write only the bootstrap prompt, not project files")
    p_bootstrap.set_defaults(func=cmd_bootstrap)

    p_results = sub.add_parser("results-inventory", help="Scan result artifacts and update experiments.md")
    p_results.add_argument("--context", "-c", action="append", default=[], help="Result file or folder to inspect")
    p_results.add_argument("--dry-run", action="store_true", help="Write inventory output only, without updating experiments.md")
    p_results.set_defaults(func=cmd_results_inventory)

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
