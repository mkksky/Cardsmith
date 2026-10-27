#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_dead_code.py —— 失效导入与死导出检查

抓两类「代码死了但没人知道」的事实（都不需要理解业务语义）：

    1. unused-import  文件内 require 绑定了名字，但绑定后全文再没出现过。
       运行时不报错，只是每次加载都白付一笔，且误导读者以为有依赖。
    2. dead-export    模块导出的具名符号，全仓库（含测试）找不到任何一处
       引用。删了不影响任何行为，却占着 API 面迷惑后人。

只查机器能裁断的事实，判断题明确不管：
    - 导出只被测试引用  → 不拦（是否保留 API 面是人的事）
    - 转口文件（index.js 转出口后没人用下游）→ 不拦（图可达性是 v2 的事）
    - 被展开继承的文件（...require('x')）→ 整文件跳过（名字已不可见）
    - 动态拼名字访问（mod[varName]）→ 天然查不到，接受漏报不误报

退出码：0 = 无新增违规（基线存量不阻断）；1 = 有新增违规；2 = 用法/规则错误
（绝不能当通过处理）。

基线（棘轮）：与 check_stale_imports.py 同一约定 —— 键 = 「文件:符号#kind」
（不含行号）。命中基线只提示不阻断，新增违规照常阻断。

用法：
    python tools/check_dead_code.py --rules rules/dead-code-guard.json --all
    python tools/check_dead_code.py --rules <rules.json> --files a.js b.js
    python tools/check_dead_code.py --rules <rules.json> --update-baseline
    python tools/check_dead_code.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_store
import dead_code_collectors as collectors

TIMEOUT_GIT = 30
DEFAULT_EXTS = (".js", ".mjs", ".cjs")


class RuleError(Exception):
    """规则文件的问题。对应退出码 2。"""


# ────────────────────────── 规则 ──────────────────────────


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc
    data["extensions"] = data.get("extensions") or list(DEFAULT_EXTS)
    data.setdefault("skip_paths", [])
    return data


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """把 glob 转成正则。** 跨目录，* 不跨目录。"""
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
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
        elif ch == ".":
            out.append(r"\.")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def is_skipped(rel: str, skip_patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


def violation_key(v: dict) -> str:
    """基线键 = 文件:符号#kind（不含行号，行号随编辑漂移）。"""
    return f"{v['file']}:{v['name']}#{v['kind']}"


# ────────────────────────── 文件收集 ──────────────────────────


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
    out = proc.stdout.decode("utf-8", errors="replace")
    return [line.strip() for line in out.splitlines() if line.strip()]


def all_files(root: Path, rules: dict) -> list[str]:
    exts = {e.lower() for e in rules["extensions"]}
    skip = rules["skip_paths"]
    out = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in f"/{rel}":
            continue
        if is_skipped(rel, skip):
            continue
        out.append(rel)
    return sorted(out)


def check_unused_imports(rel: str, src: str) -> list[dict]:
    """require 绑定的名字，绑定语句之外全文再没出现 → 违规。"""
    bindings = collectors.collect_require_bindings(src)
    if not bindings:
        return []
    # 把所有绑定声明 span 挖掉后作为「使用区」，同名多处绑定一起挖掉
    pieces, last = [], 0
    for b in bindings:
        pieces.append(src[last : b["start"]])
        last = b["end"]
    pieces.append(src[last:])
    usage = "\n".join(pieces)
    out = []
    seen = set()
    for b in bindings:
        n = b["name"]
        if n in seen:
            continue
        seen.add(n)
        if not re.search(rf"\b{re.escape(n)}\b", usage):
            out.append({"file": rel, "name": n, "kind": "unused-import"})
    return out


def check_dead_exports(rel: str, src: str, ref_index: dict) -> list[dict]:
    """导出名在全仓库（除定义文件本身）零文本引用 → 违规。

    ref_index 是 build_ref_index 预构建的「符号 → 引用它的文件集合」倒排索引，
    一次分词全库共享，避免 目标数×语料数 的重复正则扫描。
    """
    names = collectors.collect_export_names(src)
    if not names:
        return []
    return [
        {"file": rel, "name": n, "kind": "dead-export"}
        for n in names
        if not any(f != rel for f in ref_index.get(n, ()))
    ]


def check_one(root: Path, rel: str, rules: dict, ref_ctx=None) -> list[dict]:
    """ref_ctx = build_ref_index 的返回值（预构建的 (倒排索引, 展开基名集)）。
    None 表示只查失效导入，不查死导出。"""
    path = root / rel
    if not path.is_file():
        return []
    ext = path.suffix.lower()
    if ext not in {e.lower() for e in rules["extensions"]}:
        return []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuleError(f"读不了 {rel}：{exc}") from exc

    violations = check_unused_imports(rel, src)

    if ref_ctx is not None:
        ref_index, spread = ref_ctx
        # 文件被展开继承时名字已不可见，整文件跳过 dead-export 检查
        if path.stem not in spread:
            violations.extend(check_dead_exports(rel, src, ref_index))
    return violations


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """回归用例在 check_dead_code_selftest.py —— 内联会让本文件超过 500 行上限。"""
    from check_dead_code_selftest import run

    return run()


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="失效导入与死导出检查（JS/TS）")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="扫描全部源文件（含引用库构建）")
    ap.add_argument("--files", nargs="*", help="只检查指定文件")
    ap.add_argument("--quiet", action="store_true", help="只在有违规时输出")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检")
    ap.add_argument(
        "--baseline",
        help="基线文件路径（默认按约定自动发现 baselines/<rules 名>.baseline.json）",
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
        print(f"[x] 规则问题：{exc}", file=sys.stderr)
        return 2

    skip = rules["skip_paths"]
    try:
        targets = (
            args.files
            if args.files
            else (all_files(root, rules) if args.all else git_staged_files(root))
        )
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    exts = {e.lower() for e in rules["extensions"]}
    targets = [
        t for t in targets if Path(t).suffix.lower() in exts and not is_skipped(t, skip)
    ]

    try:
        # 引用库：--files 模式也全量构建（引用计数必须看全仓库，含跳过项之外的所有源码）
        corpus_files = all_files(root, rules)
        corpus = []
        for cf in corpus_files:
            try:
                corpus.append(
                    (cf, (root / cf).read_text(encoding="utf-8", errors="replace"))
                )
            except OSError:
                continue
        ref_ctx = collectors.build_ref_index(corpus)
        all_violations: list[dict] = []
        for rel in targets:
            all_violations.extend(check_one(root, rel, rules, ref_ctx))
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(v) for v in all_violations]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path,
                keys,
                baseline,
                args.accept_debt,
                args.debt_reason,
                set(targets),
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [v for v in all_violations if violation_key(v) in baseline]
    fresh = [v for v in all_violations if violation_key(v) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处死代码（失效导入 / 零引用导出）：\n")
        for v in fresh:
            where = (
                "require 绑定后全文未使用"
                if v["kind"] == "unused-import"
                else "导出在全仓库（含测试）找不到任何引用"
            )
            print(f"  {v['file']}  [{v['kind']}]  {v['name']}")
            print(f"    {where}")
            print()
        print("修法：失效导入直接删掉绑定；零引用导出确认无动态访问后连实现一起删。")
        print("确实要暂时放行，走 --update-baseline 登记成显式债务。")
        if debts and not args.quiet:
            print(
                f"\n[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）"
            )
        return 1

    if debts and not args.quiet:
        print(
            f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。"
        )
    if not args.quiet:
        print(f"[v] 死代码检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
