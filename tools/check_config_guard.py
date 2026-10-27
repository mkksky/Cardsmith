#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_config_guard.py —— application*.yml 的机器可判定配置检查

为什么单独成工具：分层 / 体积 / 密钥这些规则都只看代码，管不了「配置组合起来
才危险」的情况 —— 比如 Flyway 与 ddl-auto 同时生效、密码字段带明文默认值。
这些不需要业务语义，正则就能确认，适合进提交门禁。

只做确定性的判断，不猜：
  blocking  → 退出码 1（当前只有 CORS 通配）
  advisory  → 退出码 0 + 输出 [!] 行（套件约定：有输出即非阻断提醒）

退出码与其他工具一致：
    0 = 通过（含提示档命中）
    1 = 有阻断项
    2 = 规则 / 配置错误  ← 绝不能当通过处理

被谁使用：
    hooks/pre-commit（暂存区命中配置文件时）
    tools/release_checklist.py --all
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

EXIT_PASS, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
TIMEOUT_GIT = 30

KNOWN_CHECKS = {
    "cors-wildcard-credentials",
    "plaintext-password-default",
    "jwt-secret-default",
    "ddl-auto-with-flyway",
    "flyway-validate-disabled",
}

# allowed-origins / allowed-patterns，兼容 Spring 的 kebab-case 与 camelCase
CORS_KEY = re.compile(
    r"^\s*(allowed-origins|allowed-origin-patterns|allowedOrigins|allowedOriginPatterns)\s*:\s*(.*)$"
)
CORS_ITEM = re.compile(r"^\s*-\s*[\"']?([^\"'#\s]+)")
PASSWORD_DEFAULT = re.compile(
    r"^\s*(password|login-password)\s*:\s*\$\{[^}:]+:([^}]*)\}"
)
JWT_DEFAULT = re.compile(r"^\s*secret\s*:\s*\$\{JWT_SECRET:([^}]*)\}")
DDL_AUTO = re.compile(r"^\s*ddl-auto\s*:\s*(update|create|create-drop)\b")
FLYWAY_VALIDATE_OFF = re.compile(r"^\s*validate-on-migrate\s*:\s*false\b")
FLYWAY_OFF = re.compile(r"^\s*enabled\s*:\s*false\b")


class RuleError(Exception):
    """规则文件或 git 环境的问题。对应退出码 2。"""


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise RuleError("规则文件缺少 files（需要 glob 列表）")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise RuleError("规则文件缺少 checks（需要非空列表）")
    for i, check in enumerate(checks):
        if check.get("id") not in KNOWN_CHECKS:
            raise RuleError(f"checks[{i}].id 未知：{check.get('id')!r}")
        if check.get("mode", "blocking") not in ("blocking", "advisory"):
            raise RuleError(f"checks[{i}].mode 只能是 blocking / advisory")
    return data


def git_staged_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
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
        x.strip()
        for x in proc.stdout.decode("utf-8", "replace").splitlines()
        if x.strip()
    ]


def matches_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch(rel, p) for p in patterns)


def collect_targets(
    root: Path, patterns: list[str], files: list[str] | None, scan_all: bool
) -> list[str]:
    if files:
        return [f.replace("\\", "/") for f in files]
    if scan_all:
        found = []
        for pattern in patterns:
            for p in root.glob(pattern):
                if p.is_file():
                    found.append(p.relative_to(root).as_posix())
        return sorted(set(found))
    return [f for f in git_staged_files(root) if matches_any(f, patterns)]


def _values_after_key(line: str) -> list[str]:
    """取出 key: 后面的值；YAML 列表形式（- xxx）由调用方看下一行。"""
    raw = line.split(":", 1)[1].strip()
    raw = raw.split("#", 1)[0].strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [x.strip().strip("\"'") for x in raw.split(",") if x.strip()]


def scan_text(rel: str, text: str, checks: list[dict]) -> list[dict]:
    lines = text.splitlines()
    modes = {c["id"]: c.get("mode", "blocking") for c in checks}
    whys = {c["id"]: c.get("why", "") for c in checks}
    found: list[dict] = []

    def add(check_id: str, lineno: int, text_line: str) -> None:
        found.append(
            {
                "file": rel,
                "line": lineno,
                "check": check_id,
                "mode": modes[check_id],
                "why": whys[check_id],
                "text": text_line.strip(),
            }
        )

    flyway_disabled = any(FLYWAY_OFF.search(ln) for ln in lines)
    in_cors_block = False

    for lineno, line in enumerate(lines, 1):
        # CORS 通配：只把裸 * / http://* / https://* 当通配，
        # http://localhost:* 这种限端口的写法放行。
        m = CORS_KEY.match(line)
        if m:
            in_cors_block = True
            candidates = _values_after_key(line)
        elif in_cors_block and CORS_ITEM.match(line):
            candidates = [CORS_ITEM.match(line).group(1)]
        else:
            candidates = []
            if line.strip() and not line.lstrip().startswith("-"):
                in_cors_block = False
        for value in candidates:
            if value == "*" or re.match(r"^https?://\*$", value):
                add("cors-wildcard-credentials", lineno, line)

        m = PASSWORD_DEFAULT.match(line)
        if m and m.group(2).strip():
            add("plaintext-password-default", lineno, line)

        m = JWT_DEFAULT.match(line)
        if m and m.group(1).strip():
            add("jwt-secret-default", lineno, line)

        if DDL_AUTO.match(line) and not flyway_disabled:
            add("ddl-auto-with-flyway", lineno, line)

        if FLYWAY_VALIDATE_OFF.match(line):
            add("flyway-validate-disabled", lineno, line)

    # 同一个 key 在多行里重复命中时去重（例如 CORS 列表每项都算一次）
    seen = set()
    unique = []
    for item in found:
        key = (item["file"], item["line"], item["check"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def report(findings: list[dict]) -> int:
    blocking = [f for f in findings if f["mode"] == "blocking"]
    advisory = [f for f in findings if f["mode"] != "blocking"]

    if blocking:
        print(f"[x] 发现 {len(blocking)} 处阻断级配置问题：\n")
        for f in blocking:
            print(f"  {f['file']}:{f['line']}  [{f['check']}]")
            print(f"    {f['text']}")
            if f["why"]:
                print(f"    {f['why']}")
            print()

    # 提示档：--quiet 也要输出，退出码 0 但有输出 = 套件约定的非阻断提醒
    for f in advisory:
        print(f"[!] {f['file']}:{f['line']}  [{f['check']}] {f['text']}")
        if f["why"]:
            print(f"    {f['why']}")

    if blocking:
        return EXIT_FAIL
    if not advisory:
        print("[v] 配置检查通过（未发现危险组合）")
    return EXIT_PASS


def _selftest() -> int:
    import tempfile

    failures = 0
    checks = [
        {"id": "cors-wildcard-credentials", "mode": "blocking"},
        {"id": "plaintext-password-default", "mode": "advisory"},
        {"id": "jwt-secret-default", "mode": "advisory"},
        {"id": "ddl-auto-with-flyway", "mode": "advisory"},
        {"id": "flyway-validate-disabled", "mode": "advisory"},
    ]

    def case(name: str, text: str, expected: int) -> None:
        nonlocal failures
        got = scan_text("application-test.yml", text, checks)
        if len(got) == expected:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}：期望 {expected} 处，实际 {got}")
            failures += 1

    case("裸 * 来源 → 阻断", "cors:\n  allowed-origins: '*'\n", 1)
    case(
        "localhost 端口通配 → 放行", "cors:\n  allowed-origins: http://localhost:*\n", 0
    )
    case("密码带明文默认值 → 提示", "  password: ${DB_PASSWORD:root123}\n", 1)
    case("密码无默认值 → 放行", "  password: ${DB_PASSWORD}\n", 0)
    case("JWT 默认密钥 → 提示", "  secret: ${JWT_SECRET:dev_secret}\n", 1)
    case("ddl-auto 与 Flyway 并存 → 提示", "  ddl-auto: update\n", 1)
    case("关闭校验 → 提示", "    validate-on-migrate: false\n", 1)
    case(
        "Flyway 显式关闭 → 不再提示 ddl-auto",
        "flyway:\n  enabled: false\n  ddl-auto: update\n",
        0,
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "backend/src/main/resources").mkdir(parents=True)
        (root / "backend/src/main/resources/application.yml").write_text(
            "spring:\n  jpa:\n    hibernate:\n      ddl-auto: update\n",
            encoding="utf-8",
        )
        patterns = ["backend/src/main/resources/application*.yml"]
        got = collect_targets(root, patterns, None, True)
        if got == ["backend/src/main/resources/application.yml"]:
            print("  [v] --all 文件收集")
        else:
            print(f"  [x] --all 文件收集异常：{got}")
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return EXIT_FAIL
    print("自检全绿：9/9")
    return EXIT_PASS


def main() -> int:
    ap = argparse.ArgumentParser(description="application*.yml 配置一致性检查")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="扫描规则里的全部配置文件")
    ap.add_argument("--files", nargs="*", help="只检查指定文件")
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
    try:
        targets = collect_targets(root, rules["files"], args.files, args.all)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return EXIT_USAGE

    findings: list[dict] = []
    for rel in targets:
        path = root / rel
        if not path.is_file():
            print(f"[x] 文件不存在：{rel}", file=sys.stderr)
            return EXIT_USAGE
        findings.extend(
            scan_text(
                rel, path.read_text(encoding="utf-8", errors="replace"), rules["checks"]
            )
        )

    if not targets and not args.quiet:
        print("[v] 本次改动未涉及配置文件，跳过")
        return EXIT_PASS

    if args.quiet and not findings:
        return EXIT_PASS
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())
