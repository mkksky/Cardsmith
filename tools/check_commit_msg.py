#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_commit_msg.py —— commit message 规范检查

git commit-msg 阶段的执行体。message 是仓库的检索入口 ——
`git log --grep`、release notes、回滚定位全靠它。格式烂的 message
等于没有 message：半年后没人知道那次提交改了什么、为什么改。

检查项（全部在 rules JSON 里可调）：
    1. 类型前缀必须是约定之一（feat / fix / chore / ...）
    2. 可选 scope：feat(auth):
    3. 主题行长度上限 —— 超长的主题在 git log --oneline / GitHub 里被截断
    4. Merge / Revert / Initial 等系统生成的提交自动跳过

被卡片引用：
    cards/commit-message-guard.md

用法（钩子自动调用）：
    python tools/check_commit_msg.py --rules rules/commit-message-guard.json --msg-file .git/COMMIT_EDITMSG
手工验证：
    python tools/check_commit_msg.py --rules <rules.json> --msg-file <文件>
    git log -1 --pretty=%B | python tools/check_commit_msg.py --rules <rules.json> --stdin
    python tools/check_commit_msg.py --rules <rules.json> --selftest

退出码：0=通过 1=违规 2=配置错误
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class RuleError(Exception):
    pass


# ────────────────────────── 规则 ──────────────────────────

_SUBJECT_RE_TMPL = r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\n]+)\))?(?P<bang>!)?: .+"


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    types = data.get("types")
    if not isinstance(types, list) or not types:
        raise RuleError("规则文件缺少 types 列表")
    for t in types:
        if not re.fullmatch(r"[a-z]+", str(t)):
            raise RuleError(f"types 里的类型必须是小写字母：{t!r}")

    max_len = data.get("max_subject_len", 72)
    if not isinstance(max_len, int) or max_len < 10:
        raise RuleError(f"max_subject_len 必须是 >= 10 的整数：{max_len!r}")

    data.setdefault("skip_patterns", [r"^Merge ", r"^Revert ", r"^Initial commit"])
    for p in data["skip_patterns"]:
        try:
            re.compile(p)
        except re.error as exc:
            raise RuleError(f"skip_patterns 正则编译失败（{p!r}）：{exc}") from exc

    data["max_subject_len"] = max_len
    return data


# ────────────────────────── 检查 ──────────────────────────


def check_message(text: str, rules: dict) -> "list[str]":
    """返回违规列表（人类可读的中文句子）。空列表 = 通过。"""
    lines = text.split("\n")
    subject = lines[0].rstrip() if lines else ""

    for p in rules["skip_patterns"]:
        if re.match(p, subject):
            return []

    issues: "list[str]" = []
    types = "|".join(rules["types"])
    subject_re = re.compile(_SUBJECT_RE_TMPL)
    m = subject_re.match(subject)

    if not subject.strip():
        issues.append("主题行为空 —— git log 里第一条就是空白，等于没写")
        return issues

    if not m:
        issues.append(
            f"主题行不符合约定格式：<类型>(<范围>): <描述>，类型可选：{', '.join(rules['types'])}\n"
            f"    实际：{subject!r}\n"
            f"    例：fix(migration): 修复回滚时外键未清理"
        )
        return issues

    if m.group("type") not in rules["types"]:
        issues.append(
            f"类型 {m.group('type')!r} 不在约定列表里：{', '.join(rules['types'])}"
        )

    body_len = len(subject)
    max_len = rules["max_subject_len"]
    if body_len > max_len:
        issues.append(
            f"主题行 {body_len} 字符，超过上限 {max_len} —— 超长部分在 log --oneline 里会被截断，"
            f"细节挪到正文写"
        )

    return issues


# ────────────────────────── 自检 ──────────────────────────

_SELFTEST_RULES = {
    "types": ["feat", "fix", "chore", "docs"],
    "max_subject_len": 30,
    "skip_patterns": [r"^Merge ", r"^Revert "],
}


def _selftest() -> int:
    failures = 0

    def case(name: str, text: str, expect_ok: bool) -> None:
        nonlocal failures
        issues = check_message(text, _SELFTEST_RULES)
        ok = not issues
        if ok == expect_ok:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}\n      期望{'通过' if expect_ok else '违规'}，实际：{issues}")
            failures += 1

    case("标准格式 → 通过", "fix(auth): 修复 token 过期判断\n\n正文说明\n", True)
    case("无 scope → 通过", "chore: 升级依赖\n", True)
    case("缺类型前缀 → 违规", "更新了一些文件\n", False)
    case("类型不在列表 → 违规", "wip: 半成品\n", False)
    case("冒号后没空格 → 违规", "fix:没空格\n", False)
    case("主题超长 → 违规", "feat: " + "x" * 40 + "\n", False)
    case("主题行长度正好压线 → 通过", "feat: " + "x" * 24 + "\n", True)
    case("Merge 提交跳过", "Merge branch 'dev'\n", True)
    case("Revert 提交跳过", "Revert \"fix(auth): x\"\n", True)
    case("空主题 → 违规", "", False)
    case("scope 里带括号 → 违规", "feat(a(b)): x\n", False)

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：11/11")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="commit message 规范检查")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--msg-file", help="message 文件路径（git commit-msg 传 $1）")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 message（手工验证用）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return 2

    try:
        rules = load_rules(Path(args.rules))
    except RuleError as exc:
        print(f"[x] 规则问题：{exc}", file=sys.stderr)
        return 2

    if args.stdin:
        text = sys.stdin.read()
    elif args.msg_file:
        p = Path(args.msg_file)
        if not p.is_file():
            # commit-msg 钩子传入的文件必然存在；不存在说明调用方式不对，按配置错误报
            print(f"[x] message 文件不存在：{p}", file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8", errors="replace")
    else:
        print("[x] 需要 --msg-file 或 --stdin", file=sys.stderr)
        return 2

    issues = check_message(text, rules)

    if issues:
        print("[x] commit message 不符合规范：\n")
        for msg in issues:
            for line in msg.splitlines():
                print(f"  {line}")
            print()
        print("message 是仓库的检索入口。类型写清做了什么，范围写清在哪，细节放正文。")
        return 1

    if not args.quiet:
        print("[v] commit message 检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
