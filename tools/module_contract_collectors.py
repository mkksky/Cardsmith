#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""module_contract_collectors.py —— 模块导出契约的纯解析层

从源码里收集两类事实，不碰文件系统：

    1. 解构 require 请求了哪些源导出名
    2. 目标模块能否被静态判定为“完整导出面”，以及包含哪些具名导出

解析只面向 CommonJS。无法确定的动态导出返回 complete=False，调用方必须跳过，
不能把“看不懂”误报成“没有导出”。
"""

from __future__ import annotations

import re

_REQUIRE_DESTRUCTURE = re.compile(
    r"(?:const|let|var)\s*\{([^{}]*)\}\s*=\s*require\s*\(\s*(['\"])([^'\"]+)\2\s*\)"
)
_RE_EXPORT_ASSIGN = re.compile(r"module\.exports\s*=")
_RE_EXPORT_PROPERTY = re.compile(r"(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=")
_REGEX_PREFIX_CHARS = set("([{:;,=!?&|+-*%^~<>")
_REGEX_PREFIX_WORDS = {
    "await",
    "case",
    "delete",
    "in",
    "instanceof",
    "of",
    "return",
    "throw",
    "typeof",
    "void",
    "yield",
}


def _starts_regex(source: str, index: int) -> bool:
    """判断 `/` 是否处于 JavaScript 正则字面量的起始位置。

    只回看 `/` 左边的标识符尾巴，不切片整段前缀：
    旧实现每次 `re.search(r"([A-Za-z_$][\\w$]*)$", source[:index])`，
    长行上是 O(n²) 级别的重复扫描，实测 425 个文件累计 50s，钩子必然超时。
    """
    j = index - 1
    while j >= 0 and source[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    if source[j] in _REGEX_PREFIX_CHARS:
        return True
    end = j + 1
    start = end
    while start > 0:
        ch = source[start - 1]
        if ch.isascii() and (ch.isalnum() or ch in "_$"):
            start -= 1
        else:
            break
    return source[start:end] in _REGEX_PREFIX_WORDS


def _regex_end(source: str, index: int) -> int:
    """返回正则字面量结束 `/` 的下标；找不到返回 -1。"""
    escaped = False
    in_class = False
    for pos in range(index + 1, len(source)):
        ch = source[pos]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return pos
        elif ch == "\n":
            return -1
    return -1


def strip_comments(source: str) -> str:
    """移除注释并用空格占位，保留字符串内容和原始偏移。"""
    out = []
    quote = ""
    escaped = False
    block = False
    line = False
    i = 0
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1 : i + 2]
        if block:
            if ch == "*" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                block = False
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if line:
            if ch == "\n":
                out.append("\n")
                line = False
            else:
                out.append(" ")
            i += 1
            continue
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out.extend((" ", " "))
            i += 2
            line = True
            continue
        if ch == "/" and nxt == "*":
            out.extend((" ", " "))
            i += 2
            block = True
            continue
        if ch == "/" and _starts_regex(source, i):
            end = _regex_end(source, i)
            if end >= 0:
                out.append(source[i : end + 1])
                i = end + 1
                continue
        if ch in "'\"`":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out)


def mask_non_code(source: str) -> str:
    """把字符串和注释内容替换成空格，保留偏移，用于确认匹配发生在代码区。"""
    out = []
    quote = ""
    escaped = False
    block = False
    line = False
    i = 0
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1 : i + 2]
        if block:
            if ch == "*" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                block = False
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if line:
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                line = False
            i += 1
            continue
        if quote:
            out.append(" ")
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch == "/" and nxt == "/":
            out.extend((" ", " "))
            i += 2
            line = True
            continue
        if ch == "/" and nxt == "*":
            out.extend((" ", " "))
            i += 2
            block = True
            continue
        if ch == "/" and _starts_regex(source, i):
            end = _regex_end(source, i)
            if end >= 0:
                out.extend(" " for _ in range(end - i + 1))
                i = end + 1
                continue
        if ch in "'\"`":
            out.append(" ")
            quote = ch
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _is_code(masked: str, index: int) -> bool:
    return index < len(masked) and masked[index] not in " \t\r\n"


def _split_top_level(text: str) -> list[str]:
    parts = []
    buf = []
    depth = 0
    quote = ""
    escaped = False
    for ch in text:
        if quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _requested_names(pattern: str) -> list[str]:
    names = []
    for item in _split_top_level(pattern):
        if not item or item.startswith("..."):
            continue
        quoted = re.match(r"^(['\"])([^'\"]+)\1\s*(?::|=|$)", item)
        if quoted:
            names.append(quoted.group(2))
            continue
        ident = re.match(r"^([A-Za-z_$][\w$]*)", item)
        if ident:
            names.append(ident.group(1))
    return names


def collect_destructured_requires(source: str) -> list[dict]:
    """收集解构 require：[{specifier, names, line}]。"""
    masked = mask_non_code(source)
    out = []
    for match in _REQUIRE_DESTRUCTURE.finditer(source):
        if not _is_code(masked, match.start()):
            continue
        out.append(
            {
                "specifier": match.group(3),
                "names": _requested_names(match.group(1)),
                "line": source.count("\n", 0, match.start()) + 1,
            }
        )
    return out


def _find_matching_brace(source: str, open_index: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_index, len(source)):
        ch = source[index]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def collect_object_members(body: str) -> dict:
    """解析对象字面量顶层成员，返回 names / complete / spreads。"""
    names = []
    spreads = []
    complete = True
    for item in _split_top_level(body):
        if not item:
            continue
        if item.startswith("..."):
            spread = re.match(r"^\.\.\.\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", item)
            if spread:
                spreads.append(spread.group(1))
            else:
                complete = False
            continue
        if item.startswith("["):
            complete = False
            continue
        quoted = re.match(r"^(['\"])([^'\"]+)\1\s*:", item)
        if quoted:
            names.append(quoted.group(2))
            continue
        key = re.match(r"^([A-Za-z_$][\w$]*)\s*:", item)
        if key:
            names.append(key.group(1))
            continue
        method = re.match(
            r"^(?:async\s+)?(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*\(", item
        )
        if method:
            names.append(method.group(1))
            continue
        shorthand = re.match(r"^([A-Za-z_$][\w$]*)\s*(?:=|$)", item)
        if shorthand:
            names.append(shorthand.group(1))
            continue
        complete = False
    return {"names": names, "complete": complete, "spreads": spreads}


def collect_variable_object(source: str, identifier: str) -> "dict | None":
    """找顶层 `const|let|var <identifier> = {...}` 并解析对象成员。"""
    cleaned = strip_comments(source)
    masked = mask_non_code(cleaned)
    pattern = re.compile(rf"\b(?:const|let|var)\s+{re.escape(identifier)}\s*=\s*\{{")
    for match in pattern.finditer(cleaned):
        if not _is_code(masked, match.start()):
            continue
        open_index = cleaned.find("{", match.start())
        close_index = _find_matching_brace(cleaned, open_index)
        if close_index < 0:
            return None
        result = collect_object_members(cleaned[open_index + 1 : close_index])
        dynamic = re.search(
            rf"\b{re.escape(identifier)}\s*\[|\bObject\.assign\s*\(\s*{re.escape(identifier)}\b",
            cleaned,
        )
        if dynamic:
            result["complete"] = False
        return result
    return None


def collect_export_summary(source: str) -> dict:
    """返回目标模块的静态导出摘要。

    kind:
      empty      没有 module.exports，可能只有 exports.x
      object     module.exports = {...}
      identifier module.exports = exportObject
      require    module.exports = require('./x')
      dynamic    无法静态判定
    """
    cleaned = strip_comments(source)
    masked = mask_non_code(cleaned)
    names = []
    for match in _RE_EXPORT_PROPERTY.finditer(cleaned):
        if _is_code(masked, match.start()):
            names.append(match.group(1))

    assignments = [
        match
        for match in _RE_EXPORT_ASSIGN.finditer(cleaned)
        if _is_code(masked, match.start())
    ]
    if not assignments:
        dynamic = re.search(
            r"\b(?:Object\.defineProperty|Object\.assign)\s*\(\s*exports\b|\bexports\s*\[",
            cleaned,
        )
        if dynamic:
            return {"kind": "dynamic", "names": names, "complete": False, "spreads": []}
        return {"kind": "empty", "names": names, "complete": True, "spreads": []}

    assignment = assignments[-1]
    rest_index = assignment.end()
    while rest_index < len(cleaned) and cleaned[rest_index].isspace():
        rest_index += 1
    rest = cleaned[rest_index:]

    if rest.startswith("{"):
        close_index = _find_matching_brace(cleaned, rest_index)
        if close_index < 0:
            return {"kind": "dynamic", "names": names, "complete": False, "spreads": []}
        members = collect_object_members(cleaned[rest_index + 1 : close_index])
        return {
            "kind": "object",
            "names": names + members["names"],
            "complete": members["complete"],
            "spreads": members["spreads"],
        }

    reexport = re.match(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", rest)
    if reexport:
        return {
            "kind": "require",
            "names": names,
            "complete": True,
            "spreads": [reexport.group(1)],
        }

    ident = re.match(r"([A-Za-z_$][\w$]*)", rest)
    if ident:
        return {
            "kind": "identifier",
            "identifier": ident.group(1),
            "names": names,
            "complete": True,
            "spreads": [],
        }

    return {"kind": "dynamic", "names": names, "complete": False, "spreads": []}
