#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yamlmini_selftest.py —— yamlmini 的自检用例

从 yamlmini.py 拆出来的：那边是解析逻辑，这边是验证解析逻辑的用例数据。
拆开的直接原因是 yamlmini.py 超过了自己门禁定的 500 行上限 ——
而这不是「靠拆函数凑行数」，用例和实现确实是两件事。

用法：
    python scripts/yamlmini_selftest.py
    python scripts/yamlmini.py          # 等价，会转到本脚本

退出码：0 = 全绿，1 = 有用例失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yamlmini  # noqa: E402
from yamlmini import YamlError, loads  # noqa: E402

CASES: "list[tuple[str, str, object]]" = [
    (
        "简单映射与类型",
        "a: 1\nb: 1.5\nc: true\nd: null\ne: hello\n",
        {"a": 1, "b": 1.5, "c": True, "d": None, "e": "hello"},
    ),
    (
        "引号与行内注释",
        'a: "x # not comment"\nb: y  # real comment\nc: \'z\'\n',
        {"a": "x # not comment", "b": "y", "c": "z"},
    ),
    (
        "嵌套映射",
        "outer:\n  inner: 1\n  deep:\n    x: y\n",
        {"outer": {"inner": 1, "deep": {"x": "y"}}},
    ),
    (
        "标量序列",
        "items:\n  - a\n  - b\n  - 3\n",
        {"items": ["a", "b", 3]},
    ),
    (
        "序列项是映射",
        "tools:\n  - id: t1\n    script: a.py\n    exit: ok\n  - id: t2\n    script: b.py\n",
        {"tools": [{"id": "t1", "script": "a.py", "exit": "ok"}, {"id": "t2", "script": "b.py"}]},
    ),
    (
        "序列项含冒号的值（不该误判为映射）",
        "urls:\n  - http://example.com\n  - ftp://x\n",
        {"urls": ["http://example.com", "ftp://x"]},
    ),
    (
        "深层混合嵌套",
        "hooks:\n  - event: PostToolUse\n    matcher: Write|Edit\n    tool: t1\n    blocking: true\n"
        "  - event: pre-commit\n    tool: t2\n    blocking: false\nrules:\n  - id: r1\n    limit: 500\n",
        {
            "hooks": [
                {"event": "PostToolUse", "matcher": "Write|Edit", "tool": "t1", "blocking": True},
                {"event": "pre-commit", "tool": "t2", "blocking": False},
            ],
            "rules": [{"id": "r1", "limit": 500}],
        },
    ),
    (
        "整行注释与空行",
        "# 头部注释\n\na: 1\n\n# 中间注释\nb: 2\n",
        {"a": 1, "b": 2},
    ),
    (
        "空值为 null",
        "a:\nb: 1\n",
        {"a": None, "b": 1},
    ),
    (
        "嵌套序列中的空值项",
        "list:\n  -\n  - x\n",
        {"list": [None, "x"]},
    ),
    (
        "字面块 |",
        "text: |\n  line1\n  line2\nnext: 1\n",
        {"text": "line1\nline2", "next": 1},
    ),
    (
        "折叠块 >",
        "text: >\n  line1\n  line2\nnext: 1\n",
        {"text": "line1 line2", "next": 1},
    ),
    (
        "序列项是嵌套映射",
        "list:\n  - name: a\n    meta:\n      k: v\n",
        {"list": [{"name": "a", "meta": {"k": "v"}}]},
    ),
    (
        "流式标量序列",
        'args: ["--rules", "x.json"]\nlist: [a, b, 3]\n',
        {"args": ["--rules", "x.json"], "list": ["a", "b", 3]},
    ),
    (
        "空流式序列",
        "a: []\n",
        {"a": []},
    ),
    (
        "流式序列含逗号的引号元素",
        'args: ["a,b", c]\n',
        {"args": ["a,b", "c"]},
    ),
    (
        "frontmatter 抽取",
        "---\nname: x\ndescription: y\n---\n\n# 正文\n",
        None,  # 单独处理
    ),
]

ERROR_CASES: "list[tuple[str, str, str]]" = [
    ("tab 缩进", "a:\n\tb: 1\n", "tab"),
    ("流式序列嵌套", "a: [[1], [2]]\n", "不支持嵌套"),
    ("流式映射", "a: {x: 1}\n", "流式映射"),
    ("锚点", "a: &anchor 1\n", "锚点"),
    ("缺少冒号", "a\nb: 1\n", "找不到冒号"),
    ("重复键", "a: 1\na: 2\n", "键重复"),
    ("意外缩进", "a: 1\n   b: 2\n", "意外缩进"),
    ("缩进层级不一致", "a:\n  b: 1\n c: 2\n", "缩进"),
]


def main() -> int:
    failures = 0

    for entry in CASES:
        name, src, expected = entry[0], entry[1], entry[2]

        if expected is None:  # frontmatter 特例
            fm, body, end = yamlmini.extract_frontmatter(src)
            ok = fm == {"name": "x", "description": "y"} and "# 正文" in body and end == 4
            print(f"  [{'v' if ok else 'x'}] {name}" + ("" if ok else f"  → 实际 {fm!r}"))
            failures += 0 if ok else 1
            continue

        try:
            got = loads(src)
        except YamlError as exc:
            print(f"  [x] {name}\n      解析失败：{exc}")
            failures += 1
            continue
        if got != expected:
            print(f"  [x] {name}\n      期望：{expected!r}\n      实际：{got!r}")
            failures += 1
        else:
            print(f"  [v] {name}")

    for name, src, keyword in ERROR_CASES:
        try:
            got = loads(src)
        except YamlError as exc:
            if keyword in str(exc):
                print(f"  [v] {name}（已按预期报错）")
            else:
                print(f"  [x] {name}\n      报错信息里没有 {keyword!r}：{exc}")
                failures += 1
            continue
        print(f"  [x] {name}\n      本该报错，却解析成了：{got!r}")
        failures += 1

    print()
    total = len(CASES) + len(ERROR_CASES)
    if failures:
        print(f"自检失败：{total - failures}/{total} 通过，{failures} 项异常")
        return 1
    print(f"自检全绿：{total}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
