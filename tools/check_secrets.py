#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_secrets.py —— 密钥防泄守卫

密钥进了 git 历史就是永久的：事后删除、改密码都救不回来，任何人拿到仓库
都能从历史提交里翻出来。本守卫挂在 pre-commit，扫描**暂存文件**，分两级：

    hard（高危，阻断）  私钥块、云厂商 token 等已知格式、.env/密钥文件本身被暂存
    soft（可疑，提醒）  敏感变量名 + 密钥形态字符串的赋值（不拦截，不逼 --no-verify）

设计取舍与 doc-sync 一致：只查机器可裁断的事实（格式/形态），不猜「这个值
是不是真的密钥」——那是判断题，误报会逼出 --no-verify 习惯。致命的拦住，
可疑的让人自己看一眼，日常提交零打扰。

私隐：报告只打印**打码预览**（前 3 位 + ***），基线键存哈希不存原文，
永远不会把密钥本身写进任何输出或文件。

被卡片引用：
    cards/secrets-guard.md

用法：
    python tools/check_secrets.py --rules rules/secrets-guard.json
    python tools/check_secrets.py --rules <rules.json> --all        # 摸底
    python tools/check_secrets.py --rules <rules.json> --files <f>  # 指定文件
    python tools/check_secrets.py --rules <rules.json> --selftest

退出码：0 = 通过或仅有 soft 提醒  1 = 有 hard 新增违规  2 = 规则或配置错误
基线：只登记 hard。键 = <文件>:<规则id>:<sha1(密文)前8位>，不含行号与原文。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store  # noqa: E402
from check_doc_sync import TIMEOUT_GIT, git_staged_files, glob_to_regex, is_skipped  # noqa: E402

# 超过这个大小视为非文本，跳过（二进制/大文件里做正则是自找麻烦）
MAX_FILE_BYTES = 1_000_000

DEFAULT_PLACEHOLDERS = (
    "your_", "xxx", "test", "example", "placeholder", "changeme",
    "todo", "dummy", "sample", "abcd", "1234", "0000", "****", "<",
)


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
    for key in ("patterns", "skip_paths", "sensitive_files"):
        if not isinstance(rules.get(key), list):
            raise RuleError(f"规则缺少 {key} 列表：{path}")
    for i, p in enumerate(rules["patterns"]):
        for field in ("id", "regex", "severity"):
            if not p.get(field):
                raise RuleError(f"patterns[{i}] 缺少 {field}")
        if p["severity"] not in ("hard", "soft"):
            raise RuleError(f"patterns[{i}].severity 非法：{p['severity']}")
        try:
            re.compile(p["regex"])
        except re.error as exc:
            raise RuleError(f"patterns[{i}].regex 编译失败：{exc}")
    rules.setdefault("soft_var_regex", "")
    rules.setdefault("placeholder_words", list(DEFAULT_PLACEHOLDERS))
    rules.setdefault("allow_values", [])
    rules.setdefault("allow_files", [])
    rules.setdefault("min_length", 10)
    return rules


# ────────────────────────── 判定 ──────────────────────────


def _looks_secret(value: str, rules: dict) -> bool:
    """soft 层的第二道过滤：变量名已经像了，值本身也要像密钥。

    占位符（your_key_here / xxx / 1234...）直接放行；剩下的要求
    「字母+数字都有」或「足够长」—— 排除 'password'、'secret' 这类
    裸单词当值的写法。
    """
    low = value.lower()
    if any(w in low for w in rules["placeholder_words"]):
        return False
    has_alpha = any(ch.isalpha() for ch in value)
    has_digit = any(ch.isdigit() for ch in value)
    return (has_alpha and has_digit) or len(value) >= int(rules["min_length"]) * 2


def _mask(value: str) -> str:
    return (value[:3] + "***") if len(value) >= 6 else "***"


def scan_text(text: str, rules: dict) -> "list[dict]":
    """对单份文本跑全部规则。返回 issue 列表（不含文件名，调用方补）。"""
    issues: list = []
    allow = [v.lower() for v in rules["allow_values"]]
    for i, line in enumerate(text.splitlines(), 1):
        for p in rules["patterns"]:
            for m in re.finditer(p["regex"], line):
                value = m.group(0)
                if any(v in value.lower() for v in allow):
                    continue
                if p["severity"] == "soft" and not _looks_secret(value, rules):
                    continue
                issues.append(
                    {
                        "pid": p["id"],
                        "severity": p["severity"],
                        "line": i,
                        "value": value,
                        "preview": _mask(value),
                        "why": p.get("why", ""),
                    }
                )
        sv = rules.get("soft_var_regex")
        if sv:
            for m in re.finditer(sv, line):
                name, value = m.group(1), m.group(2)
                if any(v in value.lower() for v in allow):
                    continue
                if not _looks_secret(value, rules):
                    continue
                issues.append(
                    {
                        "pid": f"var:{name.lower()}",
                        "severity": "soft",
                        "line": i,
                        "value": value,
                        "preview": _mask(value),
                        "why": "敏感变量名赋了一个密钥形态的值",
                    }
                )
    return issues


def check_files(files: "list[str]", root: Path, rules: dict) -> "list[dict]":
    issues: list = []
    for rel in files:
        rel = rel.replace("\\", "/")
        if is_skipped(rel, rules["skip_paths"]):
            continue
        if any(glob_to_regex(p).match(rel) for p in rules["allow_files"]):
            continue
        path = root / rel
        if not path.is_file():
            continue
        # 文件名本身命中敏感清单 → high（.env / *.pem / id_rsa 之类被暂存就是事故）
        base = rel.rsplit("/", 1)[-1]
        if any(glob_to_regex(p).match(base) for p in rules["sensitive_files"]):
            issues.append(
                {
                    "file": rel,
                    "pid": "sensitive-file",
                    "severity": "hard",
                    "line": 0,
                    "value": rel,
                    "preview": rel,
                    "why": "敏感文件被暂存（密钥/凭据不该进 git，改走环境变量或 .gitignore）",
                }
            )
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw[:1024]:
            continue  # 二进制/超大文件不扫
        for issue in scan_text(raw.decode("utf-8", errors="replace"), rules):
            issue["file"] = rel
            issues.append(issue)
    return issues


def violation_key(issue: dict) -> str:
    digest = hashlib.sha1(issue["value"].encode("utf-8")).hexdigest()[:8]
    return f"{issue['file']}:{issue['pid']}:{digest}"


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="密钥防泄检查（高危阻断，可疑提醒）")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true", help="扫全仓库（摸底用）")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--baseline", help="基线文件路径（默认自动发现）")
    ap.add_argument("--update-baseline", action="store_true", help="登记存量 hard 违规")
    args = ap.parse_args()

    if args.selftest:
        import check_secrets_selftest

        return check_secrets_selftest.run_selftest()

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
        if args.files is not None:
            files = [f.replace("\\", "/") for f in args.files if f]
        elif args.all:
            # 尊重 .gitignore。直接 rglob 会把 .env / .env.local 这类**永远进不了 git**
            # 的本地文件也扫进来，判成「敏感文件被暂存」而 FAIL —— 而这些文件各机器
            # 本地存在与否不同，同一仓库在两台机器上会得出相反的体检结论。
            # git ls-files --cached --others --exclude-standard = 已跟踪 + 未跟踪但未被忽略。
            try:
                proc = subprocess.run(
                    [
                        "git", "-c", "core.quotePath=false", "ls-files",
                        "--cached", "--others", "--exclude-standard",
                    ],
                    cwd=root,
                    capture_output=True,
                    timeout=TIMEOUT_GIT,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuleError(f"列出工作区文件失败：{exc}") from exc
            if proc.returncode != 0:
                raise RuleError(
                    "git ls-files 失败："
                    + proc.stderr.decode("utf-8", errors="replace").strip()
                )
            files = [
                ln
                for ln in proc.stdout.decode("utf-8", errors="replace").splitlines()
                if ln
            ]
        else:
            files = git_staged_files(root)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    issues = check_files(files, root, rules)

    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = sorted({violation_key(i) for i in issues if i["severity"] == "hard"})
        baseline_store.write(baseline_path, keys)
        print(f"[+] 基线已更新：{baseline_path}（登记存量 hard {len(keys)} 条）")
        return 0

    hard = [i for i in issues if i["severity"] == "hard"]
    soft = [i for i in issues if i["severity"] == "soft"]
    fresh_hard = [i for i in hard if violation_key(i) not in baseline]

    for i in soft:
        loc = f"{i['file']}:{i['line']}" if i.get("line") else i["file"]
        print(f"[!] 可疑（不拦截）{loc}  [{i['pid']}]  值：{i['preview']}")
        if i["why"]:
            print(f"    {i['why']}")
    for i in hard:
        if violation_key(i) in baseline:
            if not args.quiet:
                print(f"[!] 存量（基线放行）{i['file']}:{i['line']}  [{i['pid']}]  {i['preview']}")

    if fresh_hard:
        print(f"[x] 发现 {len(fresh_hard)} 处高危密钥：\n")
        for i in fresh_hard:
            loc = f"{i['file']}:{i['line']}" if i["line"] else i["file"]
            print(f"  {loc}  [{i['pid']}]  值：{i['preview']}")
            if i["why"]:
                print(f"    为什么拦：{i['why']}")
            print()
        print("密钥进了 git 历史就是永久的，事后删除也救不回来。")
        print("改走环境变量/.env（并 .gitignore）；确属误报，把值加进规则 allow_values。")
        return 1

    if not args.quiet and not soft:
        print("[v] 密钥检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
