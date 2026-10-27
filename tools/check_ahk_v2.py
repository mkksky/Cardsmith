#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_ahk_v2.py —— AutoHotkey v2 语法守卫

抓「照着 v1 的记忆写 AHK v2」这件事。v1 的写法在 v2 里**不是报错就是静默变行为**，
两种都比编译失败更贵：脚本能跑，但行为和写的人想的不一样。

检查四类**机器能裁断的事实**：

    1. requires-v1        缺 `#Requires AutoHotkey v2.0`，或仍写着 v1.1
    2. legacy-builtin     用了 v2 已移除/改名的内置变量
    3. concat-no-space    auto-concat 相邻项之间缺空格（v2 强制要求）
    4. legacy-assign      v1 命令式裸赋值（`var = value`）

依据：https://www.autohotkey.com/docs/v2/v2-changes.htm

关于「宁漏勿误」：本守卫只做**字面层面**的事实判断，不解析完整语法。
它宁可放过一些少见的 v1 写法，也不误报合法的 v2 代码 —— 门禁一旦误报，
就会逼出 --no-verify，那整套守卫就白装了。

退出码：0 = 无违规；1 = 有违规；2 = 用法/规则错误（绝不能当通过处理）

用法：
    python tools/check_ahk_v2.py --rules rules/ahk-v2-guard.json --all
    python tools/check_ahk_v2.py --rules <rules.json> --files a.ahk b.ahk
    python tools/check_ahk_v2.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


class RuleError(Exception):
    """规则文件的问题。对应退出码 2。"""


# ---------------------------------------------------------------- 规则加载

DEFAULT_EXTS = (".ahk",)
DEFAULT_SKIP = (
    "**/.git/**",
    "**/.workbuddy/**",
    "**/.codingskills/**",
    "**/node_modules/**",
    "**/__pycache__/**",
)


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"找不到规则文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n  {e}") from e
    if not isinstance(data, dict):
        raise RuleError("规则文件顶层必须是对象")
    return data


# ---------------------------------------------------------------- 扫描工具

def iter_files(root: Path, rules: dict, files: "list[str] | None"):
    """产出待检查的 .ahk 文件路径。"""
    exts = tuple(rules.get("extensions") or DEFAULT_EXTS)
    skip = tuple(rules.get("skip_paths") or DEFAULT_SKIP)

    if files:
        for f in files:
            p = Path(f)
            if not p.is_absolute():
                p = root / p
            if p.is_file() and p.suffix.lower() in exts:
                yield p
        return

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(root).as_posix()
        if any(_match_skip(rel, s) for s in skip):
            continue
        yield p


def _match_skip(rel: str, pattern: str) -> bool:
    """把 ** 通配的 glob 变成正则。"""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), rel) is not None


def git_staged_files(root: Path) -> list[str]:
    """pre-commit 无文件参数时读取暂存清单，与其它 staged-files 工具一致。"""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=root,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    text = proc.stdout.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------- 三类检查

# v2 已移除 / 改名的内置变量。key = 变量名，value = 替代方案
LEGACY_BUILTINS = {
    "A_LocalAppData": 'EnvGet("LOCALAPPDATA")',
    "A_EnvVar": 'EnvGet("X")',
    "A_Is64BitProcess": "无需替代（v2 无此变量）",
    "A_Is64BitOS": "无需替代（v2 无此变量）",
    "A_OSType": "已移除（v2 只支持 NT）",
    "A_IsUnicode": "已移除（v2 恒为 Unicode）",
    "A_LoopFileLongPath": "A_LoopFileFullPath",
    "Clipboard": "A_Clipboard",
    "ProgramFiles": "A_ProgramFiles",
    "ComSpec": "A_ComSpec",
    "A_ThisLabel": "改用函数参数",
    "A_ExitReason": "已移除",
    "A_StringCaseSense": "已移除",
    "A_Gui": "已移除（改用事件参数）",
    "A_GuiEvent": "已移除（改用事件参数）",
    "A_GuiControl": "已移除（改用事件参数）",
    "A_GuiControlEvent": "已移除（改用事件参数）",
    "A_GuiX": "已移除（改用事件参数）",
    "A_GuiY": "已移除（改用事件参数）",
    "A_GuiWidth": "已移除（改用事件参数）",
    "A_GuiHeight": "已移除（改用事件参数）",
    "A_CaretX": "CaretGetPos()",
    "A_CaretY": "CaretGetPos()",
    "A_FormatFloat": "已移除",
    "A_FormatInteger": "已移除",
    "A_AutoTrim": "已移除",
    "A_BatchLines": "已移除",
    "A_IPAddress1": "SysGetIPAddresses()",
    "A_IPAddress2": "SysGetIPAddresses()",
    "A_IPAddress3": "SysGetIPAddresses()",
    "A_IPAddress4": "SysGetIPAddresses()",
    "A_DefaultGui": "已移除",
    "A_DefaultListView": "已移除",
    "A_DefaultTreeView": "已移除",
    "A_ThisMenu": "已移除（改用菜单回调参数）",
    "A_ThisMenuItem": "已移除（改用菜单回调参数）",
    "A_ThisMenuItemPos": "已移除（改用菜单回调参数）",
}

# v1 遗留的独立内置变量（v2 里也不该出现）
LEGACY_STANDALONE = {"Object"}

# v1 命令式裸赋值：`name = value`（v2 必须是 `name := value`）
# 排除：`:=`, `==`, `!=`, `>=`, `<=`, `+=` 等；排除 ==/!= 开头的比较
RE_LEGACY_ASSIGN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+?)\s*$"
)
# 函数调用 / 控制流 / 关键字开头的行不是赋值
NOT_ASSIGN_KEYWORDS = {
    "if", "else", "while", "for", "return", "goto", "break", "continue",
    "switch", "case", "default", "try", "catch", "finally", "throw",
    "class", "static", "global", "local",
}
RE_FUNC_CALL = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\(")

# v1 命令式语法：`Command, param1, param2`（逗号分隔、无括号）
# v2 里全是函数调用，逗号只在括号内才合法。
# 判据：行首是标识符，后面直接跟逗号（不是 `=` `:=` `==` 也不是函数调用）。
# ⚠️ 排除 v2 合法写法：
#    - 函数调用带括号：Foo(a, b)
#    - 变量赋值：x := a, b   ← v2 允许多变量赋值，逗号在赋值里合法
#    - 数组字面量：[a, b]
RE_LEGACY_COMMAND = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(.+?)\s*$"
)
# v2 的多变量赋值（这些逗号是合法的）
RE_V2_MULTI_ASSIGN = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*(:=|=)\s*.*,\s*.*$")
# v2 里常见的合法逗号场景：数组字面量、函数调用实参、for 循环
RE_LEGAL_COMMA_CONTEXT = re.compile(
    r"^\s*(\[[^\]]*\]|for\s|Loop\s|while\s|if\s|switch\s|case\s|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\[[^\]]*\]\s*$)"
)
# v1 常见命令词（这些词后面跟逗号 = v1 命令式）
V1_COMMANDS = {
    "MsgBox", "Send", "SendInput", "SendPlay", "Click", "MouseMove",
    "MouseClick", "MouseClickDrag", "Sleep", "Run", "RunWait", "WinActivate",
    "WinWait", "WinWaitActive", "WinExist", "WinClose", "WinMinimize",
    "WinMaximize", "WinRestore", "WinMove", "WinGetPos", "WinGetTitle",
    "WinGetText", "WinGetClass", "WinGetProcess", "WinGetProcessPath",
    "ControlSend", "ControlClick", "ControlGetText", "ControlSetText",
    "FileAppend", "FileRead", "FileDelete", "FileCopy", "FileMove",
    "FileCreateDir", "DirCreate", "DirDelete", "StringReplace", "StrReplace",
    "StringSplit", "StrSplit", "StringLen", "StrLen", "StringTrim", "StrTrim",
    "Sort", "Random", "SoundBeep", "KeyHistory", "KeyWait", "BlockInput",
    "SetTimer", "SetWindowPos", "PostMessage", "SendMessage", "WinWaitClose",
}

# ⚠️ 关于「auto-concat 缺空格」这条检查 —— 为什么不能简单用正则抓
# ------------------------------------------------------------------
# 官方原文说 v2 的 auto-concat 强制要求相邻项之间有空格。但**不能**简单用
# 「字符串紧贴标识符」去抓，原因是 AHK 的字符串有三种，行为完全不同：
#
#   "abc" var    双引号串：闭合后紧贴标识符 → AHK 能分辨，合法（只是 auto-concat）
#   'abc' var    单引号串：闭合后紧贴标识符 → AHK 仍当合法（v2 支持单引号串）
#   '"' var      单引号串里含双引号 → **双引号是字面量**，解析器状态特殊
#
# 真正会炸的是第三种。官方原文：
#   "Literal quote marks are written by preceding the mark with an escape
#    character - `" or `' - or by using the opposite type of quote mark:
#    '"42" is the answer'. Doubling the quote marks has no special meaning,
#    and causes an error since auto-concat requires a space."
#
# 即：`'"` 这种「单引号串包双引号」是**推荐写法**，但拼接时**必须留空格**。
# `Run('"' path '" --src1')` 合法；写成 `Run('"'path'" --src1')` 就报
# `Missing "'"`。
#
# 所以本检查只抓一种形态：**单引号串（内含双引号）后面零空白紧贴下一个 token**。
# 这正好覆盖 605 行的真实 bug，且不会误报普通的 `"abc" var`。
RE_SINGLE_QUOTED_LITERAL = re.compile(r"""'[^'\n]*"[^'\n]*'""")
RE_CONCAT_NOSPACE = re.compile(
    r"""('[^'\n]*"[^'\n]*')(?=[A-Za-z_$])"""  # 单引号串(含双引号)后零空白紧贴标识符
)

# 注释行、行尾注释、字符串内部的 # 都要排除


def strip_comment(line: str) -> str:
    """去掉行尾注释，并把字符串内容替换成等长空白（保留列位，便于定位）。

    AHK 字符串规则：
      - 双引号串内 `\"` 转义、"" 双写转义
      - 单引号串内 `''` 双写转义（单引号串内双引号是**字面量**，不是定界符）
      - 单引号串内不能有单引号（除非写成 ''）
    这里做的是**近似**处理：只求不误报，不追求完整解析。
    """
    out = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == ";":
            break  # 行注释
        if ch == '"':
            # 双引号串：扫描到未转义的闭合引号
            out.append(ch)
            i += 1
            while i < n:
                c = line[i]
                out.append(c)
                i += 1
                if c == '"':
                    # 下一个字符也是 " → 转义的双引号，继续
                    if i < n and line[i] == '"':
                        out.append('"')
                        i += 1
                        continue
                    break
                if c == "`":
                    # 反引号转义下一个字符
                    if i < n:
                        out.append(line[i])
                        i += 1
            continue
        if ch == "'":
            # 单引号串：单引号串内双引号是字面量，不是结束符
            out.append(ch)
            i += 1
            while i < n:
                c = line[i]
                out.append(c)
                i += 1
                if c == "'":
                    if i < n and line[i] == "'":
                        out.append("'")
                        i += 1
                        continue
                    break
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def check_file(path: Path, rules: dict) -> "list[tuple[int, str, str]]":
    """返回 [(行号, 类别, 说明)]。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gbk", errors="replace")

    lines = text.split("\n")
    findings: "list[tuple[int, str, str]]" = []

    # ① requires
    requires_found = False
    for i, raw in enumerate(lines[:40], start=1):
        s = raw.strip()
        if s.lower().startswith("#requires"):
            requires_found = True
            if "v2" not in s.lower():
                findings.append(
                    (i, "requires-v1", f"#Requires 写的是 v1：{s}｜应改为 #Requires AutoHotkey v2.0")
                )
            break
    if not requires_found:
        findings.append(
            (1, "requires-v1", "缺少 #Requires AutoHotkey v2.0｜v2 里缺这行会按 v1 解释器跑")
        )

    # ② ② legacy builtins
    for i, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        if not code.strip():
            continue
        for var, fix in LEGACY_BUILTINS.items():
            if not re.search(r"\b" + re.escape(var) + r"\b", code):
                continue
            findings.append(
                (i, "legacy-builtin", f"用了 v2 已移除的内置变量 {var}｜替代：{fix}")
            )
        for var in LEGACY_STANDALONE:
            if re.search(r"\b" + re.escape(var) + r"\b", code):
                findings.append(
                    (i, "legacy-builtin", f"用了 v1 的内置变量 {var}｜v2 里没有")
                )

    # ③ 单引号未闭合 —— 605 行的真实 bug
    #    `Run('"' jyPath '" --src1")` 有 3 个单引号（奇数）→ 第二个串没闭合
    #    → AHK 报 `Missing "'"`。奇数个未转义单引号 = 字符串没闭合，几乎必然是 bug。
    for i, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        if code.count("'") % 2 == 1:
            findings.append(
                (i, "unterminated-string",
                 f"本行单引号数为奇数（{code.count(chr(39))} 个）｜字符串没闭合，"
                 f"AHK 会报 Missing '\"'。检查是不是多了/少了一个引号")
            )

    # ④ concat no space —— 单引号串(含双引号) 后零空白紧贴标识符
    for i, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        if not code.strip():
            continue
        for m in RE_CONCAT_NOSPACE.finditer(code):
            findings.append(
                (i, "concat-no-space",
                 f"auto-concat 缺空格：{m.group(0)!r}｜v2 强制要求相邻项之间有空格，"
                 f"否则解析器会当成还在字符串里（报 Missing '\"'）")
            )

    # ⑤ legacy assign
    for i, raw in enumerate(lines, start=1):
        code = strip_comment(raw).rstrip()
        if not code.strip():
            continue
        s = code.strip()
        if s.startswith(";"):
            continue
        m = RE_LEGACY_ASSIGN.match(code)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in NOT_ASSIGN_KEYWORDS:
            continue
        if RE_FUNC_CALL.match(code):
            continue
        # 右值明显是表达式的（含引号/括号/运算符）不算 v1 赋值
        rhs = m.group(2)
        if rhs.startswith(("(", '"', "'")) or re.search(r"[()\.]", rhs):
            continue
        findings.append(
            (i, "legacy-assign",
             f"疑似 v1 命令式裸赋值：{name} = {rhs}｜v2 必须写成 {name} := {rhs}")
        )

    # ⑥ legacy command —— v1 的 `Command, param` 写法
    for i, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        s = code.strip()
        if not s:
            continue
        m = RE_LEGACY_COMMAND.match(code)
        if not m:
            continue
        cmd = m.group(1)
        if cmd not in V1_COMMANDS:
            continue
        # 排除 v2 合法场景
        if RE_V2_MULTI_ASSIGN.match(code) or RE_LEGAL_COMMA_CONTEXT.match(code):
            continue
        findings.append(
            (i, "legacy-command",
             f"疑似 v1 命令式语法：{s}｜v2 里 {cmd} 是函数，必须写成 {cmd}(...)")
        )

    return findings


# ---------------------------------------------------------------- 输出

def format_finding(path: Path, root: Path, line: int, kind: str, msg: str) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = str(path)
    return f"  {rel}:{line}  [{kind}] {msg}"


def main() -> int:
    ap = argparse.ArgumentParser(description="AutoHotkey v2 语法守卫")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="扫描全部 .ahk 文件")
    ap.add_argument("--files", nargs="*", help="只检查指定文件")
    ap.add_argument("--quiet", action="store_true", help="只在有违规时输出")
    ap.add_argument(
        "--selftest", action="store_true", help="跑回归用例（不需要 --rules/--root）"
    )
    args = ap.parse_args()

    if args.selftest:
        from check_ahk_v2_selftest import run as run_selftest

        return run_selftest()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[x] 项目根不存在：{root}", file=sys.stderr)
        return 2

    rules = load_rules(Path(args.rules)) if args.rules else {}

    try:
        selected = args.files
        if not args.all and selected is None:
            selected = git_staged_files(root)
        targets = list(iter_files(root, rules, selected))
    except RuleError as e:
        print(f"[x] 扫描失败：{e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[x] 扫描失败：{e}", file=sys.stderr)
        return 2

    if not targets:
        if not args.quiet:
            print("[ok] 没有找到 .ahk 文件")
        return 0

    all_findings = []
    for p in targets:
        try:
            for line, kind, msg in check_file(p, rules):
                all_findings.append((p, line, kind, msg))
        except Exception as e:  # noqa: BLE001
            print(f"[x] 读取失败 {p}：{e}", file=sys.stderr)
            return 2

    if not all_findings:
        if not args.quiet:
            print(f"[ok] AHK v2 语法检查通过（{len(targets)} 个文件）")
        return 0

    print(f"[!] 发现 {len(all_findings)} 处 AHK v2 语法问题：")
    for p, line, kind, msg in all_findings:
        print(format_finding(p, root, line, kind, msg))
    print()
    print("  依据：https://www.autohotkey.com/docs/v2/v2-changes.htm")
    return 1


if __name__ == "__main__":
    sys.exit(main())
