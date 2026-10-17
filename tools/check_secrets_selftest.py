# -*- coding: utf-8 -*-
"""tools/check_secrets_selftest.py —— check_secrets 的自检用例

独立成文件的原因：主文件守住 size-naming 的 500 行门禁（doc-sync 同款先例）。
跑法：python tools/check_secrets.py --selftest

注意：本文件里的假密钥是**故意的测试样本**，规则 skip_paths 已把本文件排除，
套件自己的 pre-commit 不会把自己拦下来。
"""

import json
import tempfile
from pathlib import Path

from check_secrets import check_files, load_rules


def run_selftest() -> int:
    failures = 0

    def ok(name: str, passed: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{'v' if passed else 'x'}] {name}")
        if not passed:
            print(f"      {detail}")
            failures += 1

    def base_rules(**over):
        rules = {
            "patterns": [
                {
                    "id": "private-key",
                    "severity": "hard",
                    "regex": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                    "why": "私钥",
                },
                {
                    "id": "github-token",
                    "severity": "hard",
                    "regex": r"\bghp_[A-Za-z0-9]{36}\b",
                    "why": "GitHub PAT",
                },
            ],
            "soft_var_regex": (
                r"(?i)\b(api[_-]?key|secret|passphrase|password)\b"
                r"\s*[=:]\s*[\"']([^\"']{10,})[\"']"
            ),
            "sensitive_files": [".env", "*.pem"],
            "skip_paths": ["**/skipme/**"],
            "allow_values": ["ALLOWED_VALUE_123456"],
            "allow_files": [],
            "min_length": 10,
            "placeholder_words": ["your_", "xxx", "1234"],
        }
        rules.update(over)
        return rules

    def w(root: Path, rel: str, text: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def run(rules, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w(root, "src/app.py", files)
            return check_files(["src/app.py"], root, rules)

    def sevs(issues):
        return sorted({i["severity"] for i in issues}) or ["none"]

    def run_expect(name, expect_sev, rules, files):
        issues = run(rules, files)
        s = sevs(issues)
        ok(name, expect_sev in s, f"期望 {expect_sev}，实际 {s}：{[(i['pid'], i['preview']) for i in issues]}")

    run_expect("私钥块 → hard", "hard", base_rules(), "key = '''-----BEGIN RSA PRIVATE KEY-----\\nabc'''\n")
    run_expect("ghp_ token → hard", "hard", base_rules(), 't = "ghp_' + "A1b2C3d4" * 4 + "A1b2\"\n")
    run_expect("真实感 api_key 赋值 → soft（不阻断）", "soft", base_rules(), 'api_key = "Ak33jZx91mQpLr7T"\n')
    run_expect("passphrase 赋值 → soft", "soft", base_rules(), 'passphrase = "Pq7wErTy99x"\n')
    run_expect("占位符值 → 无违规", "none", base_rules(), 'api_key = "your_api_key_here"\npassword = "1234"\n')
    run_expect("裸单词当值 → 无违规", "none", base_rules(), 'password = "correct-horse"\n')
    run_expect("allow_values 放行", "none", base_rules(), 'api_key = "Ak33jZx91mQpLr7T-ALLOWED_VALUE_123456"\n')
    run_expect(
        "skip_paths 命中不查",
        "none",
        base_rules(skip_paths=["**/skipme/**", "**/src/**"]),
        'api_key = "Ak33jZx91mQpLr7T"\n',
    )

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        w(root, ".env", "API_KEY=Ak33jZx91mQpLr7T\n")
        issues = check_files([".env"], root, base_rules())
        ok(".env 暂存 → hard（文件级）", "hard" in sevs(issues), f"{issues}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        w(root, "cert.pem", "-----BEGIN CERTIFICATE-----\n")
        issues = check_files(["cert.pem"], root, base_rules())
        ok(".pem 暂存 → hard（即使内容是证书）", "hard" in sevs(issues), f"{issues}")

    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "r.json"
        bad.write_text(
            json.dumps({"patterns": [{"id": "x", "regex": "([", "severity": "hard"}]}),
            encoding="utf-8",
        )
        try:
            load_rules(bad)
            ok("坏正则 → 规则错误可被识别", False, "load_rules 竟然没抛异常")
        except Exception:
            ok("坏正则 → 规则错误可被识别", True)

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：11/11")
    return 0
