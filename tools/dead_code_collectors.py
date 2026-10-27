#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/dead_code_collectors.py —— 死代码检查的解析层

从 check_dead_code.py 拆出（行数守卫先例同 doc_sync_refs / import_collectors）：
    - require 绑定收集与解构名解析
    - 具名导出收集（module.exports = {...} / exports.x / Class）
    - 全仓库「符号 → 文件集合」倒排索引与展开继承识别

纯函数、无 IO，check_dead_code.py 只做文件收集、检查与基线。
"""

from __future__ import annotations

import re

# ────────────────────────── 解析：require 绑定 ──────────────────────────

_RE_REQUIRE = re.compile(
    r"\b(?:const|let|var)\s+(.+?)\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
)


def _split_top_level(text: str) -> list[str]:
    """按逗号切分，忽略 {} [] () 与引号内的逗号。"""
    parts, buf, depth, quote = [], [], 0, ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'`":
            quote = ch
            buf.append(ch)
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _destructure_names(spec: str) -> list[str]:
    """从解构模式里取绑定名。`{a, b: c, d = 1}` → [a, c, d]；非对象模式返回 []。"""
    spec = spec.strip()
    m = re.match(r"^\{(.*)\}$", spec, re.S)
    if not m:
        return []
    names = []
    for item in _split_top_level(m.group(1)):
        item = item.strip()
        if not item or item.startswith("..."):
            continue
        # 'a: b' 取右侧绑定名；'a = 默认值' 取左侧；先剥默认值再取别名
        if ":" in item:
            key, _, val = item.partition(":")
            val = val.split("=")[0].strip()
            if val:
                names.append(val)
            continue
        names.append(item.split("=")[0].strip())
    return [n for n in names if re.fullmatch(r"[A-Za-z_$][\w$]*", n)]


def collect_require_bindings(src: str) -> list[dict]:
    """收集本文件的 require 绑定：[{name, start, end}]，span 覆盖整条声明。"""
    out = []
    for m in _RE_REQUIRE.finditer(src):
        spec = m.group(1).strip()
        span_start = m.start()
        span_end = m.end()
        # 声明可能跨行（解构体），向后补齐到分号或行尾
        while span_end < len(src) and src[span_end] not in ";\n":
            span_end += 1
        if spec.startswith("{"):
            names = _destructure_names(spec)
        else:
            names = [spec] if re.fullmatch(r"[A-Za-z_$][\w$]*", spec) else []
        for n in names:
            out.append({"name": n, "start": span_start, "end": span_end})
    return out


# ────────────────────────── 解析：具名导出 ──────────────────────────


def _match_brace(src: str, open_idx: int) -> int:
    """返回与 src[open_idx]（必须是 {）配对的 } 下标；找不到返回 -1。"""
    depth, quote = 0, ""
    for i in range(open_idx, len(src)):
        ch = src[i]
        if quote:
            if ch == quote:
                quote = ""
            elif ch == "\\":
                pass
            continue
        if ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def collect_export_names(src: str) -> list[str]:
    """收集模块的具名导出。识别：
    module.exports = { a, b: c, ... }   （花括号配对后按顶层逗号切）
    exports.name = ...  /  module.exports.name = ...
    module.exports = ClassName / identifier（非字面量对象时）
    """
    names = []
    for m in re.finditer(r"module\.exports\s*=", src):
        rest = src[m.end() :].lstrip()
        offset = m.end() + (len(src[m.end() :]) - len(rest))
        if rest.startswith("{"):
            close = _match_brace(src, offset)
            if close == -1:
                continue
            body = src[offset + 1 : close]
            for item in _split_top_level(body):
                item = item.strip()
                if not item or item.startswith("..."):
                    continue
                key = re.match(r"^(['\"]?)([A-Za-z_$][\w$]*)\1\s*:", item)
                if key:
                    names.append(key.group(2))
                    continue
                # 方法简写：gone() {} / async gone() / get foo() / set foo(v)
                method = re.match(
                    r"^(?:async\s+)?(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*\(", item
                )
                if method:
                    names.append(method.group(1))
                    continue
                head = item.split("=")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", head):
                    names.append(head)
            break  # module.exports = {...} 只认第一处（重赋值罕见，误报方向安全）
        else:
            ident = re.match(r"(?:class|function\s*\*?\s*)?([A-Za-z_$][\w$]*)", rest)
            if ident and ident.group(1) not in ("class", "function"):
                names.append(ident.group(1))
    for m in re.finditer(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=", src):
        names.append(m.group(1))
    # 去重保序
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ────────────────────────── 检查 ──────────────────────────


_RE_IDENTIFIER = re.compile(r"\b[A-Za-z_$][\w$]*\b")
_RE_SPREAD_REQUIRE = re.compile(r"\.\.\.\s*require\s*\(\s*['\"]([^'\"]+)['\"]")


def build_ref_index(corpus: "list[tuple[str, str]]") -> tuple[dict, set]:
    """构建倒排索引。返回 (符号 → 文件集合, 被展开继承的模块基名集合)。"""
    index: dict = {}
    spread: set = set()
    for rel, src in corpus:
        for ident in set(_RE_IDENTIFIER.findall(src)):
            index.setdefault(ident, []).append(rel)
        for spec in _RE_SPREAD_REQUIRE.findall(src):
            base = spec.replace("\\", "/").rstrip("/").split("/")[-1]
            base = base.rsplit(".", 1)[0] if "." in base else base
            if base:
                spread.add(base)
    return index, spread
