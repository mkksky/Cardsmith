#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yamlmini —— 严格 YAML 子集解析器（零依赖，纯标准库）

为什么不用 pyyaml：
    这套件要求「环境没装好也不能失效」，且卡片是唯一真源，
    解析行为必须完全可预测。所以自带一个子集解析器。

支持
    - 映射、序列、任意层嵌套（缩进 2 空格起）
    - 标量：字符串 / 整数 / 浮点 / true / false / null / ~
    - 引号：'single' 与 "double"，引号内的 # 不当注释
    - 注释：# 前必须有空白才是注释（YAML 原规则）
    - 块标量：| 字面块 / > 折叠块（可带 - 或 + 后缀）
    - 序列项是映射：- key: value 后跟对齐的键

不支持（遇到会报错，绝不静默忽略）
    - tab 缩进
    - 流式集合：[a, b] / {a: b}
    - 锚点与别名：&anchor / *alias
    - 多文档：---
    - 复杂键：? key

设计原则
    宁可报错，不可猜。凡是不在本子集里的语法，一律抛 YamlError（带行号与原文）。
    「静默忽略」正是配置漂移的根源 —— 卡片说了三件事，解析器只看到两件，
    剩下那件就永远不会被校验。

自检
    python scripts/yamlmini.py          # 跑内置用例，全绿才算可用
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


class YamlError(Exception):
    """解析失败。带行号与原文，便于直接定位到卡片里那一行。"""

    def __init__(self, message: str, lineno: int = 0, raw: str = ""):
        self.lineno = lineno
        self.raw = raw
        detail = f"第 {lineno} 行：{message}"
        if raw:
            detail += f"\n    原文：{raw}"
        super().__init__(detail)


class _Line:
    __slots__ = ("no", "indent", "text", "raw", "stripped", "blank")

    def __init__(self, no: int, indent: int, text: str, raw: str, blank: bool):
        self.no = no
        self.indent = indent
        self.text = text        # 剥掉本级缩进后的内容，结构判断用它
        self.raw = raw          # 原始整行，错误信息与块标量取内容用它
        self.stripped = text.strip()
        self.blank = blank

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Line(no={self.no}, indent={self.indent}, text={self.text!r})"


# 简单键：字母/数字/下划线/连字符，或含空格的短字符串（我们只取第一个冒号切分）
_KEY_RE = re.compile(r"^[^:'\"\s][^:'\"]*$")


def _strip_comment(s: str) -> str:
    """剥掉行尾注释。# 前必须有空白（YAML 规则），引号内的 # 不剥。"""
    in_single = in_double = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or s[i - 1] in " \t":
                return s[:i]
    return s


def _preprocess(text: str) -> list[_Line]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]

    out: list[_Line] = []
    for i, raw in enumerate(text.split("\n"), start=1):
        stripped = raw.strip()
        if not stripped:
            out.append(_Line(i, 0, "", raw, True))
            continue
        if stripped.startswith("#"):
            out.append(_Line(i, 0, "", raw, True))
            continue

        # 禁止 tab 缩进 —— 混用 tab 会让缩进层级无法判断，必须硬失败
        lead = raw[: len(raw) - len(raw.lstrip(" \t"))]
        if "\t" in lead:
            raise YamlError("缩进中禁止使用 tab，请改用空格", i, raw)

        indent = len(raw) - len(raw.lstrip(" "))
        content = raw[indent:]
        _is_unsupported(content, i, raw)
        out.append(_Line(i, indent, content, raw, False))
    return out


_UNSUPPORTED = (
    (re.compile(r"^\["), "不支持流式序列 [a, b]，请改用 - 逐行书写"),
    (re.compile(r"^\{"), "不支持流式映射 {a: b}，请改用缩进书写"),
    (re.compile(r"^&"), "不支持锚点 &anchor"),
    (re.compile(r"^\*"), "不支持别名 *alias"),
    (re.compile(r"^---\s*$"), "不支持多文档分隔符 ---"),
    (re.compile(r"^\.\.\.\s*$"), "不支持多文档结束符 ..."),
    (re.compile(r"^\?"), "不支持复杂键 ? key"),
)


def _is_unsupported(content: str, no: int, raw: str) -> bool:
    for pat, msg in _UNSUPPORTED:
        if pat.search(content):
            raise YamlError(msg, no, raw)
    return False


def _split_key(text: str, line: _Line) -> "tuple[str, str]":
    """把 `key: rest` 切成 ('key', 'rest')。按第一个不在引号内的冒号切分。"""
    in_single = in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            key = text[:i].strip()
            if not key:
                raise YamlError("冒号前缺少键名", line.no, text)
            rest = _strip_comment(text[i + 1 :]).strip()
            return key, rest
    raise YamlError("这一行看起来是映射，但找不到冒号（键值对必须写成 key: value）", line.no, text)


def _looks_like_map_entry(text: str) -> bool:
    """判断 `- ` 后面那段是不是 `key: value`。"""
    in_single = in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            # 冒号后必须是空白或行尾，否则可能是 `http://x` 这类值
            after = text[i + 1 :]
            return after == "" or after[0] in " \t"
    return False


_NULLS = {"", "~", "null", "Null", "NULL"}
_TRUE = {"true", "True", "TRUE", "yes", "Yes", "on", "On"}
_FALSE = {"false", "False", "FALSE", "no", "No", "off", "Off"}


def _split_inline(s: str, line: _Line) -> "list[str]":
    """按逗号切分流式序列的内容，引号内的逗号不切。"""
    out: list[str] = []
    buf: list[str] = []
    in_s = in_d = False
    for ch in s:
        if ch == "'" and not in_d:
            in_s = not in_s
            buf.append(ch)
        elif ch == '"' and not in_s:
            in_d = not in_d
            buf.append(ch)
        elif ch == "," and not in_s and not in_d:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _parse_scalar(text: str, line: _Line) -> Any:
    text = text.strip()
    if text in _NULLS:
        return None

    # 流式序列：只允许标量元素，不允许嵌套 —— 卡片里的 args 大多是这一种，
    # 支持它能省掉一堆 - 逐行书写；但嵌套集合会让解析器复杂度失控，一律拒绝。
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError("流式序列缺少收尾的 ]", line.no, line.raw)
        inner = text[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in _split_inline(inner, line):
            if part.startswith(("[", "{")):
                raise YamlError("流式序列不支持嵌套集合，请改用 - 逐行书写", line.no, line.raw)
            items.append(_parse_scalar(part, line))
        return items

    # 值位置的锚点 / 别名 / 流式映射也要拦 —— 只查行首是不够的：
    # `a: {x: 1}` 的行首是 a，检查放在行首会漏掉。
    if text.startswith("{"):
        raise YamlError("不支持流式映射 {a: b}，请改用缩进书写", line.no, line.raw)
    if text.startswith("&"):
        raise YamlError("不支持锚点 &anchor", line.no, line.raw)
    if text.startswith("*"):
        raise YamlError("不支持别名 *alias", line.no, line.raw)

    if text in _TRUE:
        return True
    if text in _FALSE:
        return False

    # 引号字符串
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        if text[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner

    if text.startswith(("'", '"')) != text.endswith(("'", '"')) and len(text) > 1:
        if text[0] in "\"'":
            raise YamlError("引号没有闭合", line.no, text)

    # 数字
    # 两条正则都已在前面fullmatch 校验过格式，int()/float() 不可能抛 ValueError，
    # 所以这里不再套 try/except —— 那层兜底从不是防御，只是让错误静默滑走。
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", text) and re.search(r"[.eE]", text):
        return float(text)

    return text


def _parse_block_scalar(lines: list[_Line], idx: int, parent_indent: int, style: str) -> "tuple[str, int]":
    """解析 | 或 > 块。空行无条件收集，遇到非空且缩进 <= 父级时停止。"""
    collected: list[_Line] = []
    while idx < len(lines):
        ln = lines[idx]
        if ln.blank:
            collected.append(ln)
            idx += 1
            continue
        if ln.indent <= parent_indent:
            break
        collected.append(ln)
        idx += 1

    while collected and collected[-1].blank:
        collected.pop()

    if not collected:
        return "", idx

    content_indent = min(ln.indent for ln in collected if not ln.blank)
    parts: list[str] = []
    for ln in collected:
        if ln.blank:
            parts.append("")
        else:
            parts.append(ln.raw[content_indent:])

    if style.startswith("|"):
        text = "\n".join(parts)
    else:
        # 折叠块：相邻非空行用空格连起来，空行保留为断行
        out: list[str] = []
        buf: list[str] = []
        for p in parts:
            if p.strip() == "":
                if buf:
                    out.append(" ".join(buf))
                    buf = []
                out.append("")
            else:
                buf.append(p.strip())
        if buf:
            out.append(" ".join(buf))
        text = "\n".join(out)

    if style.endswith("-"):
        text = text.rstrip("\n")
    return text, idx


def _parse_map(lines: list[_Line], idx: int, indent: int) -> "tuple[dict[str, Any], int]":
    result: dict[str, Any] = {}
    while idx < len(lines):
        ln = lines[idx]
        if ln.blank:
            idx += 1
            continue
        if ln.indent < indent:
            break
        if ln.indent > indent:
            raise YamlError(f"意外缩进（本层缩进应为 {indent}）", ln.no, ln.raw)
        if ln.stripped.startswith("- ") or ln.stripped == "-":
            break

        key, rest = _split_key(ln.stripped, ln)

        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            value, idx = _parse_block_scalar(lines, idx + 1, indent, rest)
        elif rest == "":
            nxt = _next_content(lines, idx + 1)
            if nxt is not None and lines[nxt].indent > indent:
                value, idx = _parse_block(lines, idx + 1, lines[nxt].indent)
            else:
                value, idx = None, idx + 1
        else:
            value, idx = _parse_scalar(rest, ln), idx + 1

        if key in result:
            raise YamlError(f"键重复：{key!r}", ln.no, ln.raw)
        result[key] = value
    return result, idx


def _parse_seq(lines: list[_Line], idx: int, indent: int) -> "tuple[list[Any], int]":
    result: list[Any] = []
    while idx < len(lines):
        ln = lines[idx]
        if ln.blank:
            idx += 1
            continue
        if ln.indent < indent:
            break
        if ln.indent > indent:
            raise YamlError(f"意外缩进（本层缩进应为 {indent}）", ln.no, ln.raw)

        stripped = ln.stripped
        if not (stripped.startswith("- ") or stripped == "-"):
            break

        item_text = stripped[2:].strip() if stripped.startswith("- ") else ""

        if item_text == "":
            nxt = _next_content(lines, idx + 1)
            if nxt is not None and lines[nxt].indent > indent:
                value, idx = _parse_block(lines, idx + 1, lines[nxt].indent)
            else:
                value, idx = None, idx + 1
        elif _looks_like_map_entry(item_text):
            sub_indent = indent + 2
            sub: list[_Line] = [_Line(ln.no, sub_indent, item_text, item_text, False)]
            j = _next_content(lines, idx + 1)
            while j is not None and lines[j].indent >= sub_indent:
                sub.append(lines[j])
                j = _next_content(lines, j + 1)
            value, _ = _parse_map(sub, 0, sub_indent)
            idx = j if j is not None else len(lines)
        else:
            value, idx = _parse_scalar(item_text, ln), idx + 1

        result.append(value)
    return result, idx


def _next_content(lines: list[_Line], idx: int) -> "int | None":
    """返回下一个非空行的索引，没有则 None。"""
    while idx < len(lines):
        if not lines[idx].blank:
            return idx
        idx += 1
    return None


def _parse_block(lines: list[_Line], idx: int, indent: int) -> "tuple[Any, int]":
    nxt = _next_content(lines, idx)
    if nxt is None:
        return None, len(lines)
    if lines[nxt].indent < indent:
        return None, idx
    stripped = lines[nxt].stripped
    if stripped.startswith("- ") or stripped == "-":
        return _parse_seq(lines, idx, indent)
    return _parse_map(lines, idx, indent)


def loads(text: str) -> Any:
    """解析 YAML 子集文本，返回 Python 对象。失败抛 YamlError。"""
    lines = _preprocess(text)
    nxt = _next_content(lines, 0)
    if nxt is None:
        return {}
    value, idx = _parse_block(lines, 0, 0)
    leftover_idx = _next_content(lines, idx)
    if leftover_idx is not None:
        leftover = lines[leftover_idx]
        raise YamlError(
            "这里出现了无法归入上层结构的内容（多半是缩进层级不一致）",
            leftover.no,
            leftover.raw,
        )
    return value


def load(path: "str | Path") -> Any:
    p = Path(path)
    return loads(p.read_text(encoding="utf-8"))


def extract_frontmatter(text: str) -> "tuple[Any, str, int]":
    """从 Markdown 文本里取出 `---` 之间的 frontmatter。

    返回 (解析结果, frontmatter 之后的正文, frontmatter 结束行号)。
    没有 frontmatter 时返回 (None, 原文, 0)。
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---"):
        return None, text, 0
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise YamlError("frontmatter 起始的 --- 没有对应的结束 ---")
    body = "\n".join(lines[end + 1 :])
    return loads("\n".join(lines[1:end])), body, end + 1


if __name__ == "__main__":
    # 自检用例拆到 yamlmini_selftest.py 了（两个文件各自在 500 行以内）。
    # 这里保一个入口，习惯性敲 python scripts/yamlmini.py 也能跑全套用例。
    import yamlmini_selftest

    sys.exit(yamlmini_selftest.main())


