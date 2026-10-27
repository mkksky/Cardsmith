#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_ahk_v2_selftest.py —— AutoHotkey v2 守卫的回归用例

单独成文件而不是内联在工具里：与 check_dead_code_selftest.py 同一理由 ——
内联后工具本体会超过本套件自己要求的 .py 500 行上限。

用例覆盖的边界：
    1. 六类违规各有命中与放行用例
    2. 保守方向不误报（合法 v2 写法必须零误报）
    3. 真实 bug 复现（605 行的 Run 引号不闭合）
    4. 注释 / 字符串里的引号不干扰判定

用法：
    python tools/check_ahk_v2_selftest.py
    python tools/check_ahk_v2.py --rules rules/ahk-v2-guard.json --selftest

退出码：0 = 全绿，1 = 有用例失败
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from check_ahk_v2 import check_file

RULES = {"extensions": [".ahk"], "skip_paths": ["**/skip/**"]}

V2_HEADER = "#Requires AutoHotkey v2.0\n"


def run() -> int:
    failures = 0

    def case(name: str, content: str, expect_kinds: list[str], expect_none: bool = False) -> None:
        """expect_none=True 表示期望零违规；否则要求 expect_kinds 里的类别都出现。"""
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.ahk"
            p.write_text(content, encoding="utf-8")
            findings = check_file(p, RULES)
        kinds = [k for _, k, _ in findings]
        ok = not findings if expect_none else all(k in kinds for k in expect_kinds)
        if ok:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}\n      期望 {expect_kinds or '零违规'}，实际 {kinds}")
            failures += 1

    # ---------- ① requires ----------
    case(
        "缺 #Requires → 违规",
        "x := 1\n",
        ["requires-v1"],
    )
    case(
        "#Requires 写 v1.1 → 违规",
        "#Requires AutoHotkey v1.1\nx := 1\n",
        ["requires-v1"],
    )
    case(
        "#Requires v2.0 → 放行",
        V2_HEADER + "x := 1\n",
        [],
        expect_none=True,
    )

    # ---------- ② legacy builtin ----------
    case(
        "A_LocalAppData → 违规",
        V2_HEADER + 'x := A_LocalAppData "\\JianyingPro"\n',
        ["legacy-builtin"],
    )
    case(
        "A_EnvVar → 违规",
        V2_HEADER + 'x := A_EnvVar("LOCALAPPDATA")\n',
        ["legacy-builtin"],
    )
    case(
        "EnvGet 替代写法 → 放行",
        V2_HEADER + 'x := EnvGet("LOCALAPPDATA")\n',
        [],
        expect_none=True,
    )

    # ---------- ③ 单引号未闭合（真实 bug） ----------
    case(
        "单引号串多一个双引号 → 违规（605 行原样）",
        V2_HEADER + '    Run(\'"\' jyPath \'" --src1")\n',
        ["unterminated-string"],
    )
    case(
        "单引号串正确闭合 → 放行",
        V2_HEADER + "    Run('\"' jyPath '\" --src1')\n",
        [],
        expect_none=True,
    )
    case(
        "单引号串内含分号 → 不误报",
        V2_HEADER + "    x := 'a;b'\n",
        [],
        expect_none=True,
    )

    # ---------- ④ concat 缺空格 ----------
    case(
        "单引号串后零空白紧贴标识符 → 违规",
        V2_HEADER + "    Run('\"'path'\" --src1')\n",
        ["concat-no-space"],
    )
    case(
        "单引号串后有空格 → 放行",
        V2_HEADER + "    Run('\"' path '\" --src1')\n",
        [],
        expect_none=True,
    )
    case(
        "双引号串后紧贴标识符 → 放行（AHK 能分辨）",
        V2_HEADER + '    Log("hello" x)\n',
        [],
        expect_none=True,
    )

    # ---------- ⑤ legacy assign ----------
    case(
        "v1 裸赋值 → 违规",
        V2_HEADER + "x = 5\n",
        ["legacy-assign"],
    )
    case(
        "v2 赋值 → 放行",
        V2_HEADER + "x := 5\n",
        [],
        expect_none=True,
    )
    case(
        "比较表达式 a == b → 放行",
        V2_HEADER + "if (a == b) {\n    return 1\n}\n",
        [],
        expect_none=True,
    )

    # ---------- ⑥ legacy command ----------
    case(
        "v1 命令式 MsgBox, hi → 违规",
        V2_HEADER + "MsgBox, hello\n",
        ["legacy-command"],
    )
    case(
        "v2 函数调用 MsgBox(a) → 放行",
        V2_HEADER + 'MsgBox("hello")\n',
        [],
        expect_none=True,
    )
    case(
        "v2 多变量赋值 a := 1, b → 放行",
        V2_HEADER + "a := 1, b := 2\n",
        [],
        expect_none=True,
    )
    case(
        "v2 数组字面量 [a, b] → 放行",
        V2_HEADER + "arr := [1, 2, 3]\n",
        [],
        expect_none=True,
    )

    # ---------- 综合：真实项目的合法写法不应被误伤 ----------
    case(
        "典型合法 v2 片段 → 放行",
        V2_HEADER
        + "SendMode(\"Input\")\n"
        + 'CoordMode("Mouse", "Screen")\n'
        + "roots := []\n"
        + 'for rel in ["Program Files\\JianyingPro", "JianyingPro"] {\n'
        + "    roots.Push(rel)\n"
        + "}\n"
        + "if (!DirExist(root))\n"
        + "    continue\n"
        + 'Log("✓ 命中: " p)\n'
        + "try {\n"
        + "    x := 1\n"
        + "} catch\n"
        + "    x := 2\n",
        [],
        expect_none=True,
    )

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：21/21")
    return 0


if __name__ == "__main__":
    sys.exit(run())
