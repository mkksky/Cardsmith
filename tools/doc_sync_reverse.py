# -*- coding: utf-8 -*-
"""tools/doc_sync_reverse.py —— doc-sync 的反向并入逻辑

独立成文件的原因：主文件撞 size-naming 的 500 行门禁（doc_sync_selftest.py
同一先例）。这里解决 v1 的盲区：pre-commit 只查「暂存文件里的白名单文档」，
提交里只有代码时文档检查完全沉默——哪怕文档引用的路径刚刚被改没了。

反向并入的语义（2026-10-21）：
    暂存的代码路径 → 在白名单文档的 path-ref 里反查谁引用了它 →
    命中的文档并入本次校对（不要求文档被暂存）。
    api_docs 另有一条触发线：暂存文件落在某份 API 文档的 search_roots
    下 → 该文档的签名对账一并执行。

匹配是「宁可多查不可漏查」：多并入一份文档只是多一次检查，代价是几毫秒；
漏掉才是真漂移。所以 endswith 式的尾段匹配是故意的——文档省略
source_roots 前缀（monorepo）或按文档邻域写相对路径（cf_doc_bases）时，
引用串天然比真实暂存路径短，只有尾段匹配能兜住。
"""

from __future__ import annotations

from pathlib import Path

from doc_sync_paths import strip_check_suffixes
from doc_sync_refs import extract_path_refs


def norm_ref(ref: str) -> str:
    """引用串归一：剥前导 / 与 ./，压平反斜杠。"""
    out = ref.replace("\\", "/").lstrip("/").strip()
    while out.startswith("./"):
        out = out[2:]
    return out


def ref_matches_staged(ref: str, staged_rel: str) -> bool:
    """一个文档引用是否命中一个暂存路径。

    三条规则，按序：
      1. 目录引用（ref 以 / 结尾）→ 暂存路径在它下面即命中
      2. 全等
      3. 尾段匹配 —— 兜住文档邻域相对路径（docs/x.md 写 `guide.md`
         实际是 docs/guide.md）与 source_roots 前缀省略（文档写
         `application/a.py` 实际是 pkg/application/a.py）
    """
    r = norm_ref(ref)
    s = staged_rel.replace("\\", "/")
    if not r:
        return False
    if r.endswith("/"):
        return s.startswith(r)
    return s == r or s.endswith("/" + r)


def docs_referencing(
    staged_rels: "list[str]", doc_rels: "list[str]", root: Path, rules: dict
) -> "dict[str, list[str]]":
    """反查：哪些文档的 path-ref 引用了本次暂存的路径。

    返回 {文档: [命中的暂存路径]}，只含有命中的。文档读不了（不存在）
    直接跳过——它本身由 check_path_refs 的缺口逻辑管，不归这里。
    """
    hits: dict = {}
    if not staged_rels:
        return hits
    for doc in doc_rels:
        path = root / doc
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matched: set = set()
        for raw in extract_path_refs(text, rules):
            ref = norm_ref(strip_check_suffixes(raw))
            for s in staged_rels:
                if ref_matches_staged(ref, s):
                    matched.add(s)
        if matched:
            hits[doc] = sorted(matched)
    return hits


def api_docs_triggered(staged_rels: "list[str]", entries: list) -> "list[str]":
    """暂存文件落在某份 API 文档的 search_roots 下 → 该文档应对账。"""
    out: list = []
    for entry in entries:
        doc = str(entry.get("doc", "")).replace("\\", "/")
        if not doc or doc in out:
            continue
        srs = [
            str(s).replace("\\", "/").strip("/")
            for s in entry.get("search_roots", [])
        ]
        for s in staged_rels:
            rel = s.replace("\\", "/")
            if any(sr and (rel == sr or rel.startswith(sr + "/")) for sr in srs):
                out.append(doc)
                break
    return out
