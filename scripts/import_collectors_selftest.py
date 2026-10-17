#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_collectors_selftest.py —— JS 注释剥离与导入收集的自检用例

为什么必须有：collect_javascript() 是零依赖的正则实现，正则看得见
字符串里的 require、看不见「这是注释」。某小程序项目首次接入时 11 处报错
里 7 处是注释里的历史 require —— 误报率过六成，blocking 守卫会被
当成噪声绕过去。这组用例锁住「注释不算导入」这条底线。

用法：
    python scripts/import_collectors_selftest.py

退出码：0 = 全绿，1 = 有用例失败。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from import_collectors import collect_javascript, mask_js_comments  # noqa: E402

# (用例名, 源码, 期望收集到的说明符列表)
CASES: "list[tuple[str, str, list[str]]]" = [
    (
        "真实导入照收",
        "const a = require('./a');\nimport b from './b';\n",
        ["./a", "./b"],
    ),
    (
        "行注释里的 require 不算",
        "// require('../../../services/x')\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "块注释里的 require 不算",
        "/* 原实现: require('../../common/y') */\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "多行块注释里的 import 不算",
        "/**\n * ⚠️ 修复记录：\n *   import x from './gone'\n *   require('./also-gone')\n */\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "字符串里的 // 不能当注释（否则后半行被吞）",
        "const url = 'https://a.com/x';\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "双引号 / 反引号同理",
        'const u = "http://a";\nconst t = `x // y ${1} z`;\nconst a = require(\'./a\');\n',
        ["./a"],
    ),
    (
        "正则字面量里的转义斜杠不误伤",
        "const re = /\\/\\/x/;\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "行号必须与原文件对齐（换行保留）",
        "// c1\n/* c2\n   c3 */\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        "未闭合引号不能吞掉后半文件",
        "const s = 'oops\nconst a = require('./a');\n",
        ["./a"],
    ),
    (
        # 收集顺序是契约：先扫 JS_BARE_RES（副作用 import / require / 动态
        # import）整组，再扫 JS_FROM_RE（import/export ... from）整组，
        # 组内按出现位置。所以动态 import 在前、export ... from 在后。
        "export ... from 与动态 import 都收（顺序按分组）",
        "export { x } from './x';\nconst y = await import('./y');\n",
        ["./y", "./x"],
    ),
]

# (用例名, 源码, 期望行号)
LINENO_CASES: "list[tuple[str, str, int]]" = [
    ("首行", "const a = require('./a');\n", 1),
    ("注释三行后的真实导入", "// a\n// b\n// c\nconst x = require('./x');\n", 4),
    ("块注释跨行后的真实导入", "/* a\nb */\nconst x = require('./x');\n", 3),
]


def _collect(src: str) -> "list[tuple[int, str, str]]":
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(src)
        path = Path(fh.name)
    try:
        return collect_javascript(path, "case.js")
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    failed = 0

    for name, src, expected in CASES:
        got = [spec for _, spec, _ in _collect(src)]
        if got == expected:
            print(f"  [v] {name}")
        else:
            failed += 1
            print(f"  [x] {name}\n        期望 {expected}\n        实得 {got}")

    for name, src, expected_lineno in LINENO_CASES:
        got = _collect(src)
        actual = got[0][0] if got else -1
        if actual == expected_lineno:
            print(f"  [v] 行号 · {name}")
        else:
            failed += 1
            print(f"  [x] 行号 · {name}\n        期望第 {expected_lineno} 行\n        实得第 {actual} 行")

    masked = mask_js_comments("a\nb\n")
    if masked.count("\n") == 2:
        print("  [v] mask 不改动换行数")
    else:
        failed += 1
        print("  [x] mask 改动了换行数 —— 会让所有报错行号错位")

    total = len(CASES) + len(LINENO_CASES) + 1
    print(f"\n{total} 个用例：{total - failed} 通过、{failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
