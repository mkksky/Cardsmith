#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_doc_sync.py —— 文档同步守卫

文档漂移是慢性的：代码改了文档没跟上，当时没人发现，三个月后文档比没有更毒
（照着过期文档调接口，参数对不上）。本工具抓三类**机器可裁断**的漂移：

    1. path-ref   文档里引用的文件/目录在磁盘上不存在（路径时效性）
    2. api-ref    文档声明的方法/类在源码 AST 里不存在（签名漂移，v1 只查名字）
    3. new-file-doc-missing
                  项目显式配置 new_file_docs 后，新增源码未被指定文档引用

明确不做的（做不了/不该做）：
    - 判断文档"内容还对不对" —— 那是判断题，工具只能查事实
    - 比对参数默认值 —— 误报高，v1 只查名字存在性

路径解析顺序（命中任一即通过；引用前导 `/` 按项目根相对处理）：
    <文档所在目录>/<ref>  →  <项目根>/<ref>  →  <项目根>/<source_root>/<ref>
    →  <项目根>/<cf_doc_bases 推导基>/<ref>
    第三级是为「文档省略仓库子目录前缀」的项目准备的（如 monorepo 里
    源码在 backend/ 下，文档写 application/...）；第四级是
    云函数文档邻域（docs/05-cloudfunctions/<fn>/ 的 README 写云函数内
    相对路径）。引用尾部的行号/符号锚点/查询串（`xxx.js:64` 等）查盘前
    剥掉；末段无扩展名的 pages/ 路由补 .js 探测。

检查范围（两种模式）：
    --all      扫 doc_roots 白名单里的全部文档
    staged     git 暂存模式（pre-commit 走这条）：白名单文档直接查 +
               **反向并入** —— 暂存的代码路径反查哪些白名单文档引用了它，
               命中的文档一并校对。v1 只看被暂存的文档，提交里只有代码时
               文档检查完全沉默，哪怕文档引用的路径刚被删掉（2026-10-21 修）。

退出码：0=通过或无可查文件 1=有新增违规 2=规则或配置错误
基线：键 = <文档>:<ref或符号>:<kind>，不含行号。--update-baseline 登记存量。

职责拆分（500 行门禁所致，与 doc_sync_selftest.py 同一先例）：
    tools/doc_sync_checks.py   三类检查本体 + 文档收集
    tools/doc_sync_paths.py    路径解析、glob、暂存清单解析
    tools/doc_sync_refs.py     从 markdown 抽引用（纯函数）
    tools/doc_sync_reverse.py  反向并入的匹配逻辑

被卡片引用：
    cards/doc-sync-guard.md

用法：
    python tools/check_doc_sync.py --rules rules/doc-sync-guard.json
    python tools/check_doc_sync.py --rules <rules.json> --all
    python tools/check_doc_sync.py --rules <rules.json> --files README.md
    python tools/check_doc_sync.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store  # noqa: E402
from doc_sync_checks import (  # noqa: E402
    check_api_doc,
    check_new_file_docs,
    check_path_refs,
    collect_doc_files,
)
from doc_sync_paths import compile_allow_patterns, glob_to_regex, is_skipped  # noqa: E402
from doc_sync_paths import (  # noqa: E402
    added_paths,
    parse_name_status_records,
    record_paths,
)
from doc_sync_reverse import api_docs_triggered, docs_referencing  # noqa: E402

TIMEOUT_GIT = 30


class RuleError(Exception):
    """规则文件的问题。对应退出码 2。"""


# ────────────────────────── 规则 ──────────────────────────


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuleError(f"规则文件读不了：{path}\n    {exc}") from exc
    for key in ("doc_roots", "doc_extensions", "path_suffixes", "skip_paths"):
        if not isinstance(rules.get(key), list):
            raise RuleError(f"规则缺少 {key} 列表：{path}")
    for key in (
        "source_roots",
        "allow_refs",
        "allow_ref_patterns",
        "skip_symbols",
        "api_docs",
        "cf_doc_bases",
        "new_file_docs",
    ):
        rules.setdefault(key, [])
    rules.setdefault("check_bare_names", False)
    # api_docs 的必填项在加载期校验：配置错误属于退出码 2，不该拖到检查期才崩
    for entry in rules.get("api_docs", []):
        if not isinstance(entry, dict) or not entry.get("doc"):
            raise RuleError("api_docs 条目缺少 doc 字段")
        if not entry.get("search_roots"):
            raise RuleError(f"api_docs 条目 {entry.get('doc')} 缺少 search_roots")
    for index, entry in enumerate(rules.get("new_file_docs", []), start=1):
        if not isinstance(entry, dict):
            raise RuleError(f"new_file_docs 第 {index} 条必须是对象")
        for key in ("source", "docs"):
            value = entry.get(key)
            values = [value] if isinstance(value, str) else value
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(item, str) and item.strip() for item in values)
            ):
                raise RuleError(
                    f"new_file_docs 第 {index} 条缺少有效的 {key}（字符串或非空字符串数组）"
                )
    # 形态白名单在加载期编译：坏正则属配置错误（退出码 2），不能拖到检查时才崩
    try:
        rules["_compiled_allow_patterns"] = compile_allow_patterns(
            rules["allow_ref_patterns"]
        )
    except (TypeError, re.error) as exc:
        raise RuleError(f"allow_ref_patterns 配置非法：{exc}") from exc
    return rules


# ────────────────────────── 暂存清单 ──────────────────────────


def git_staged_records(root: Path) -> "list[dict]":
    """暂存区里的改动记录（保留状态与重命名旧路径）。

    含新增/修改/重命名（目标路径）、**删除的路径**、**重命名的旧路径** ——
    删除与旧路径是文档漂移的高发场景：文件挪了或没了，文档还指着它，
    只看 ACMR 等于把最该抓的漂移放过去（2026-10-21）。
    """
    try:
        proc = subprocess.run(
            # core.quotePath=false：默认会把中文路径转义成 "\345\260..." 这类字面串，
            # 路径解析不到文件 → 检查静默通过（2026-10-10 修）
            # -z：路径以 NUL 分隔，特殊字符路径不用引号转义，解析不会错
            [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--cached",
                "--name-status",
                "-z",
            ],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    return parse_name_status_records(proc.stdout.decode("utf-8", errors="replace"))


def git_staged_files(root: Path) -> "list[str]":
    """兼容旧调用方：返回暂存记录展开后的全部路径。"""
    return record_paths(git_staged_records(root))


# ────────────────────────── 编排 ──────────────────────────


def run_doc_checks(
    paths: "list[str]",
    root: Path,
    rules: dict,
    args,
    whitelist_only: bool,
    new_paths: "list[str] | None" = None,
) -> "tuple[list[dict], set[str]]":
    """统一编排：文档直接查 + 代码反查并入 + 可选新增文件文档覆盖。

    whitelist_only=True（git 暂存模式）：只有 doc_roots 白名单里的文档直接查，
    反查并入也只并入白名单文档 —— 白名单纪律在提交时最容易被绕过。
    whitelist_only=False（--files 显式指定）：给定的 md 都直接查，但反查
    并入仍只从白名单里找（不该因为点名了一个文件就把整个文档树拖进来）。
    """
    doc_exts = tuple(e.lower() for e in rules["doc_extensions"])
    allowed = set(collect_doc_files(root, rules))
    api_entries = rules.get("api_docs", [])
    api_map = {
        str(e.get("doc", "")).replace("\\", "/"): e for e in api_entries
    }
    issues: list = []
    checked: set = set()
    code_paths: list = []
    for rel in paths:
        rel = rel.replace("\\", "/")
        if is_skipped(rel, rules["skip_paths"]):
            continue
        if rel in api_map:
            checked.add(rel)
            issues += check_api_doc(api_map[rel], root, rules)
        if rel.lower().endswith(doc_exts) and (not whitelist_only or rel in allowed):
            checked.add(rel)
            issues += check_path_refs(rel, root, rules)
        else:
            code_paths.append(rel)
    issues += check_new_file_docs(new_paths or [], root, rules)
    if not code_paths:
        return issues, checked
    # 反向并入：代码改了（哪怕文档没被暂存）→ 引用了它的白名单文档一并查
    hits = docs_referencing(code_paths, sorted(allowed - checked), root, rules)
    for doc, hit_paths in hits.items():
        checked.add(doc)
        issues += check_path_refs(doc, root, rules)
        if not args.quiet:
            print(f"[i] 反查并入：{'、'.join(hit_paths)} 被 {doc} 引用，一并校对")
    for doc in api_docs_triggered(code_paths, api_entries):
        if doc in checked or doc not in api_map:
            continue
        checked.add(doc)
        issues += check_api_doc(api_map[doc], root, rules)
        if not args.quiet:
            print(f"[i] 反查并入：search_roots 覆盖本次改动，{doc} 一并对账")
    return issues, checked


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="文档同步检查（路径时效性 + API 签名对账）"
    )
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument(
        "--baseline",
        help="基线文件路径（默认自动发现 baselines/<rules 名>.baseline.json）",
    )
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="把当前违规写入基线（棘轮登记），退出码 0",
    )
    ap.add_argument(
        "--accept-debt",
        action="store_true",
        help="棘轮闸门：本次会新增债务时，显式认账放行（必须配 --debt-reason）",
    )
    ap.add_argument(
        "--debt-reason", default="", help="认账理由，会记进基线文件的 accepted 段"
    )
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    try:
        rules = load_rules(Path(args.rules))
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    scanned: set = set()
    try:
        if args.files is not None:
            given = [f.replace("\\", "/") for f in args.files if f]
            issues, checked = run_doc_checks(given, root, rules, args, False)
            scanned = set(given) | checked
        elif args.all:
            issues = []
            for doc in collect_doc_files(root, rules):
                if not is_skipped(doc, rules["skip_paths"]):
                    scanned.add(doc)
                    issues += check_path_refs(doc, root, rules)
            for entry in rules.get("api_docs", []):
                api_doc = str(entry.get("doc", "")).replace("\\", "/")
                if api_doc:
                    scanned.add(api_doc)
                issues += check_api_doc(entry, root, rules)
        else:
            records = git_staged_records(root)
            staged = record_paths(records)
            issues, checked = run_doc_checks(
                staged, root, rules, args, True, added_paths(records)
            )
            scanned = {f.replace("\\", "/") for f in staged} | checked
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    return report(issues, Path(args.rules), args, scanned)


def report(
    issues: "list[dict]", rules_path: Path, args, scanned: "set[str] | None" = None
) -> int:
    try:
        baseline_path, baseline = baseline_store.open_baseline(
            rules_path, args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(i) for i in issues]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path,
                keys,
                baseline,
                args.accept_debt,
                args.debt_reason,
                scanned,
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [i for i in issues if violation_key(i) in baseline]
    fresh = [i for i in issues if violation_key(i) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处文档漂移：\n")
        for i in fresh:
            print(f"  {i['file']}  [{i['kind']}]")
            label = "应登记于" if i["kind"] == "new-file-doc-missing" else "引用"
            print(f"    {label}：{i['ref']}")
            if i["why"]:
                print(f"    为什么拦：{i['why']}")
            print()
        print("文档比没有更毒：照着过期文档调接口，参数对不上还以为是自己错。")
        print("改代码时同步改文档；确实暂时放行，走 --update-baseline 登记成显式债务。")
        print()
        if debts and not args.quiet:
            print(
                f"[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）"
            )
        return 1

    if debts and not args.quiet:
        print(
            f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。"
        )
    if not args.quiet:
        print("[v] 文档同步检查通过")
    return 0


def violation_key(issue: dict) -> str:
    return f"{issue['file']}:{issue['kind']}:{issue['ref']}"


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """用例在 doc_sync_selftest.py —— 主文件守住 500 行上限（size-naming 门禁）。"""
    import doc_sync_selftest

    return doc_sync_selftest.run_selftest()


if __name__ == "__main__":
    sys.exit(main())
