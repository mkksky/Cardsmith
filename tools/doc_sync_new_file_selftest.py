# -*- coding: utf-8 -*-
"""new_file_docs 的可选规则自检。

独立文件的原因与 doc_sync_selftest.py 相同：主自检接近 500 行门禁。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from check_doc_sync import RuleError, load_rules, run_doc_checks
from doc_sync_checks import check_new_file_docs
from doc_sync_paths import added_paths, glob_to_regex, parse_name_status_records


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rules(*, source, docs) -> dict:
    return {
        "doc_roots": ["docs"],
        "doc_extensions": [".md"],
        "source_roots": [],
        "cf_doc_bases": [],
        "path_suffixes": [".py", ".md", ".json"],
        "skip_paths": [],
        "allow_refs": [],
        "allow_ref_patterns": [],
        "skip_symbols": [],
        "api_docs": [],
        "new_file_docs": [{"source": source, "docs": docs}],
    }


class _Args:
    quiet = True


def run_selftest() -> "tuple[int, int]":
    """返回 (运行数, 失败数)，由主自检汇总。"""
    ran = 0
    failures = 0

    def check(name: str, expect_count: int, build) -> None:
        nonlocal ran, failures
        ran += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            new_paths, rules = build(root)
            issues = check_new_file_docs(new_paths, root, rules)
            ok = len(issues) == expect_count
            print(f"  [{'v' if ok else 'x'}] {name}")
            if not ok:
                print(f"      期望 {expect_count} 处，实际 {len(issues)}：{issues}")
                failures += 1

    check(
        "new_file_docs：新增文件被文档引用 → 通过",
        0,
        lambda r: (
            ["src/new_service.py"],
            (
                _write(r, "docs/architecture.md", "见 `src/new_service.py`\n"),
                _rules(source=["src/**/*.py"], docs=["docs/architecture.md"]),
            )[-1],
        ),
    )

    check(
        "new_file_docs：新增文件未被文档引用 → 违规",
        1,
        lambda r: (
            ["src/new_service.py"],
            (
                _write(r, "docs/architecture.md", "架构说明\n"),
                _rules(source=["src/**/*.py"], docs=["docs/architecture.md"]),
            )[-1],
        ),
    )

    check(
        "new_file_docs：目录级引用覆盖其下新增文件",
        0,
        lambda r: (
            ["src/modules/new_service.py"],
            (
                _write(r, "docs/architecture.md", "见 `src/modules/`\n"),
                _rules(source=["src/**/*.py"], docs=["docs/architecture.md"]),
            )[-1],
        ),
    )

    check(
        "new_file_docs：source 未匹配的新增文件不检查",
        0,
        lambda r: (
            ["tests/test_new.py"],
            (
                _write(r, "docs/architecture.md", "架构说明\n"),
                _rules(source=["src/**/*.py"], docs=["docs/architecture.md"]),
            )[-1],
        ),
    )

    glob_ok = bool(glob_to_regex("src/**/*.py").match("src/new_service.py"))
    ran += 1
    print(f"  [{'v' if glob_ok else 'x'}] glob：中间的 **/ 可匹配零层目录")
    if not glob_ok:
        failures += 1

    check(
        "new_file_docs：指定文档不存在 → 明确报错",
        1,
        lambda r: (
            ["src/new_service.py"],
            _rules(source="src/**/*.py", docs="docs/missing.md"),
        ),
    )

    records = parse_name_status_records(
        "A\0src/new.py\0M\0src/changed.py\0R100\0src/old.py\0src/renamed.py\0"
        "C100\0src/base.py\0src/copied.py\0"
    )
    got = added_paths(records)
    ok = got == ["src/new.py", "src/renamed.py", "src/copied.py"]
    ran += 1
    print(f"  [{'v' if ok else 'x'}] new_file_docs：只把 A / R / C 目标路径视为新增")
    if not ok:
        print(f"      实际：{got}")
        failures += 1

    with tempfile.TemporaryDirectory() as td:
        rules_path = Path(td) / "rules.json"
        rules_path.write_text(
            json.dumps(
                {
                    "doc_roots": [],
                    "doc_extensions": [".md"],
                    "source_roots": [],
                    "path_suffixes": [".py"],
                    "skip_paths": [],
                    "new_file_docs": [{"source": [], "docs": ["docs/a.md"]}],
                }
            ),
            encoding="utf-8",
        )
        try:
            load_rules(rules_path)
        except RuleError:
            ok = True
        else:
            ok = False
    ran += 1
    print(f"  [{'v' if ok else 'x'}] new_file_docs：非法规则配置返回配置错误")
    if not ok:
        failures += 1

    def orchestration_case(name: str, new_paths: list, expect_count: int) -> None:
        nonlocal ran, failures
        ran += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "docs/architecture.md", "架构说明\n")
            rules = _rules(source=["src/**/*.py"], docs=["docs/architecture.md"])
            issues, _ = run_doc_checks(
                ["src/new_service.py"],
                root,
                rules,
                _Args(),
                True,
                new_paths=new_paths,
            )
            ok = len(issues) == expect_count
            print(f"  [{'v' if ok else 'x'}] {name}")
            if not ok:
                print(f"      期望 {expect_count} 处，实际 {len(issues)}：{issues}")
                failures += 1

    orchestration_case(
        "编排：新增路径接入 new_file_docs 覆盖检查",
        ["src/new_service.py"],
        1,
    )
    orchestration_case(
        "编排：既有文件修改不误触发新增文件规则",
        [],
        0,
    )

    return ran, failures
