#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_size_naming.py —— 文件体积与命名规范检查

两条规则：
    1. 单文件行数上限（超限 = 单一职责被破坏的信号）
    2. 文件名命名规范（Python 模块 snake_case、Java 类 PascalCase、Vue 组件 PascalCase 等）

被卡片引用：
    cards/file-size-and-naming-guard.md

用法：
    python tools/check_size_naming.py --rules rules/file-size-and-naming-guard.json --all
    python tools/check_size_naming.py --rules <rules.json> --files a.py b.java
    python tools/check_size_naming.py --rules <rules.json>              # 默认取 git 暂存文件
    python tools/check_size_naming.py --rules <rules.json> --update-baseline   # 棘轮：登记存量违规
    python tools/check_size_naming.py --selftest

退出码：
    0 = 通过（基线里的存量债务不算违规）
    1 = 有新增违规
    2 = 用法错误 / 规则文件缺失或格式错误

基线（棘轮）：与 check_import_direction.py 同一约定 ——
    自动发现 <rules 所在目录>/../baselines/<rules 名>.baseline.json，或用 --baseline 显式指定。
    命中基线的违规（键 = 文件:类型）只提示不阻断，新增违规照常阻断。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_store

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

    lines_cfg = data.get("file_lines")
    if not isinstance(lines_cfg, dict) or "default" not in lines_cfg:
        raise RuleError("规则文件缺少 file_lines.default")
    overrides = lines_cfg.get("overrides", {})
    if not isinstance(overrides, dict):
        raise RuleError("file_lines.overrides 必须是映射（扩展名 → 上限）")

    naming = data.get("naming", {})
    if not isinstance(naming, dict):
        raise RuleError("naming 必须是映射")

    data.setdefault("skip_paths", [])
    data["file_lines"]["overrides"] = overrides
    data["naming"] = naming
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


def limit_for(rel: str, rules: dict) -> "int | None":
    """按扩展名取行数上限。返回 None 表示这个类型不检查。"""
    overrides = rules["file_lines"]["overrides"]
    suffix = Path(rel).suffix.lower()
    for pattern, value in overrides.items():
        if (
            glob_to_regex(pattern).match(Path(rel).name)
            or pattern.lower() == "*" + suffix
        ):
            return value if isinstance(value, int) else None
    return rules["file_lines"]["default"]


def naming_rule_for(rel: str, rules: dict) -> "str | None":
    """按扩展名取命名正则。dunder 模块名（__init__、__main__）是 Python
    解释器强制的名字，不适用任何命名规范 —— 这不是豁免，是规范的作用域。"""
    stem = Path(rel).stem
    if stem.startswith("__") and stem.endswith("__"):
        return None
    suffix = Path(rel).suffix.lower()
    for key, value in rules["naming"].items():
        if key.lower() == "*" + suffix:
            return value
    return None


def is_skipped(rel: str, skip_patterns: list) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


# 基线（棘轮）的存取逻辑在 tools/baseline_store.py，两个检查工具共用。
# 本工具只定义自己的键：文件 + 违规类型。文件改名/新增违规文件都会产生新键 → 阻断。


def violation_key(issue: dict) -> str:
    return f"{issue['file']}:{issue['kind']}"


# ────────────────────────── 文件收集 ──────────────────────────


def git_staged_files(root: Path) -> "list[str]":
    try:
        proc = subprocess.run(
            # core.quotePath=false：默认会把中文路径转义成 "\345\260..." 这类字面串，
            # 路径解析不到文件 → 检查静默通过（2026-10-10 修）
            [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=ACMR",
            ],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    return [
        ln.strip()
        for ln in proc.stdout.decode("utf-8", errors="replace").splitlines()
        if ln.strip()
    ]


def all_files(root: Path, rules: dict) -> "list[str]":
    # 扩展名来源必须同时覆盖 naming 和 file_lines.overrides 两个键空间：
    # 只看 naming 会漏掉「只设了行数上限、没设命名规则」的类型（如 .js），
    # 造成 --all 模式下行数检查静默失效 —— 踩过的真 bug（2026-09-24 修复）。
    exts = set()
    for source in (rules.get("naming", {}), rules["file_lines"].get("overrides", {})):
        for key in source:
            if key.startswith("*"):
                exts.add(key[1:].lower())
    if not exts:
        exts = {".py", ".java", ".vue", ".js", ".ts"}
    out = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in f"/{rel}":
            continue
        if f.suffix.lower() in exts:
            out.append(rel)
    return sorted(out)


# ────────────────────────── 检查 ──────────────────────────

# 二进制资产没有「行」这个概念。行数上限是「这个文件承担了两件事」的信号，
# 而 zip 容器 / 字体 / 图片里既没有函数也没有职责可拆。
# 已知类型先按扩展名短路（不读盘），未知类型再用 NUL 字节嗅探兜底。
_BINARY_SUFFIXES = frozenset(
    {
        ".pptx",
        ".ppt",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pdf",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".gz",
        ".tgz",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".bmp",
        ".psd",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".jar",
        ".war",
        ".class",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".pyc",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".wav",
        ".flac",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
)

# git 的判据：前 8000 字节出现 NUL 就是二进制。取 8 KiB 是因为
# 容器格式的头部结构一定落在这一段里。
BINARY_SNIFF_BYTES = 8192


def is_binary(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return True
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(BINARY_SNIFF_BYTES)
    except OSError:
        # 读不动就不拦 —— 交给后续的真实报错，别在这里伪装成「二进制」放行
        return False


def count_lines(path: Path) -> int:
    data = path.read_bytes()
    if not data:
        return 0
    n = data.count(b"\n")
    if not data.endswith(b"\n"):
        n += 1
    return n


def check_one(rel: str, root: Path, rules: dict) -> "list[dict]":
    path = root / rel
    if not path.is_file():
        return []
    issues = []

    limit = limit_for(rel, rules)
    # 二进制文件跳过行数检查：对压缩字节数 0x0A 得到的是随机数，不是行数。
    # 曾把一个 804 KB 的 .pptx 判成「1531 行，超限 1031 行」，还建议作者去拆函数。
    if limit is not None and not is_binary(path):
        n = count_lines(path)
        if n > limit:
            issues.append(
                {
                    "kind": "file-lines",
                    "file": rel,
                    "actual": n,
                    "limit": limit,
                    "excess": n - limit,
                }
            )

    pattern = naming_rule_for(rel, rules)
    if pattern:
        stem = Path(rel).stem
        if not re.fullmatch(pattern, stem):
            issues.append(
                {
                    "kind": "naming",
                    "file": rel,
                    "name": stem,
                    "pattern": pattern,
                }
            )

    return issues


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """回归用例在 check_size_naming_selftest.py —— 内联会让本文件超过 500 行上限。"""
    from check_size_naming_selftest import run

    return run()


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="文件体积与命名规范检查")
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
        print(f"[x] 规则问题：{exc}", file=sys.stderr)
        return 2

    try:
        if args.files:
            targets = list(args.files)
        elif args.all:
            targets = all_files(root, rules)
        else:
            targets = git_staged_files(root)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    targets = [t for t in targets if not is_skipped(t, rules["skip_paths"])]

    issues: list = []
    for rel in targets:
        issues.extend(check_one(rel, root, rules))

    # ── 基线（棘轮）：存量债务放行，新增阻断 ──
    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline
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
                set(targets),
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [i for i in issues if violation_key(i) in baseline]
    fresh = [i for i in issues if violation_key(i) not in baseline]

    if fresh:
        over = [i for i in fresh if i["kind"] == "file-lines"]
        named = [i for i in fresh if i["kind"] == "naming"]
        print(f"[x] 发现 {len(fresh)} 处新增违规：\n")
        for i in over:
            print(f"  {i['file']}")
            print(
                f"    文件 {i['actual']} 行，超过上限 {i['limit']} 行（超出 {i['excess']} 行）"
            )
            print()
        for i in named:
            print(f"  {i['file']}")
            print(f"    文件名 {i['name']!r} 不符合规范：{i['pattern']}")
            print()
        if over:
            print("超长文件不是靠拆函数解决，而是先问：这个文件是不是承担了两件事？")
            print("拆分的依据是职责，不是行数。")
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
        print(f"[v] 体积与命名检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
