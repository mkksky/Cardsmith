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
    - 默认「新增也拦」（保守）。需要让正常追加的新文件通过的目录，规则里写
      allow_new: true —— 只放行 A（新增），修改与删除照拦
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
    # 默认「新增也拦」：保守值。迁移脚本这类受保护目录里，新增同样可能有问题，
    # 需要放行的项目显式写 allow_new: true —— 默认放宽比默认收紧危险得多。
    allow_new = data.setdefault("allow_new", False)
    if not isinstance(allow_new, bool):
        raise RuleError("allow_new 必须是布尔值（true / false）")
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
        raise RuleError(
            f"git 命令失败（当前目录可能不是 git 仓库）：{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout.decode("utf-8", errors="replace")


def parse_name_status(out: str) -> "list[tuple[str, str]]":
    """把 git --name-status 输出解析成 [(状态字母, 路径)]。

    --no-renames 保证改名被拆成 D + A 两条，不会出现三列（R100\\t旧\\t新），
    这样「删了一个迁移脚本」不会因为被识别成改名而漏掉 D 那半。
    """
    changes = []
    for line in out.splitlines():
        parts = [p for p in line.strip().split("\t") if p]
        if len(parts) < 2:
            continue
        changes.append((parts[0][:1].upper(), parts[-1]))
    return changes


def staged_changes(root: Path) -> "list[tuple[str, str]]":
    """暂存区的 (状态, 路径)。**不加 --diff-filter 过滤**：
    受保护路径检查必须覆盖删除（D），ACMR 过滤会漏掉被删的迁移脚本 —— 踩过。"""
    out = _run_git(["diff", "--cached", "--name-status", "--no-renames"], root)
    return parse_name_status(out)


def worktree_changes(root: Path) -> "list[tuple[str, str]]":
    """--all 模式：工作区相对 HEAD 的全部变更（含未暂存），用于摸底。"""
    out = _run_git(["diff", "--name-status", "--no-renames", "HEAD"], root)
    return parse_name_status(out)


# ────────────────────────── 检查 ──────────────────────────


def status_map(changes: "list[tuple[str, str]]") -> "dict[str, str]":
    """[(状态, 路径)] → {路径: 状态}。

    单独抽出来是因为这里踩过：`dict(changes)` 得到的是 {状态: 路径}，键值反了，
    status 永远查不到 → allow_new 静默失效。自检专门有一条守这个方向。
    """
    return {path: status for status, path in changes}


def check_paths(
    rels: "list[str]", rules: dict, statuses: "dict[str, str] | None" = None
) -> "list[dict]":
    """rels 是变更路径；statuses 是路径 → git 状态字母（A/M/D/R）。

    `allow_new: true` 时放行 A（新增）—— 迁移这类目录的正常工作流就是不断追加新脚本，
    把新增也当违规会让门禁变成「每次写迁移都得 --no-verify」，等于亲手废掉这道闸。
    statuses 缺省（--files 显式点名）时不做放行：点名检查就是要看它算不算违规。
    """
    allow_new = rules.get("allow_new") is True
    issues = []
    for rel in rels:
        rel = rel.replace("\\", "/")
        if is_skipped(rel, rules["skip_paths"]):
            continue
        status = (statuses or {}).get(rel, "")
        if allow_new and status == "A":
            continue
        if is_protected(rel, rules["protected_paths"]):
            issues.append({"file": rel, "status": status, "why": rules["why"]})
    return issues


# ────────────────────────── 自检 ──────────────────────────


_SELFTEST_RULES = {
    "protected_paths": ["database/migrations/**", "schema.prisma"],
    "skip_paths": ["**/*.generated.*"],
    "why": "测试用受保护路径",
}


def _selftest() -> int:
    failures = 0
    total = 0
    allow_new_rules = {**_SELFTEST_RULES, "allow_new": True}

    def case(
        name: str,
        rels: "list[str]",
        expect: int,
        rules: "dict | None" = None,
        statuses: "dict[str, str] | None" = None,
    ) -> None:
        nonlocal failures, total
        total += 1
        got = check_paths(rels, rules or _SELFTEST_RULES, statuses)
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

    # allow_new：只放行「新增」，修改仍拦
    m = "database/migrations/2026_init.sql"
    new_m = "database/migrations/2027_new.sql"
    case("默认（无 allow_new）新增仍拦", [new_m], 1, statuses={new_m: "A"})
    case("allow_new + 新增 → 通过", [new_m], 0, allow_new_rules, {new_m: "A"})
    case("allow_new + 修改 → 仍违规", [m], 1, allow_new_rules, {m: "M"})
    case("allow_new + 删除 → 仍违规", [m], 1, allow_new_rules, {m: "D"})
    case("allow_new + 状态缺失 → 保守拦下", [new_m], 1, allow_new_rules, {})

    # 集成层回归：changes → {路径: 状态} 的方向
    # （曾写成 dict(changes)，得到 {状态: 路径}，status 永远查不到 → allow_new 静默失效）
    total += 1
    sm = status_map([("A", "new.sql"), ("M", "old.sql")])
    if sm.get("new.sql") == "A" and sm.get("old.sql") == "M" and "A" not in sm:
        print("  [v] status_map 方向：路径 → 状态")
    else:
        print(f"  [x] status_map 方向异常：{sm}")
        failures += 1

    # git --name-status 输出解析
    total += 1
    parsed = parse_name_status("A\tnew.sql\nM\told.sql\nD\tgone.sql\n")
    if parsed == [("A", "new.sql"), ("M", "old.sql"), ("D", "gone.sql")]:
        print("  [v] parse_name_status 解析正确")
    else:
        print(f"  [x] parse_name_status 异常：{parsed}")
        failures += 1

    # glob 语义回归：**/ 与 * 的区别
    total += 1
    r = glob_to_regex("database/migrations/**")
    if r.match("database/migrations/a.sql") and not r.match("other/a.sql"):
        print("  [v] glob ** 语义：匹配子树、不越界")
    else:
        print("  [x] glob ** 语义异常")
        failures += 1

    print()
    if failures:
        print(f"自检失败：{failures}/{total} 项异常")
        return 1
    print(f"自检全绿：{total}/{total}")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="受保护路径守卫")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument(
        "--all", action="store_true", help="对比 HEAD 的工作区变更（摸底用）"
    )
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
            statuses: "dict[str, str]" = {}
        elif args.all:
            changes = worktree_changes(root)
            targets = [p for _, p in changes]
            statuses = status_map(changes)
        else:
            changes = staged_changes(root)
            targets = [p for _, p in changes]
            statuses = status_map(changes)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    issues = check_paths(targets, rules, statuses)

    if issues:
        print(f"[x] 受保护路径出现 {len(issues)} 处变更：\n")
        for i in issues:
            print(f"  [{i['status'] or '?'}] {i['file']}")
            print(f"    {i['why']}")
            print()
        print("这些文件落地后视为只读。确需变更：先在评审里说清影响面与回滚路径，")
        print("评审通过后由负责人执行，而不是混在业务提交里顺手改掉。")
        if rules.get("allow_new") is True:
            print("（本规则已开 allow_new：新增文件放行，上面都是对已有文件的改动）")
        return 1

    if not args.quiet:
        print(f"[v] 受保护路径检查通过（本次变更 {len(targets)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
