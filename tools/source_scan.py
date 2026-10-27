#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/source_scan.py —— 「这次要检查哪些文件」

从 check_import_direction.py 拆出来的第二块：暂存区收集、全量扫描、路径豁免，
以及基线键的定义。和「怎么比对」分开 —— 这边决定范围，那边决定结论。

为什么单拆：主工具在 0.10.3 加 project_packages 后到 519 行，超了套件自己的
500 行上限（被 size-naming 门禁当场拦下）。先拆了规则与层判定（layer_spec.py），
还差 19 行；这块与「判定方向对不对」无关，只负责找文件，是第二个自然的断点。

顺手留个记录：`Get-Content ... | .Count` 在 Windows PowerShell 5.1 下按 GBK
读 UTF-8 文件，会把带中文的行数错（曾数出 489，真实 519）—— 量行数别用它，
用 python 或 gc -Encoding UTF8。

被谁使用：
    tools/check_import_direction.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from layer_spec import RuleError, glob_to_regex

TIMEOUT_GIT = 30


def git_staged_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            # core.quotePath=false：默认会把中文路径转义成 "\345\260..." 这类字面串，
            # 路径解析不到文件 → 检查静默通过（2026-10-10 修）
            [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=ACMR",
            ],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    out = proc.stdout.decode("utf-8", errors="replace")
    return [line.strip() for line in out.splitlines() if line.strip()]


def all_source_files(root: Path) -> list[str]:
    out = []
    for ext in ("*.py", "*.java"):
        for f in root.rglob(ext):
            rel = f.relative_to(root).as_posix()
            if "/.git/" in f"/{rel}" or rel.startswith(".git/"):
                continue
            out.append(rel)
    return sorted(out)


def is_skipped(rel: str, skip_patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


# 基线（棘轮）的存取逻辑在 tools/baseline_store.py，几个检查工具共用。
# 本工具只定义自己的键：文件 + 被导入模块，不含行号（行号随编辑漂移，不能当键）。


def violation_key(v: dict) -> str:
    return f"{v['file']}:{v['module']}"
