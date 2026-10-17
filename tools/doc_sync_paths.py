# -*- coding: utf-8 -*-
"""tools/doc_sync_paths.py —— check_doc_sync 的路径解析辅助

独立成文件的原因：主文件撞 size-naming 的 500 行门禁。路径解析
（剥定位后缀 / 路由补扩展名 / cf 文档邻域）是一个独立职责，先例：
doc_sync_selftest.py 同样因 500 行门禁独立。
"""

import re

# 路径尾部的「定位后缀」——查盘前剥掉，报告也按剥完的口径
_LINE_TAIL = re.compile(r":[0-9/,\.\-]+$")  # :64 / :145-172 / :52-57,183-199 / :43/176 / :58-
_SYM_TAIL = re.compile(r":[A-Za-z_][A-Za-z0-9_]*$")  # xxx.js:getClientStatistics
_QUERY_TAIL = re.compile(r"\?.*$")  # pages/x/index?type=factory


def strip_check_suffixes(ref: str) -> str:
    """剥掉路径后跟的行号/符号锚点/查询串，返回纯路径用于查盘与报告。

    `xxx.js:64` 这类「路径+定位」写法必须剥 —— 主文件的 _clean 有意保留
    尾部数字（数字不是句读，防误伤 `./guide.md`），不剥则永远查不到。
    2026-09-28 周检实测：149 处新增违规里 70 处是这个形态。
    """
    out = ref
    for _ in range(3):  # 迭代到不动点，防御 ::64 这类叠写
        new = _QUERY_TAIL.sub("", out)
        new = _LINE_TAIL.sub("", new)
        new = _SYM_TAIL.sub("", new)
        if new == out:
            break
        out = new
    return out


def extra_bases(doc_rel: str, rules: dict) -> "list[str]":
    """按规则 cf_doc_bases 推导该文档的额外引用解析基（相对路径片段）。

    典型用途：docs/05-cloudfunctions/<fn>/ 下的 README 写 `handlers/crud.js`
    这种云函数内相对路径 —— 基 = cloudfunctions/<fn>/ 后即可解析。
    返回的是相对片段，调用方决定拼在项目根还是 source_root 下。
    """
    bases = []
    for spec in rules.get("cf_doc_bases", []):
        doc_prefix = str(spec.get("doc_prefix", ""))
        base_prefix = str(spec.get("base_prefix", ""))
        if not doc_prefix or not base_prefix:
            continue
        rel = doc_rel.replace("\\", "/")
        idx = rel.find(doc_prefix)
        if idx == -1:
            continue
        fn = rel[idx + len(doc_prefix) :].split("/", 1)[0]
        if fn:
            bases.append(base_prefix + fn)
    return bases


def compile_allow_patterns(patterns) -> "list[re.Pattern]":
    """编译形态白名单 allow_ref_patterns；非字符串或坏正则向上抛（TypeError/re.error）。"""
    compiled = []
    for pat in patterns or []:
        if not isinstance(pat, str):
            raise TypeError(f"allow_ref_patterns 必须是字符串数组：{pat!r}")
        compiled.append(re.compile(pat))
    return compiled


def is_allowed_ref(tok: str, rules: dict) -> bool:
    """命中精确白名单 allow_refs 或形态白名单 allow_ref_patterns 即放行。

    形态白名单用于「长得像路径但不是路径」的项目固定写法：价格单位
    （元/㎡、万元/亩）、纯数字比（28/24/19）、不入库的本地证据
    （.local-evidence/）。正则按 fullmatch 裁断，编译结果缓存在 rules 里。
    """
    if tok in rules.get("allow_refs", []):
        return True
    compiled = rules.get("_compiled_allow_patterns")
    if compiled is None:
        compiled = compile_allow_patterns(rules.get("allow_ref_patterns", []))
        rules["_compiled_allow_patterns"] = compiled
    return any(p.fullmatch(tok) for p in compiled)


def candidate_refs(ref: str) -> "list[str]":
    """一个引用的可能形态：原样 + 无扩展名页面路由补 .js。

    小程序文档常写页面路由（`/pages/client/list`），实际文件是
    pages/client/list.js —— 末段无扩展名且路径含 pages/ 时补 .js 探测。
    """
    refs = [ref]
    last = ref.rstrip("/").rsplit("/", 1)[-1]
    if "pages/" in ref and not ref.endswith("/") and "." not in last:
        refs.append(ref.lstrip("/") + ".js")
    return refs
