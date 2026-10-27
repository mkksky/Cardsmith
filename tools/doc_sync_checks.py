#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/doc_sync_checks.py —— doc-sync 的三类检查本体

从 check_doc_sync.py 拆出来的原因：主文件撞 size-naming 的 500 行门禁，
再塞进反向并入的编排逻辑必然超线。这里只放「文档 → 事实」的三类检查：

    1. path-ref   文档里引用的文件/目录在磁盘上不存在（路径时效性）
    2. api-ref    文档声明的方法/类在源码 AST 里不存在（签名漂移，v1 只查名字）
    3. new-file-doc-missing
                  可选映射要求新增源码至少被一份指定文档引用

与 IO 编排（CLI、暂存、反向并入）分离：编排逻辑怎么变，检查语义不动。
"""

from __future__ import annotations

import ast
from pathlib import Path

from doc_sync_paths import (
    candidate_refs,
    extra_bases,
    glob_to_regex,
    is_skipped,
    strip_check_suffixes,
)
from doc_sync_refs import extract_call_refs, extract_path_refs
from doc_sync_reverse import norm_ref, ref_matches_staged


def collect_doc_files(root: Path, rules: dict) -> "list[str]":
    """按 doc_roots 白名单收集要查的文档，返回相对项目根的 posix 路径。"""
    exts = {e.lower() for e in rules["doc_extensions"]}
    out = set()
    for entry in rules["doc_roots"]:
        base = root / entry
        if base.is_file():
            out.add(Path(entry).as_posix())
        elif base.is_dir():
            for f in base.rglob("*"):
                if f.is_file() and f.suffix.lower() in exts:
                    rel = f.relative_to(root).as_posix()
                    if "/.git/" not in f"/{rel}" and "/__pycache__/" not in f"/{rel}":
                        out.add(rel)
        # 既不是文件也不是目录 → 声明有误，留着让用户发现（不静默）
    return sorted(out)


def _exists_somewhere(doc_rel: str, ref: str, root: Path, rules: dict) -> bool:
    """引用在 任一文档邻域 / 项目根 / source_roots / cf 邻域下存在 即通过。

    前导 `/` 的引用按项目根相对处理（小程序路由写法），不能用 pathlib
    直接 join —— Path('a') / '/b' 会得到绝对路径 /b，把前缀全丢掉。
    cf 邻域基（extra_bases）同时按「项目根相对」和「source_root 相对」
    两种布局探测 —— 扁平仓前者命中，monorepo（源码在子目录）后者命中。
    """
    ref = ref.lstrip("/")
    doc_dir = (root / doc_rel).parent
    srs = list(rules.get("source_roots", []))
    extras = extra_bases(doc_rel, rules)
    base_dirs = [doc_dir, root]
    base_dirs += [root / sr for sr in srs]
    base_dirs += [root / b for b in extras]
    base_dirs += [root / sr / b for sr in srs for b in extras]
    candidates = [d / r for d in base_dirs for r in candidate_refs(ref)]
    return any(c.exists() for c in candidates)


def check_path_refs(doc_rel: str, root: Path, rules: dict) -> "list[dict]":
    """查一份文档里的路径引用是否都还在磁盘上。"""
    path = root / doc_rel
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    issues = []
    seen = set()
    for raw in extract_path_refs(text, rules):
        ref = strip_check_suffixes(raw)
        if not ref or ref in seen:  # foo.js:1 与 foo.js:2 剥完同径，只报一次
            continue
        seen.add(ref)
        if not _exists_somewhere(doc_rel, ref, root, rules):
            issues.append(
                {
                    "kind": "path-ref",
                    "file": doc_rel,
                    "ref": ref,
                    "why": "文档引用的路径在磁盘上不存在（项目根与 source_roots 都没有）",
                }
            )
    return issues


def _string_list(value) -> "list[str]":
    """规则里的单字符串或字符串数组统一成去空列表。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).replace("\\", "/").strip() for item in value if str(item).strip()]


def _doc_references_file(doc_rel: str, source_rel: str, root: Path, rules: dict) -> bool:
    """指定文档是否用 path-ref 引用了新增源码路径（支持目录引用）。"""
    path = root / doc_rel
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in extract_path_refs(text, rules):
        ref = norm_ref(strip_check_suffixes(raw))
        if ref_matches_staged(ref, source_rel):
            return True
    return False


def check_new_file_docs(
    new_rels: "list[str]", root: Path, rules: dict
) -> "list[dict]":
    """按项目可选的 new_file_docs 规则检查新增源码是否在文档中登记。

    规则默认空，因此通用安装对存量项目零影响。每条规则要求：匹配 source
    glob 的新增路径，至少被 docs 中一份文档用 path-ref 引用。目录级引用
    （如 ``src/modules/``）视为覆盖其下文件。
    """
    issues: list[dict] = []
    if not new_rels:
        return issues

    for entry in rules.get("new_file_docs", []):
        source_patterns = _string_list(entry.get("source"))
        docs = _string_list(entry.get("docs"))
        if not source_patterns or not docs:
            continue
        matchers = [glob_to_regex(pattern) for pattern in source_patterns]
        for rel in new_rels:
            source_rel = rel.replace("\\", "/")
            if is_skipped(source_rel, rules.get("skip_paths", [])):
                continue
            if not any(matcher.match(source_rel) for matcher in matchers):
                continue
            if any(
                _doc_references_file(doc, source_rel, root, rules) for doc in docs
            ):
                continue
            missing = [doc for doc in docs if not (root / doc).is_file()]
            if missing and len(missing) == len(docs):
                reason = f"规则指定的文档不存在：{', '.join(missing)}"
            elif missing:
                reason = (
                    f"新增文件未被现有文档引用（另缺文档：{', '.join(missing)}）："
                    f"{', '.join(doc for doc in docs if doc not in missing)}"
                )
            else:
                reason = f"新增文件未被指定文档引用：{', '.join(docs)}"
            issues.append(
                {
                    "kind": "new-file-doc-missing",
                    "file": source_rel,
                    "ref": ", ".join(docs),
                    "why": f"{reason}（new_file_docs 要求新增源码有文档落点）",
                }
            )
    return issues


def collect_defined_symbols(root: Path, search_roots: "list[str]") -> set:
    """AST 收集 search_roots 下所有 .py 定义的类名/函数名/方法名。"""
    names: set = set()
    for sr in search_roots:
        base = root / sr
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    names.add(node.name)
    return names


def check_api_doc(entry: dict, root: Path, rules: dict) -> "list[dict]":
    """把 API 文档里声明的方法/类与源码 AST 对账。"""
    doc_rel = str(entry.get("doc", "")).replace("\\", "/")
    if not doc_rel:
        return []
    doc_path = root / doc_rel
    issues: list = []
    if not doc_path.is_file():
        issues.append(
            {
                "kind": "api-doc-missing",
                "file": doc_rel,
                "ref": "(整个文件)",
                "why": "规则声明了这份 API 文档，但文件不存在",
            }
        )
        return issues
    search_roots = [str(s).replace("\\", "/") for s in entry.get("search_roots", [])]
    if not search_roots:
        return []
    defined = collect_defined_symbols(root, search_roots)
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    for name in extract_call_refs(text, rules):
        if name not in defined:
            issues.append(
                {
                    "kind": "api-ref",
                    "file": doc_rel,
                    "ref": name,
                    "why": f"文档声明的方法/类 {name} 在 search_roots 的源码里不存在（改代码没同步文档）",
                }
            )
    return issues
