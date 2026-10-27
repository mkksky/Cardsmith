#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_size_naming_selftest.py —— 体积与命名守卫的回归用例

单独成文件而不是内联在工具里：与 check_dead_code_selftest.py 同一理由 ——
内联后工具本体超过本套件自己要求的 .py 500 行上限（ruff-format 展开后 564 行）。

用例覆盖的边界：
    1. 行数上限      超限报、overrides 设 null 的类型不报
    2. 命名规范      PascalCase / snake_case / dunder 模块名不适用
    3. 二进制资产    zip 容器与未知扩展名不数行数，文本文件照旧检查
    4. --all 收集    只出现在 file_lines.overrides 里的扩展名也要被扫到
    5. 棘轮基线      命中放行、未命中算新增，约定路径定位正确

用法：
    python tools/check_size_naming_selftest.py
    python tools/check_size_naming.py --rules rules/file-size-and-naming-guard.json --selftest

退出码：0 = 全绿，1 = 有用例失败
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import baseline_store
from check_size_naming import all_files, check_one, violation_key

_SELFTEST_RULES = {
    "file_lines": {"default": 10, "overrides": {"*.md": None, "*.js": 8}},
    "naming": {"*.py": "^[a-z][a-z0-9_]*$", "*.java": "^[A-Z][A-Za-z0-9]*$"},
    "skip_paths": [],
}


def run() -> int:
    import tempfile

    failures = 0

    def case(name: str, files: dict, expect: int) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            total = []
            for rel in files:
                total.extend(check_one(rel, root, _SELFTEST_RULES))
            if len(total) == expect:
                print(f"  [v] {name}")
            else:
                print(
                    f"  [x] {name}\n      期望 {expect} 处，实际 {len(total)}：{total}"
                )
                failures += 1

    case("短文件 + 合法命名 → 通过", {"good_name.py": "a = 1\n"}, 0)
    case(
        "超长文件 → 违规", {"short.py": "\n".join(f"x{i} = {i}" for i in range(20))}, 1
    )
    case("PascalCase 的 .py → 命名违规", {"BadName.py": "a = 1\n"}, 1)
    case(
        "PascalCase 的 .java → 通过", {"UserService.java": "class UserService {}\n"}, 0
    )
    case("snake_case 的 .java → 命名违规", {"user_service.java": "class X {}\n"}, 1)
    case(
        "overrides 设 null 的 .md 不查行数",
        {"long.md": "\n".join(str(i) for i in range(50))},
        0,
    )
    case(
        "dunder 模块名（__init__）不适用命名规范",
        {"pkg/__init__.py": "a = 1\n", "pkg/__main__.py": "a = 1\n"},
        0,
    )

    # 二进制资产不数行数：pptx 是 zip 容器，压缩字节里随机出现的 0x0A 会被
    # 当成换行符。真实事故：804 KB 的 .pptx 被判「1531 行，超限 1031 行」。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "手册.pptx").write_bytes(b"PK\x03\x04" + b"\x00\n" * 40)
        (root / "无扩展名二进制.bin").write_bytes(b"\x00" * 200)
        (root / "纯文本.txt").write_text(
            "\n".join(f"line{i}" for i in range(20)), encoding="utf-8"
        )
        binary_issues = check_one("手册.pptx", root, _SELFTEST_RULES) + check_one(
            "无扩展名二进制.bin", root, _SELFTEST_RULES
        )
        text_issues = check_one("纯文本.txt", root, _SELFTEST_RULES)
        if not binary_issues and len(text_issues) == 1:
            print("  [v] 二进制资产不数行数，文本文件照旧检查")
        else:
            print(
                f"  [x] 二进制判定异常\n"
                f"      二进制被报：{binary_issues}\n      文本应报 1 处，实际：{text_issues}"
            )
            failures += 1

    # 守住 all_files 的扩展名收集：.js 只出现在 file_lines.overrides 里，
    # 也必须在 --all 全量扫描中被收集并检查（曾经漏掉，静默失效）
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "big.js").write_text(
            "\n".join("var x = 1" for _ in range(20)), encoding="utf-8"
        )
        (root / "note.md").write_text("text\n" * 50, encoding="utf-8")
        (root / "good_name.py").write_text("a = 1\n", encoding="utf-8")
        collected = all_files(root, _SELFTEST_RULES)
        issues = [i for rel in collected for i in check_one(rel, root, _SELFTEST_RULES)]
        js_in = any(rel.endswith(".js") for rel in collected)
        js_flagged = any(i["file"].endswith(".js") for i in issues)
        if js_in and js_flagged and not any(i["file"].endswith(".md") for i in issues):
            print("  [v] --all 收集 .js 并查行数（overrides 键空间）")
        else:
            print(
                f"  [x] --all 扩展名收集异常\n      收集到：{collected}\n      违规：{issues}"
            )
            failures += 1

    # 基线（棘轮）：命中放行、未命中算新增；约定路径定位正确
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "x.baseline.json"
        bp.write_text(json.dumps({"entries": ["a.py:file-lines"]}), encoding="utf-8")
        keys = baseline_store.load(bp)
        fake = [
            {"file": "a.py", "kind": "file-lines"},
            {"file": "b.py", "kind": "naming"},
        ]
        fresh = [v for v in fake if violation_key(v) not in keys]
        discovered = baseline_store.default_baseline_path(Path("rules/xxx.json"))
        if (
            len(keys) == 1
            and len(fresh) == 1
            and fresh[0]["file"] == "b.py"
            and discovered.as_posix() == "baselines/xxx.baseline.json"
        ):
            print("  [v] 基线过滤与约定路径定位")
        else:
            print(
                f"  [x] 基线逻辑异常：keys={keys} fresh={fresh} discovered={discovered}"
            )
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：10/10")
    return 0


if __name__ == "__main__":
    sys.exit(run())
