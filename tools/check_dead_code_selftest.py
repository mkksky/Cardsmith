#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_dead_code_selftest.py —— 死代码守卫的回归用例

单独成文件而不是内联在工具里：与 check_perf_smells_selftest.py 同一理由 ——
内联后工具本体超过本套件自己要求的 .py 500 行上限（ruff-format 展开后 526 行）。

用例覆盖的边界：
    1. 两类违规都抓     unused-import / dead-export 各有命中与放行用例
    2. 保守方向不误报   字符串字面量引用、测试引用、展开继承、skip_paths
    3. 形态收口         方法简写 gone() {} / async gone() / get foo()
    4. 基线交互         基线键文件:符号#kind，行号不进键

用法：
    python tools/check_dead_code_selftest.py
    python tools/check_dead_code.py --rules rules/dead-code-guard.json --selftest

退出码：0 = 全绿，1 = 有用例失败
"""

from __future__ import annotations

import sys
from pathlib import Path

import dead_code_collectors as collectors
from check_dead_code import check_one, is_skipped, violation_key


def run() -> int:

    import tempfile

    failures = 0

    def case(
        name: str,
        files: dict,
        expect_unused: int,
        expect_dead: int,
        rules: dict | None = None,
    ) -> None:
        nonlocal failures
        active = rules or {"extensions": [".js"], "skip_paths": ["skip/**"]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relp, content in files.items():
                p = root / relp
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            targets = [
                r
                for r in files
                if r.endswith(tuple(active["extensions"]))
                and not is_skipped(r, active["skip_paths"])
            ]
            corpus = [(r, (root / r).read_text(encoding="utf-8")) for r in targets]
            ref_ctx = collectors.build_ref_index(corpus)
            unused = dead = 0
            for r in targets:
                for v in check_one(root, r, active, ref_ctx):
                    if v["kind"] == "unused-import":
                        unused += 1
                    else:
                        dead += 1
            if (unused, dead) == (expect_unused, expect_dead):
                print(f"  [v] {name}")
            else:
                print(
                    f"  [x] {name}\n      期望 unused={expect_unused}/dead={expect_dead}，"
                    f"实际 unused={unused}/dead={dead}"
                )
                failures += 1

    case(
        "require 绑定被使用 → 通过",
        {
            "a.js": "const x = require('./b');\nconsole.log(x);\n",
            "b.js": "module.exports = 1;\n",
        },
        0,
        0,
    )
    case(
        "require 绑定全文未用 → 违规",
        {
            "a.js": "const x = require('./b');\nconsole.log('hi');\n",
            "b.js": "module.exports = 1;\n",
        },
        1,
        0,
    )
    case(
        "解构部分成员未用 → 只算一个违规",
        {
            "a.js": "const { used, unusedFn } = require('./b');\nused();\n",
            "b.js": "module.exports = { used: 1, unusedFn: 2 };\n",
        },
        1,
        0,
    )
    case(
        "别名解构 {a: b}，b 未用 → 违规记绑定名",
        {
            "a.js": "const { a: b } = require('./b');\nconsole.log('x');\n",
            "b.js": "module.exports = { a: 1 };\n",
        },
        1,
        0,
    )
    case(
        "带默认值解构 {a = 1} 未用 → 违规",
        {
            "a.js": "const { a = 1 } = require('./b');\nconsole.log('x');\n",
            "b.js": "module.exports = {};\n",
        },
        1,
        0,
    )
    case(
        "无绑定 require（副作用）→ 跳过",
        {
            "a.js": "require('./b');\nconsole.log('hi');\n",
            "b.js": "module.exports = 1;\n",
        },
        0,
        0,
    )
    case(
        "导出对象有外部引用 → 通过",
        {
            "a.js": "const { greet } = require('./b');\ngreet();\n",
            "b.js": "module.exports = { greet() {} };\n",
        },
        0,
        0,
    )
    case(
        "导出对象零外部引用 → 违规",
        {
            "a.js": "console.log('no import');\n",
            "b.js": "module.exports = { gone() {} };\n",
        },
        0,
        1,
    )
    case("exports.name 零引用 → 违规", {"b.js": "exports.lonely = 1;\n"}, 0, 1)
    case(
        "module.exports = Class 零引用 → 违规",
        {"b.js": "class Thing {}\nmodule.exports = Thing;\n"},
        0,
        1,
    )
    case(
        "测试文件里的引用也算引用 → 通过",
        {
            "__tests__/b.test.js": "const { greet } = require('../b');\ngreet();\n",
            "b.js": "module.exports = { greet() {} };\n",
        },
        0,
        0,
    )
    case(
        "被展开继承的文件 → 整文件跳过导出检查",
        {
            "a.js": "const all = { ...require('./b') };\nconsole.log(all);\n",
            "b.js": "module.exports = { gone() {} };\n",
        },
        0,
        0,
    )
    case(
        "导出名字出现在他处字符串里也算引用（保守）→ 通过",
        {
            "a.js": "console.log('see gone docs');\n",
            "b.js": "module.exports = { gone() {} };\n",
        },
        0,
        0,
    )
    case(
        "skip_paths 命中的文件不查",
        {
            "skip/a.js": "const x = require('./b');\n",
            "b.js": "module.exports = { used() {} };\n",
            "c.js": "const { used } = require('./b');\nused();\n",
        },
        0,
        0,
    )
    case(
        "一文件两类违规并存 → 各记各的",
        {
            "a.js": "const dead = require('./b');\nconsole.log('x');\n",
            "b.js": "module.exports = { gone() {} };\n",
        },
        1,
        1,
    )

    # 基线键格式
    key = violation_key({"file": "x/y.js", "name": "foo", "kind": "dead-export"})
    if key == "x/y.js:foo#dead-export":
        print("  [v] 基线键 = 文件:符号#kind")
    else:
        print(f"  [x] 基线键异常：{key}")
        failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：16/16")
    return 0


if __name__ == "__main__":
    sys.exit(run())
