#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/import_collectors.py —— 各语言源码的 import 收集器

从源文件提取「(行号, 模块路径, 原文)」三元组，供 check_import_direction.py
做层级判断。只负责收集，不做任何方向判断。

为什么必须用 AST 而不能 grep：
    函数体内的延迟导入（`def f(): from x import y`）是藏跨层调用的常见手法，
    静态文本搜索抓不到。Python 走 ast.NodeVisitor，每个 Import/ImportFrom
    节点的 lineno 都登记，包括函数体、类方法内的。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


class ParseError(Exception):
    """源文件解析失败（如 Python 语法错误）。"""


class PyImportCollector(ast.NodeVisitor):
    """收集导入节点，并标记哪些落在 TYPE_CHECKING 块里。"""

    def __init__(self) -> None:
        self.imports: list[tuple[int, str, str]] = []  # (lineno, module_path, 原文)
        self.type_checking_ranges: list[tuple[int, int]] = []

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            start = node.lineno
            end = max(
                (getattr(n, "lineno", start) for n in ast.walk(node)),
                default=start,
            )
            self.type_checking_ranges.append((start, end))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((node.lineno, alias.name, f"import {alias.name}"))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            # 相对导入：用 level 还原成 ..x.y 形态，交给上层补全
            prefix = "." * node.level
            mod = f"{prefix}{node.module or ''}"
        else:
            mod = node.module or ""
        names = ", ".join(a.name for a in node.names)
        self.imports.append((node.lineno, mod, f"from {mod} import {names}"))

    def in_type_checking(self, lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in self.type_checking_ranges)


def _is_type_checking_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.M)


def collect_python(path: Path, rel: str) -> "tuple[list[tuple[int, str, str]], list[tuple[int, int]]]":
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=rel)
    except SyntaxError as exc:
        raise ParseError(f"Python 语法错误：{rel}:{exc.lineno}\n    {exc.msg}") from exc
    col = PyImportCollector()
    col.visit(tree)
    return col.imports, col.type_checking_ranges


def collect_java(path: Path, rel: str) -> list[tuple[int, str, str]]:
    """Java 按 package/import 全限定名判断。包名本身不参与层级判断，
    层级由 import 的全限定名映射回 patterns（见 check_import_direction）。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = JAVA_IMPORT_RE.match(line)
        if m:
            out.append((i, m.group(1), line.strip()))
    return out


# ────────────────────────── JS / TS ──────────────────────────
# JS/TS 没有标准库 AST，用正则收集 —— 与 Java 收集器同一取舍：
# 启发式换零依赖。裸说明符（第三方包 / Node 内建）天然跳过，
# 存在性判定本身也保守，边界情况靠 skip_paths / 基线消化。
#
# 但正则必须先剥注释，否则误报率高到无法当门禁用：
#     // require('../../../services/x')        ← 讲「为什么这么写」的历史说明
#     /* 原实现: require('../../common/y') */
# 这类「修复记录」在真实项目里极常见 —— 某小程序项目首次接入本收集器时，
# 11 处报失效导入里 7 处是注释里的历史 require，真断链只有 4 处。
# 误报占比过六成的 blocking 守卫，第一次提交就会把门禁变成噪声。

# import ... from 'x' / export ... from 'x'（含多行花括号列表）
JS_FROM_RE = re.compile(r"""\b(?:import|export)\b[^;'"]*?\bfrom\s*['"]([^'"]+)['"]""")
# import 'x'（副作用）/ require('x') / import('x')（动态导入）
JS_BARE_RES = (
    re.compile(r"""\bimport\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]"""),
)


def mask_js_comments(src: str) -> str:
    """把注释内容替换成空格（换行保留），让正则只命中真实代码。

    保留换行是硬要求：调用方用 `src.count("\\n", 0, m.start())` 算行号，
    抹掉换行会让所有报错行号错位。

    只区分字符串 / 行注释 / 块注释，不做完整词法分析：
      ・正则字面量里的裸 `//` 不处理 —— JS 正则中 `//` 必须写成 `\\/`
        否则会被引擎当行注释，写法上本来就不存在裸 `//`。
      ・普通字符串不跨行，遇到换行强制退出字符串态，防止一处未闭合
        引号把文件后半段整体吞掉（模板字符串 `` ` `` 例外，可跨行）。
      ・字符串里的 `//`、`/*` 不会误伤（这正是不能直接用正则剥注释的原因：
        `const url = 'https://a.com'` 会把后半行吃掉）。
    """
    out = list(src)
    i = 0
    n = len(src)
    mode = "code"  # code / line / block / str
    quote = ""
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if mode == "code":
            if ch in "\"'`":
                mode, quote = "str", ch
            elif ch == "/" and nxt == "/":
                mode = "line"
                out[i] = out[i + 1] = " "
                i += 2
                continue
            elif ch == "/" and nxt == "*":
                mode = "block"
                out[i] = out[i + 1] = " "
                i += 2
                continue
            i += 1
            continue
        if mode == "line":
            if ch == "\n":
                mode = "code"
            else:
                out[i] = " "
            i += 1
            continue
        if mode == "block":
            if ch == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                mode = "code"
                continue
            if ch != "\n":
                out[i] = " "
            i += 1
            continue
        # mode == "str"
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            mode = "code"
        elif ch == "\n" and quote != "`":
            mode = "code"  # 普通字符串不跨行；未闭合时不把后半文件全吞掉
        i += 1
    return "".join(out)


def mask_js_strings_and_comments(src: str) -> str:
    """在 mask_js_comments 之上，**额外把字符串字面量的内容也替换成空格**。

    与 mask_js_comments 的分工：
      ・mask_js_comments 只让解析器跳过字符串（内容原样保留），
        因为收集 import 时正则是靠字符串的引号来定位的，抹掉就找不到了
      ・本函数给「用正则找坏味道」的调用方用：正则不该命中字符串里的内容

    为什么需要：JS/Java 的坏味道正则（空 catch、无参 catch）跑在源码上时，
    会命中**字符串字面量里的代码样本** —— 最常见的形态就是测试用例：
    一行 case() 调用把「空 catch」的完整写法当字典值传给临时文件。
    那行自己就在 .py 文件里写了一条空 catch，扫全文必然命中。
    登记基线是错的解法（那是给真违规留的出口），必须让扫描器看不见字符串。

    注意本函数的说明里刻意**不写完整的坏味道代码**：docstring 的缩进块在
    词法上就是真实代码，写出来会被自家 pre-commit 的 debug-smells 拦下
    （真阳性，但那是文档在演示坏味道，不是在写坏味道）。

    换行一律保留：调用方靠 src.count("\\n", 0, m.start()) 算行号。
    字符串的**引号本身也保留**，这样依赖引号定位的正则（如 catch 前的括号匹配）
    仍能看到字符串边界，不会因为内容被抹掉而错位匹配。
    """
    masked = mask_js_comments(src)
    out = list(masked)
    i = 0
    n = len(masked)
    quote = ""
    while i < n:
        ch = masked[i]
        if quote:
            # 已进入字符串：内容抹成空格，\n 保留；转义整体跳过
            if ch == "\\" and i + 1 < n:
                out[i] = " " if ch != "\n" else "\n"
                out[i + 1] = " " if masked[i + 1] != "\n" else "\n"
                i += 2
                continue
            if ch == quote:
                quote = ""
            elif ch != "\n":
                out[i] = " "
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
        i += 1
    return "".join(out)


def collect_javascript(path: Path, rel: str) -> list[tuple[int, str, str]]:
    """收集 JS/TS/Vue 的模块说明符，返回 (行号, 说明符原文, 展示文本)。

    说明符保留原样（'./x'、'@/a/b'、'lodash'），由调用方决定哪些参与
    存在性判定 —— 裸说明符（第三方包 / Node 内建）天然跳过。
    """
    src = mask_js_comments(path.read_text(encoding="utf-8", errors="replace"))
    seen: set[tuple[int, str]] = set()
    out: list[tuple[int, str, str]] = []

    def add(lineno: int, spec: str) -> None:
        key = (lineno, spec)
        if key not in seen:
            seen.add(key)
            out.append((lineno, spec, f"import '{spec}'"))

    for pat in JS_BARE_RES:
        for m in pat.finditer(src):
            add(src.count("\n", 0, m.start()) + 1, m.group(1))
    for m in JS_FROM_RE.finditer(src):
        add(src.count("\n", 0, m.start()) + 1, m.group(1))
    return out
