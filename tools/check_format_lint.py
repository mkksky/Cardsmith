#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_format_lint.py —— 格式化与 lint 工具链接入

本脚本**不自己实现任何 lint 规则**，只负责把项目里已有的格式化/lint 工具
（ruff / eslint / prettier / black …）接进套件，统一到本套件的退出码约定上。

    工具不存在 → 输出 SKIP，退出码 0（不阻断）
    工具存在、有违规 → 退出码 1
    工具存在但执行失败（超时 / OSError 等）→ 退出码 1（不再被伪装成「未安装」）
    规则文件坏了 → 退出码 2

为什么工具不存在时放行而不是报错：
    格式化工具属于「有则更好」的一层。因为没装 ruff 就把提交卡死，
    后果是使用者养成 --no-verify 习惯 —— 那样连真正重要的分层检查也一起失效了。

被卡片引用：
    cards/format-lint-guard.md

用法：
    python tools/check_format_lint.py --rules rules/format-lint-guard.json --all
    python tools/check_format_lint.py --rules <rules.json> --files a.py
    python tools/check_format_lint.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_GIT = 30
TIMEOUT_LINT = 120
# 单次调用的命令行长度预算（字符）。Windows CreateProcess 上限约 32767，
# 留足余量避免 [WinError 206] 文件名或扩展名太长。
ARG_BUDGET = 16000


class RuleError(Exception):
    pass


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        raise RuleError("规则文件需要非空 tools 列表")
    for i, t in enumerate(tools):
        for field in ("name", "extensions", "command"):
            if field not in t:
                raise RuleError(f"tools[{i}] 缺少字段 {field!r}")
        if not isinstance(t["command"], list) or not t["command"]:
            raise RuleError(f"tools[{i}].command 必须是非空列表")
        if not isinstance(t["extensions"], list) or not t["extensions"]:
            raise RuleError(f"tools[{i}].extensions 必须是非空列表")
    data.setdefault("skip_paths", [])
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


def chunk_paths(
    paths: "list[str]", prefix_len: int, budget: int = ARG_BUDGET
) -> "list[list[str]]":
    """把文件列表切成若干批，使每批的命令行长度不超过预算。

    一次把成百上千个路径塞进单个 argv，在 Windows 上会超过 CreateProcess 的
    命令行长度上限（约 32767），直接抛 [WinError 206]——工具根本没跑起来。
    分批后逐批调用，任何一批出问题都照样能判出来。
    """
    chunks: "list[list[str]]" = []
    cur: "list[str]" = []
    size = prefix_len
    for p in paths:
        plen = len(p) + 1
        if cur and size + plen > budget:
            chunks.append(cur)
            cur, size = [], prefix_len
        cur.append(p)
        size += plen
    if cur:
        chunks.append(cur)
    return chunks


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
    exts = {e.lower() for t in rules["tools"] for e in t["extensions"]}
    out = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if rel.startswith(".git/") or f"/{rel}".startswith("/.git/"):
            continue
        if f.suffix.lower() in exts:
            out.append(rel)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="格式化与 lint 工具链接入")
    ap.add_argument("--rules")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        # 只验规则解析与文件归类，不依赖本机是否装了 lint 工具
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "ruff",
                                "extensions": [".py"],
                                "command": ["ruff", "check"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            try:
                rules = load_rules(p)
            except RuleError as exc:
                print(f"  [x] 合法规则被拒：{exc}")
                return 1
            print("  [v] 合法规则解析通过")

            bad = Path(td) / "bad.json"
            for content, label in (
                ('{"tools": []}', "空 tools"),
                ('{"tools": [{"name": "x"}]}', "缺字段"),
                (
                    '{"tools": [{"name":"x","extensions":[".py"],"command":[]}]}',
                    "空 command",
                ),
            ):
                bad.write_text(content, encoding="utf-8")
                try:
                    load_rules(bad)
                except RuleError:
                    print(f"  [v] {label} → 已按预期报错")
                else:
                    print(f"  [x] {label} → 本该报错却通过了")
                    return 1

            # 分批逻辑：文件一个不丢，且每批的命令行估算长度不超预算
            n = 5000
            names = [
                f"okxtradesys.gitee/aaa/bbb/ccc/module_{i:05d}.py" for i in range(n)
            ]
            prefix_len = len("ruff check --no-fix") + 1
            chunks = chunk_paths(names, prefix_len)
            if sum(len(c) for c in chunks) != n:
                print("  [x] 分批丢文件")
                return 1
            if any(
                prefix_len + sum(len(p) + 1 for p in c) > ARG_BUDGET for c in chunks
            ):
                print("  [x] 分批超出命令行预算")
                return 1
            print(f"  [v] 分批：{n} 个文件 → {len(chunks)} 批，均未超预算")

        print()
        print("自检全绿")
        return 0

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

    targets = [
        t
        for t in targets
        if not any(glob_to_regex(p).match(t) for p in rules["skip_paths"])
    ]

    if not targets:
        if not args.quiet:
            print("[v] 没有需要检查的文件")
        return 0

    violations: list = []
    missing: list = []
    exec_failed: list = []

    for tool in rules["tools"]:
        exts = {e.lower() for e in tool["extensions"]}
        subset = [t for t in targets if Path(t).suffix.lower() in exts]
        if not subset:
            continue

        exe = tool["command"][0]
        if shutil.which(exe) is None:
            missing.append(tool["name"])
            continue

        # 分批调用：文件多时单条命令行会超长，分批既避开 WinError 206，
        # 也不会因为「跑了但没报错」而误判成通过。
        prefix_len = len(" ".join(tool["command"])) + 1
        for chunk in chunk_paths(subset, prefix_len):
            cmd = [*tool["command"], *chunk]
            try:
                proc = subprocess.run(
                    cmd, cwd=str(root), capture_output=True, timeout=TIMEOUT_LINT
                )
            except subprocess.TimeoutExpired:
                exec_failed.append((tool["name"], f"超时（>{TIMEOUT_LINT}s）"))
                break
            except OSError as exc:
                # 注意：这是「执行失败」，不是「未安装」。以前把它并进 skipped，
                # 结果 ruff 明明装着却被打成「未安装，已跳过」→ 门禁静默通过。
                exec_failed.append((tool["name"], f"无法执行：{exc}"))
                break

            if proc.returncode != 0:
                out = proc.stdout.decode("utf-8", errors="replace").strip()
                err = proc.stderr.decode("utf-8", errors="replace").strip()
                violations.append(
                    (tool["name"], out or err or f"退出码 {proc.returncode}")
                )
                break

    if missing and not args.quiet:
        print(f"[~] 本机未安装，已跳过：{', '.join(missing)}")
        print("    需要检查请先安装，或让它保持跳过 —— 装好之后自动生效。")

    problems = violations + exec_failed
    if problems:
        print(f"\n[x] 有 {len(problems)} 个工具未能正常完成检查：\n")
        for name, output in problems:
            print(f"  ── {name} ──")
            for line in output.splitlines()[:40]:
                print(f"  {line}")
            print()
        return 1

    if not args.quiet:
        print(f"[v] 格式化与 lint 检查通过（{len(targets)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
