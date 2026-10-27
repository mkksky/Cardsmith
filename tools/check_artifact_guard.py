#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_artifact_guard.py —— 构建产物入库与前端产物同步检查

为什么单独成工具：体积、密钥、分层这些规则只看单个源文件，管不了「哪些文件
根本不该进仓库」和「仓库里的构建产物落后源码多久」——这两件事都需要 git 视角。

分两档，刻意不同：
  blocking  → 本次**新增**的产物进暂存区（.venv/ dist/ target/ *.log …）
  advisory  → 已经被跟踪的存量产物、static/ 落后 src/ 超过 N 天

只拦新增的理由：存量清理前若直接阻断，每次提交都会红，最后一定被 --no-verify
绕过；把存量放提示档，新账才拦得住。

退出码：
    0 = 通过（含提示档命中）
    1 = 有新增产物入库
    2 = 规则 / git 环境错误  ← 绝不能当通过处理

被谁使用：
    hooks/pre-commit（暂存区新增文件）
    tools/release_checklist.py --all（存量 + 构建同步）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

EXIT_PASS, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
TIMEOUT_GIT = 30


class RuleError(Exception):
    """规则文件或 git 环境的问题。对应退出码 2。"""


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    for field in ("blocked_paths", "advisory_paths"):
        value = data.get(field)
        if not isinstance(value, list):
            raise RuleError(f"规则文件缺少 {field}（需要字符串数组）")
    sync = data.get("build_sync")
    if not isinstance(sync, dict) or not sync.get("source") or not sync.get("artifact"):
        raise RuleError("build_sync 需要 source 与 artifact")
    if not isinstance(sync.get("max_lag_days", 7), int):
        raise RuleError("build_sync.max_lag_days 必须是整数")
    return data


def _git(root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"执行 git 失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    return proc.stdout.decode("utf-8", "replace")


def git_staged_added(root: Path) -> list[str]:
    out = _git(root, ["diff", "--cached", "--name-only", "--diff-filter=A"])
    return [x.strip() for x in out.splitlines() if x.strip()]


def git_tracked(root: Path) -> list[str]:
    out = _git(root, ["ls-files"])
    return [x.strip() for x in out.splitlines() if x.strip()]


def last_commit_ts(root: Path, path: str) -> int | None:
    out = _git(root, ["log", "-1", "--format=%ct", "--", path]).strip()
    return int(out) if out.isdigit() else None


def classify(paths: list[str], patterns: list[str]) -> list[str]:
    return [p for p in paths if any(fnmatch(p, pat) for pat in patterns)]


def build_sync_warning(root: Path, sync: dict) -> str | None:
    source_ts = last_commit_ts(root, sync["source"])
    artifact_ts = last_commit_ts(root, sync["artifact"])
    if source_ts is None or artifact_ts is None:
        return None
    lag_days = (source_ts - artifact_ts) / 86400
    limit = int(sync.get("max_lag_days", 7))
    if lag_days <= limit:
        return None
    return (
        f"[!] {sync['artifact']} 落后 {sync['source']} 约 {lag_days:.0f} 天"
        f"（阈值 {limit} 天）：{sync.get('why', '')}"
    )


def _selftest() -> int:
    failures = 0

    def case(name: str, paths: list[str], patterns: list[str], expected: int) -> None:
        nonlocal failures
        got = classify(paths, patterns)
        if len(got) == expected:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}：期望 {expected} 个，实际 {got}")
            failures += 1

    blocked = [".venv/**", "dist/**", "target/**", "*.log", "tmp/**"]
    case(
        "虚拟环境与构建产物命中",
        [".venv/Scripts/python.exe", "dist/index.html"],
        blocked,
        2,
    )
    case("源码不误伤", ["src/main.js", "backend/src/main/java/A.java"], blocked, 0)
    case("日志文件命中（任意目录）", ["logs/backend.log", "backend.log"], blocked, 2)
    case(
        "static 只进提示档",
        ["backend/src/main/resources/static/index.html"],
        ["backend/src/main/resources/static/**"],
        1,
    )

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return EXIT_FAIL
    print("自检全绿：4/4")
    return EXIT_PASS


def main() -> int:
    ap = argparse.ArgumentParser(description="构建产物入库与产物同步检查")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="检查全部已跟踪文件与构建同步")
    ap.add_argument("--files", nargs="*", help="只检查指定文件（视为本次新增）")
    ap.add_argument("--quiet", action="store_true", help="通过时不输出")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return EXIT_USAGE

    try:
        rules = load_rules(Path(args.rules))
    except RuleError as exc:
        print(f"[x] 规则问题：{exc}", file=sys.stderr)
        return EXIT_USAGE

    root = Path(args.root).resolve()
    blocked_patterns = rules["blocked_paths"]
    advisory_patterns = rules.get("advisory_paths") or []

    try:
        if args.files:
            candidates = [f.replace("\\", "/") for f in args.files]
            new_bad = classify(candidates, blocked_patterns)
            tracked_bad, tracked_advisory = [], classify(candidates, advisory_patterns)
        elif args.all:
            tracked = git_tracked(root)
            # --all 不拦存量：存量产物是历史债，拦了只会导致每次都红
            new_bad = []
            tracked_bad = classify(tracked, blocked_patterns)
            tracked_advisory = classify(tracked, advisory_patterns)
        else:
            staged = git_staged_added(root)
            new_bad = classify(staged, blocked_patterns)
            tracked_bad, tracked_advisory = [], classify(staged, advisory_patterns)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return EXIT_USAGE

    if new_bad:
        print(f"[x] 本次新增了 {len(new_bad)} 个不应入库的构建产物：\n")
        for rel in new_bad:
            print(f"  {rel}")
        print()
        print("处理方式：git rm --cached <文件>，并把对应目录补进 .gitignore。")
        print("构建产物应由 CI / 本地构建生成，不进版本库。")
        return EXIT_FAIL

    # 按类汇总，不逐文件刷屏：static/ 下通常有几十个带 hash 的产物，
    # 每个都打一行会把 release-checklist 的 WARN 区淹没。
    if tracked_advisory:
        print(
            f"[!] 已有 {len(tracked_advisory)} 个前端构建产物被 git 跟踪"
            f"（示例：{tracked_advisory[0]}）；应由构建生成，建议 git rm --cached"
        )
    if tracked_bad:
        print(
            f"[!] 已有 {len(tracked_bad)} 个存量产物被 git 跟踪（不阻断本次提交，清理后自动消失）："
        )
        for rel in tracked_bad[:10]:
            print(f"    {rel}")
        if len(tracked_bad) > 10:
            print(f"    … 另有 {len(tracked_bad) - 10} 个")

    if args.all:
        try:
            warning = build_sync_warning(root, rules["build_sync"])
        except RuleError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return EXIT_USAGE
        if warning:
            print(warning)

    if not args.quiet and not (tracked_bad or tracked_advisory or args.all):
        print("[v] 仓库卫生检查通过（无新增产物）")

    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
