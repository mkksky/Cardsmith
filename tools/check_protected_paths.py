#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_protected_paths.py —— 受保护路径守卫

有些目录一旦落地就不许再动，动了就必须走显式流程（评审、专门的迁移工具等），
典型：数据库迁移脚本。靠人记住「这个目录别碰」不可靠 —— 记忆会漂移，
 Especially 多机协作时另一台机器上的人根本不知道这条规矩。

本工具把它变成门禁：git 暂存区（或指定文件）里出现受保护路径下的任何变更
（增 / 改 / 删 / 改名）就报违规。删除也在检查范围内 —— 删迁移脚本比改它更危险。

设计取舍：
    - 不解析文件内容，只看路径。内容级检查（迁移是否可回滚）是另一个工具的事
    - --all 模式对比 HEAD 工作区差异，用于摸底和端到端验证
    - 支持 --selftest；退出码 0=通过 1=有违规 2=配置错误

被卡片引用：
    cards/db-migration-guard.md

用法：
    python tools/check_protected_paths.py --rules rules/db-migration-guard.json
    python tools/check_protected_paths.py --rules <rules.json> --files a.sql b.py
    python tools/check_protected_paths.py --rules <rules.json> --all
    python tools/check_protected_paths.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TIMEOUT_GIT = 30


class RuleError(Exception):
    pass


# ────────────────────────── 规则 ──────────────────────────


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    protected = data.get("protected_paths")
    if not isinstance(protected, list) or not protected:
        raise RuleError("规则文件缺少 protected_paths 列表（至少一条 glob）")

    data.setdefault("skip_paths", [])
    data.setdefault("why", "受保护目录：变更必须走显式流程，不许顺手改")
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


def is_protected(rel: str, patterns: list) -> bool:
    return any(glob_to_regex(p).match(rel) for p in patterns)


def is_skipped(rel: str, skip_patterns: list) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


# ────────────────────────── 变更收集 ──────────────────────────


def _run_git(args: list, root: Path) -> str:
    try:
        proc = subprocess.run(
            # core.quotePath=false：默认会把中文路径转义成 "\345\260..." 这类字面串，
            # 路径解析不到文件 → 检查静默通过（2026-10-10 修）
            ["git", "-c", "core.quotePath=false", *args],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"git 命令失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError(f"git 命令失败（当前目录可能不是 git 仓库）：{proc.stderr.decode('utf-8', errors='replace').strip()}")
    return proc.stdout.decode("utf-8", errors="replace")


def staged_files(root: Path) -> "list[str]":
    """暂存区的全部变更路径。**不加 --diff-filter 过滤**：
    受保护路径检查必须覆盖删除（D），ACMR 过滤会漏掉被删的迁移脚本 —— 踩过。"""
    out = _run_git(["diff", "--cached", "--name-only"], root)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def worktree_changed_files(root: Path) -> "list[str]":
    """--all 模式：工作区相对 HEAD 的全部变更（含未暂存），用于摸底。"""
    out = _run_git(["diff", "--name-only", "HEAD"], root)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ────────────────────────── 检查 ──────────────────────────


def check_paths(rels: "list[str]", rules: dict) -> "list[dict]":
    issues = []
    for rel in rels:
        rel = rel.replace("\\", "/")
        if is_skipped(rel, rules["skip_paths"]):
            continue
        if is_protected(rel, rules["protected_paths"]):
            issues.append({"file": rel, "why": rules["why"]})
    return issues


# ────────────────────────── 自检 ──────────────────────────


_SELFTEST_RULES = {
    "protected_paths": ["database/migrations/**", "schema.prisma"],
    "skip_paths": ["**/*.generated.*"],
    "why": "测试用受保护路径",
}


def _selftest() -> int:
    failures = 0

    def case(name: str, rels: "list[str]", expect: int) -> None:
        nonlocal failures
        got = check_paths(rels, _SELFTEST_RULES)
        if len(got) == expect:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}\n      期望 {expect} 处，实际 {len(got)}：{got}")
            failures += 1

    case("迁移目录下的修改 → 违规", ["database/migrations/2026_init.sql"], 1)
    case("迁移目录深层嵌套 → 违规", ["database/migrations/sub/dir/x.sql"], 1)
    case("迁移目录外 → 通过", ["database/seeds/2026_init.sql"], 0)
    case("迁移目录的同级相似名 → 通过", ["database/migrations_backup/x.sql"], 0)
    case("受保护的单文件 → 违规", ["schema.prisma"], 1)
    case("generated 豁免优先于受保护", ["database/migrations/x.generated.sql"], 0)
    case("Windows 反斜杠路径归一后仍命中", ["database\\migrations\\x.sql"], 1)
    case("空列表 → 通过", [], 0)

    # glob 语义回归：**/ 与 * 的区别
    r = glob_to_regex("database/migrations/**")
    if r.match("database/migrations/a.sql") and not r.match("other/a.sql"):
        print("  [v] glob ** 语义：匹配子树、不越界")
    else:
        print("  [x] glob ** 语义异常")
        failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：9/9")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="受保护路径守卫")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true", help="对比 HEAD 的工作区变更（摸底用）")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    try:
        rules = load_rules(Path(args.rules))
        if args.files:
            targets = list(args.files)
        elif args.all:
            targets = worktree_changed_files(root)
        else:
            targets = staged_files(root)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    issues = check_paths(targets, rules)

    if issues:
        print(f"[x] 受保护路径出现 {len(issues)} 处变更：\n")
        for i in issues:
            print(f"  {i['file']}")
            print(f"    {i['why']}")
            print()
        print("这些文件落地后视为只读。确需变更：先在评审里说清影响面与回滚路径，")
        print("评审通过后由负责人执行，而不是混在业务提交里顺手改掉。")
        return 1

    if not args.quiet:
        print(f"[v] 受保护路径检查通过（本次变更 {len(targets)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
