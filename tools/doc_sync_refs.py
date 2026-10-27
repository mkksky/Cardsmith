#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/doc_sync_refs.py —— 从 markdown 文本里抽「疑似引用」

被 tools/check_doc_sync.py 引用。

为什么独立成文件（2026-10-18，size-naming 门禁拦下 510 行后按职责拆）：
    这一段是纯函数 + 正则常量，不碰磁盘、不读规则结构，职责单一：
    「文本进、候选引用出」。它与主文件的 I/O、基线、报告输出互不依赖，
    是天然的分界。拆开后主文件 441 行，两侧都远在 500 上限内。

抽出来还有一处好处：这里的取舍（裸文件名默认不查、code span 才算引用）
需要被单独 review，混在 500 行里容易被略过。

取舍说明：
    - 只认 code span（`x`）与 markdown 链接 —— 散文里的裸词不算引用，
      否则「run the checker」这种句子会被当成路径
    - 裸文件名默认不查（check_bare_names 才开）：散文里「check.py 会报错」
      多为泛指而非引用，首次真数据验证过这个取舍
"""

from __future__ import annotations

import re

from doc_sync_paths import is_allowed_ref

# code span：反引号包裹且不跨行。跨行的 ```` 代码块不当作引用来源
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# markdown 链接：[text](target)
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# `name(...)` 形态的方法引用，名字必须是合法标识符
_CALL_REF = re.compile(r"^([A-Za-z_]\w*)\(")
# 尾部标点：路径后面常跟句读，需要剥掉
_PUNCT = " \t.,;:!?，。；：！？）)】》\"'`"
# URL scheme：带这些前缀的是链接不是仓库内路径
_SCHEME = ("http://", "https://", "ftp://", "mailto:", "data:", "file://", "cloud://")


def _clean(token: str) -> str:
    """剥掉 code span 里混进来的句读与锚点，返回干净的疑似路径。

    注意不能对整串做 strip(_PUNCT)：前导的 "." 会被吃掉，./guide.md 变 /guide.md
    （自检抓过）。前导只剥空白和引号类标点，保留 . / 以识别相对路径。
    """
    tok = token.strip()
    tok = tok.lstrip(" \t,;:!?，。；：！？）)】》\"'`").rstrip(_PUNCT)
    if tok.startswith("./"):
        tok = tok[2:]
    anchor = tok.find("#")
    if anchor != -1 and "/" in tok:  # docs/x.md#section → docs/x.md
        tok = tok[:anchor]
    return tok.rstrip(_PUNCT).strip()


def _is_pathlike(tok: str, rules: dict) -> bool:
    if not tok or " " in tok:
        return False
    if tok.lower().startswith(_SCHEME):
        return False
    if tok.startswith("~") or tok.startswith("$"):
        return False
    if any(ch in tok for ch in "*<>{}|\"'`"):
        return False
    if "/" in tok:
        return True
    # 裸文件名（无斜杠）：散文里 "check.py 会报错" 多为泛指，默认不查，
    # check_bare_names: true 才查 —— 首轮真数据验证过的取舍（自检有覆盖）
    suffixes = tuple(s.lower() for s in rules["path_suffixes"])
    return bool(rules.get("check_bare_names")) and tok.lower().endswith(suffixes)


def extract_path_refs(text: str, rules: dict) -> "list[str]":
    """从 markdown 文本抽疑似路径引用（code span + markdown 链接），去重保序。"""
    seen, refs = set(), []
    tokens = [m.group(1) for m in _CODE_SPAN.finditer(text)]
    tokens += [m.group(1) for m in _MD_LINK.finditer(text)]
    for token in tokens:
        tok = _clean(token)
        if not _is_pathlike(tok, rules):
            continue
        if is_allowed_ref(tok, rules):  # 精确白名单 + 形态白名单（单位/数字比/证据目录）
            continue
        if tok not in seen:
            seen.add(tok)
            refs.append(tok)
    return refs


def extract_call_refs(text: str, rules: dict) -> "list[str]":
    """从 code span 里抽 `name(...)` 形态的方法引用。"""
    seen, names = set(), []
    for m in _CODE_SPAN.finditer(text):
        cm = _CALL_REF.match(m.group(1).strip())
        if not cm:
            continue
        name = cm.group(1)
        if name in rules.get("skip_symbols", []):
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names