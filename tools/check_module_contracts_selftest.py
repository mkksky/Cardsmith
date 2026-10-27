#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_module_contracts.py 的回归用例。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from check_module_contracts import (
    collect_contract_violations,
    filter_for_files,
    violation_key,
)

RULES = {
    "extensions": [".js"],
    "skip_paths": ["skip/**"],
    "aliases": {"@/": "src/"},
}


def run() -> int:
    failures = 0

    def case(name: str, files: dict, expect: int) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            actual = collect_contract_violations(root, RULES)
            if len(actual) == expect:
                print(f"  [v] {name}")
            else:
                print(
                    f"  [x] {name}\n      期望 {expect} 处，实际 {len(actual)}：{actual}"
                )
                failures += 1

    case(
        "定义了导出对象但漏写 module.exports → 违规",
        {
            "entry.js": "const { Api } = require('./utils');\nApi.run();\n",
            "utils/index.js": "const exportObject = { Api: {} };\n",
        },
        1,
    )
    case(
        "导出对象正确赋值 → 通过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi.run();\n",
            "utils/index.js": (
                "const exportObject = { Api: {} };\nmodule.exports = exportObject;\n"
            ),
        },
        0,
    )
    case(
        "别名解构按源导出名检查 → 缺失命中",
        {
            "entry.js": "const { Runtime: runner } = require('./utils');\nrunner();\n",
            "utils/index.js": "module.exports = { Api: {} };\n",
        },
        1,
    )
    case(
        "exports.name 形态 → 通过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils/index.js": "exports.Api = {};\n",
        },
        0,
    )
    case(
        "再导出 require → 通过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils/index.js": "module.exports = require('./base');\n",
            "base.js": "module.exports = { Api: {} };\n",
        },
        0,
    )
    case(
        "对象内静态 spread require → 通过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils/index.js": "module.exports = { ...require('./base'), Local: 1 };\n",
            "base.js": "module.exports = { Api: {} };\n",
        },
        0,
    )
    case(
        "未知动态 spread → 保守跳过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils/index.js": "module.exports = { ...loadExports() };\n",
        },
        0,
    )
    case(
        "Object.assign(exports, ...) 动态导出 → 保守跳过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils/index.js": "Object.assign(exports, loadExports());\n",
        },
        0,
    )
    case(
        "导出对象后续动态补属性 → 整模块保守跳过",
        {
            "entry.js": "const { Dynamic } = require('./utils');\nDynamic();\n",
            "utils/index.js": (
                "const all = { Api: 1 };\nall[key] = value;\nmodule.exports = all;\n"
            ),
        },
        0,
    )
    case(
        "目录 index.js 解析 → 违规",
        {
            "entry.js": "const { Missing } = require('./lib');\nMissing();\n",
            "lib/index.js": "module.exports = { Api: {} };\n",
        },
        1,
    )
    case(
        "规则别名解析 → 违规",
        {
            "entry.js": "const { Missing } = require('@/utils');\nMissing();\n",
            "src/utils.js": "module.exports = { Api: {} };\n",
        },
        1,
    )
    case(
        "非仓库相对/别名说明符 → 跳过",
        {"entry.js": "const { Missing } = require('lodash');\nMissing();\n"},
        0,
    )
    case(
        "目标路径不存在由 stale-import 负责 → 本守卫跳过",
        {"entry.js": "const { Missing } = require('./gone');\nMissing();\n"},
        0,
    )
    case(
        "注释和字符串中的 require 样例 → 不误报",
        {
            "entry.js": (
                "// const { Missing } = require('./utils');\n"
                "const sample = \"const { Missing } = require('./utils');\";\n"
                "console.log(sample);\n"
            ),
            "utils.js": "module.exports = { Api: {} };\n",
        },
        0,
    )
    case(
        "正则字面量含引号后仍能识别 module.exports → 通过",
        {
            "entry.js": "const { Api } = require('./utils');\nApi();\n",
            "utils.js": (
                "const quoteRe = /['\"]/g;\nmodule.exports = { Api: quoteRe };\n"
            ),
        },
        0,
    )
    case(
        "skip_paths 命中 → 不检查",
        {
            "skip/entry.js": "const { Missing } = require('../utils');\n",
            "utils.js": "module.exports = { Api: {} };\n",
        },
        0,
    )

    key = violation_key(
        {
            "file": "entry.js",
            "target": "utils.js",
            "name": "Api",
        }
    )
    if key == "entry.js:utils.js:Api#missing-export":
        print("  [v] 基线键包含消费者、目标与缺失导出名")
    else:
        print(f"  [x] 基线键异常：{key}")
        failures += 1

    fake = [{"file": "entry.js", "target": "utils.js", "name": "Api"}]
    related = (
        len(filter_for_files(fake, {"utils.js"})) == 1
        and len(filter_for_files(fake, {"entry.js"})) == 1
        and len(filter_for_files(fake, {"other.js"})) == 0
        and len(filter_for_files(fake, set())) == 0
    )
    if related:
        print("  [v] 暂存过滤：消费者或目标命中均复查，无关/空集不报告")
    else:
        print("  [x] 暂存过滤逻辑异常")
        failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：18/18")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
