#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_module_contracts.py —— CommonJS 模块导出契约检查

抓「消费方明确解构了某个导出，目标模块却没有提供」的确定性故障。
与 dead-code-guard 互补：前者查接口缺失，后者查接口没人用。

只检查 CommonJS：
    const { A, B: alias } = require('./module')

目标模块可静态判定时必须完整解析导出面；一旦遇到动态补属性、动态 spread、
计算属性等写法，整模块保守跳过，避免把“看不懂”误报成“没有导出”。

用法：
    python tools/check_module_contracts.py --rules rules/module-contract-guard.json --all
    python tools/check_module_contracts.py --rules <rules.json> --files a.js
    python tools/check_module_contracts.py --rules <rules.json> --update-baseline
    python tools/check_module_contracts.py --rules <rules.json> --selftest

退出码：0=通过或仅存量债务；1=有新增契约违规；2=规则/用法错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import baseline_store
import module_contract_collectors as collectors

TIMEOUT_GIT = 30
DEFAULT_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


class RuleError(Exception):
    """规则或用法问题。对应退出码 2。"""


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc
    data["extensions"] = data.get("extensions") or list(DEFAULT_EXTS)
    data["skip_paths"] = data.get("skip_paths") or []
    data["aliases"] = data.get("aliases") or {}
    return data


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i : i + 3] == "**/":
                    out.append(r"(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == ".":
            out.append(r"\.")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def is_skipped(rel: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(pattern).match(rel) for pattern in patterns)


def violation_key(violation: dict) -> str:
    """键不含行号；同一消费者从同一目标缺同一导出才算同一条债务。"""
    return (
        f"{violation['file']}:{violation['target']}:{violation['name']}#missing-export"
    )


def git_staged_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    text = proc.stdout.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def all_files(root: Path, rules: dict) -> list[str]:
    exts = {ext.lower() for ext in rules["extensions"]}
    patterns = rules["skip_paths"]
    files = []
    # 用 os.walk 而不是 rglob：rglob 会先递归进 skip_paths 里的大目录
    # （node_modules 十万级文件，单次钩子实测 50s 直接超时），再在结果里过滤。
    # 这里在 dirnames 上就地剪枝，不进被排除的目录树。
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [
            d for d in dirnames if not is_skipped(prefix + d + "/", patterns)
        ]
        for name in filenames:
            if Path(name).suffix.lower() not in exts:
                continue
            rel = prefix + name
            if rel.startswith(".git/") or "/.git/" in f"/{rel}":
                continue
            if is_skipped(rel, patterns):
                continue
            files.append(rel)
    return sorted(files)


def resolve_module(
    root: Path, consumer: Path, specifier: str, rules: dict
) -> "Path | None":
    if specifier.startswith("./") or specifier.startswith("../"):
        base = consumer.parent / specifier
    else:
        match = next(
            (
                prefix
                for prefix in sorted(rules["aliases"], key=len, reverse=True)
                if specifier.startswith(prefix)
            ),
            None,
        )
        if match is None:
            return None
        base = root / rules["aliases"][match] / specifier[len(match) :]
    base = base.resolve()
    ext_list = [ext.lower() for ext in rules["extensions"]]
    candidates = [base]
    candidates.extend(Path(str(base) + ext) for ext in ext_list)
    candidates.extend(base / f"index{ext}" for ext in ext_list)
    for candidate in candidates:
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate.resolve()
    return None


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def export_contract(
    root: Path,
    target: Path,
    rules: dict,
    cache: dict,
    seen: "set[Path] | None" = None,
) -> dict:
    """递归解析静态导出面；无法判定时返回 complete=False。"""
    target = target.resolve()
    cached = cache.get(target)
    if cached is not None:
        return cached
    seen = set() if seen is None else set(seen)
    if target in seen:
        return {"names": set(), "complete": False}
    seen.add(target)

    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"names": set(), "complete": False}
    summary = collectors.collect_export_summary(source)
    names = set(summary.get("names") or [])
    complete = bool(summary.get("complete", False))
    kind = summary.get("kind")

    if kind == "object":
        for specifier in summary.get("spreads") or []:
            nested_target = resolve_module(root, target, specifier, rules)
            if nested_target is None:
                complete = False
                continue
            nested = export_contract(root, nested_target, rules, cache, seen)
            names.update(nested["names"])
            complete = complete and nested["complete"]
    elif kind == "identifier":
        members = collectors.collect_variable_object(source, summary["identifier"])
        if members is None:
            complete = False
        else:
            names.update(members["names"])
            complete = complete and members["complete"]
            for specifier in members["spreads"]:
                nested_target = resolve_module(root, target, specifier, rules)
                if nested_target is None:
                    complete = False
                    continue
                nested = export_contract(root, nested_target, rules, cache, seen)
                names.update(nested["names"])
                complete = complete and nested["complete"]
    elif kind == "require":
        specifier = (summary.get("spreads") or [None])[0]
        nested_target = (
            resolve_module(root, target, specifier, rules) if specifier else None
        )
        if nested_target is None:
            complete = False
        else:
            nested = export_contract(root, nested_target, rules, cache, seen)
            names.update(nested["names"])
            complete = nested["complete"]
    elif kind == "dynamic":
        complete = False

    result = {"names": names, "complete": complete}
    cache[target] = result
    return result


def collect_contract_violations(root: Path, rules: dict) -> list[dict]:
    consumers = all_files(root, rules)
    export_cache: dict = {}
    violations = []
    for rel in consumers:
        consumer = (root / rel).resolve()
        try:
            source = consumer.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for imported in collectors.collect_destructured_requires(source):
            target = resolve_module(root, consumer, imported["specifier"], rules)
            if target is None:
                continue  # 不存在路径由 stale-import-guard 负责
            contract = export_contract(root, target, rules, export_cache)
            if not contract["complete"]:
                continue
            target_rel = _relative(root, target)
            for name in imported["names"]:
                if name in contract["names"]:
                    continue
                violations.append(
                    {
                        "file": rel,
                        "target": target_rel,
                        "name": name,
                        "line": imported["line"],
                        "specifier": imported["specifier"],
                        "kind": "missing-export",
                    }
                )
    return violations


def filter_for_files(violations: list[dict], selected: set[str]) -> list[dict]:
    """staged/files 只过滤报告范围；消费者或目标任一侧被点名都算相关。"""
    if not selected:
        return []
    return [
        violation
        for violation in violations
        if violation["file"] in selected or violation["target"] in selected
    ]


def _selftest() -> int:
    from check_module_contracts_selftest import run

    return run()


def main() -> int:
    parser = argparse.ArgumentParser(description="CommonJS 模块导出契约检查")
    parser.add_argument("--rules", help="规则 JSON 路径")
    parser.add_argument("--root", default=".", help="项目根")
    parser.add_argument("--all", action="store_true", help="扫描全部源码")
    parser.add_argument("--files", nargs="*", help="只报告与这些文件相关的契约")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--baseline", help="基线文件路径")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--accept-debt", action="store_true")
    parser.add_argument("--debt-reason", default="")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()
    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    try:
        rules = load_rules(Path(args.rules))
        selected = (
            set(args.files)
            if args.files
            else set()
            if args.all
            else set(git_staged_files(root))
        )
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    violations = collect_contract_violations(root, rules)
    if not args.all:
        violations = filter_for_files(violations, selected)

    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(v) for v in violations]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path,
                keys,
                baseline,
                args.accept_debt,
                args.debt_reason,
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [v for v in violations if violation_key(v) in baseline]
    fresh = [v for v in violations if violation_key(v) not in baseline]
    if fresh:
        print(f"[x] 发现 {len(fresh)} 处模块导出契约违规：\n")
        for violation in fresh:
            print(
                f"  {violation['file']}:{violation['line']}  "
                f"require('{violation['specifier']}') 解构 {violation['name']}"
            )
            print(f"    但 {violation['target']} 未导出该成员")
        print("\n修法：补齐目标模块导出，或修正消费方的解构名。")
        if debts and not args.quiet:
            print(f"\n[!] 另有 {len(debts)} 处已登记存量债务，已放行。")
        return 1

    if debts and not args.quiet:
        print(f"[!] {len(debts)} 处存量债务在基线中，已放行。")
    if not args.quiet:
        print("[v] 模块导出契约检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
