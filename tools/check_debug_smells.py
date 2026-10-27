#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_debug_smells.py —— 调试坏味检查（根因协议的执行体）

抓的不是 bug，是「让症状闭嘴而不是修问题」的写法：

    1. 裸 except:        连 KeyboardInterrupt / SystemExit 一起吞
    2. except...pass:    异常静默消失，症状不见了但根因还在
    3. 空 catch 块:      JS/Java 里同款的错误黑洞

设计取舍：
    - 只做行级/块级模式匹配，不做数据流分析 —— 假阳性靠规则文件调，
      假阴性靠人工 review 兜底。工具的职责是把「确定有问题」的拦下来
    - 支持 --selftest；退出码 0=通过 1=有违规 2=配置错误
    - 基线（棘轮）与 check_size_naming.py 同一约定，键 = 文件:模式id

被卡片引用：
    cards/root-cause-protocol.md

用法：
    python tools/check_debug_smells.py --rules rules/root-cause-guard.json --all
    python tools/check_debug_smells.py --rules rules/root-cause-guard.json --files a.py b.js
    python tools/check_debug_smells.py --rules rules/root-cause-guard.json              # 默认取 git 暂存文件
    python tools/check_debug_smells.py --rules rules/root-cause-guard.json --update-baseline
    python tools/check_debug_smells.py --rules rules/root-cause-guard.json --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_store
from import_collectors import mask_js_strings_and_comments

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

    patterns = data.get("patterns", [])
    seen: set = set()
    for i, p in enumerate(patterns):
        if not isinstance(p, dict) or not p.get("id") or not p.get("regex"):
            raise RuleError(f"patterns[{i}] 缺少 id 或 regex")
        if p["id"] in seen:
            raise RuleError(f"patterns 里 id 重复：{p['id']}")
        seen.add(p["id"])
        try:
            re.compile(p["regex"], re.MULTILINE)
        except re.error as exc:
            raise RuleError(f"patterns[{i}]（{p['id']}）正则编译失败：{exc}") from exc

    builtins = data.get("builtin", [])
    known_builtin = {"except-pass"}
    for b in builtins:
        if b not in known_builtin:
            raise RuleError(f"未知内置检测器：{b!r}（可用：{', '.join(sorted(known_builtin))}）")

    if not patterns and not builtins:
        raise RuleError("规则文件既没有 patterns 也没有 builtin，等于什么都没检查")

    data.setdefault("extensions", [".py", ".js", ".ts", ".java", ".vue"])
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


def is_skipped(rel: str, skip_patterns: list) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


def violation_key(issue: dict) -> str:
    return f"{issue['file']}:{issue['pattern']}"


# ────────────────────────── 文件收集 ──────────────────────────


def git_staged_files(root: Path) -> "list[str]":
    try:
        proc = subprocess.run(
            # core.quotePath=false：默认会把中文路径转义成 "\345\260..." 这类字面串，
            # 路径解析不到文件 → 检查静默通过（2026-10-10 修）
            ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=root,
            capture_output=True,
            timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    return [ln.strip() for ln in proc.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]


def all_files(root: Path, rules: dict) -> "list[str]":
    exts = {e.lower() for e in rules["extensions"]}
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

# 为什么 except-pass 不用正则：「except 块里是否只有 pass」取决于缩进层级 ——
# pass 之后的下一条语句缩进 <= except 行才算出了块。纯正则做不对（自检抓过），
# 所以这个检测器写死在代码里，规则文件用 "builtin": ["except-pass"] 声明启用。
_EXCEPT_LINE = re.compile(r"^([ \t]*)except\b[^:\n]*:\s*(#.*)?$")
_PASS_LINE = re.compile(r"^[ \t]*pass\b[ \t]*(?:#.*)?$")


def _builtin_except_pass(rel: str, text: str) -> "list[dict]":
    issues = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = _EXCEPT_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        except_indent = len(m.group(1))
        j = i + 1
        first_code = None
        while j < len(lines):
            line = lines[j]
            if not line.strip() or line.strip().startswith("#"):
                j += 1
                continue
            first_code = (j, line)
            break
        if first_code and _PASS_LINE.match(first_code[1]):
            # 块内第一句是 pass —— 再看后面还有没有同块语句，有就不算吞
            k = first_code[0] + 1
            swallowed = True
            while k < len(lines):
                line = lines[k]
                if not line.strip() or line.strip().startswith("#"):
                    k += 1
                    continue
                if len(line) - len(line.lstrip()) > except_indent:
                    swallowed = False  # pass 后同块还有逻辑，不算纯吞
                break
            if swallowed:
                issues.append(
                    {
                        "pattern": "except-pass",
                        "file": rel,
                        "line": i + 1,
                        "why": "异常块里只有 pass，错误静默消失，症状不见了但根因还在",
                        "snippet": lines[i].strip()[:120],
                    }
                )
        i = j if first_code else i + 1
    return issues


def check_one(rel: str, root: Path, rules: dict) -> "list[dict]":
    path = root / rel
    if not path.is_file():
        return []
    ext = path.suffix.lower()
    if ext not in {e.lower() for e in rules["extensions"]}:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")

    issues = []
    if "except-pass" in rules.get("builtin", []):
        issues.extend(_builtin_except_pass(rel, text))

    # 正则扫描跑在「屏蔽了字符串与注释」的文本上，而不是原文。
    # 否则会命中字符串字量里的代码样本 —— 最常见的两处：
    #   1. 自检用例本身：case("JS 空 catch", {"bad.js": "try { f() } catch (e) {}"} ...)
    #      这行把一条空 catch 写进了 .py 文件的字符串里，扫全文必然命中
    #   2. 文档/配置里的示例代码片段
    # 误报不能用登记基线「赦免」（那是给真违规留的出口），只能让扫描器看不见字符串。
    #
    # mask_js_strings_and_comments 用轻量词法扫描，单双引号、反引号三态都处理，
    # Python 的单/双引号字符串同样覆盖 —— 所以不按扩展名分流，统一屏蔽。
    scan_src = mask_js_strings_and_comments(text)

    seen_spans: "set[tuple[int, int]]" = set()  # 同一位置命中多条模式时只报第一条，避免刷屏
    for p in rules["patterns"]:
        for m in re.finditer(p["regex"], scan_src, re.MULTILINE):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            line_no = scan_src.count("\n", 0, m.start()) + 1
            nl = scan_src.find("\n", m.start())
            snippet = text[m.start() : nl if nl != -1 else len(text)]
            issues.append(
                {
                    "pattern": p["id"],
                    "file": rel,
                    "line": line_no,
                    "why": p.get("why", ""),
                    "snippet": snippet.strip()[:120],
                }
            )
    return issues


# ────────────────────────── 自检 ──────────────────────────


_SELFTEST_RULES = {
    "extensions": [".py", ".js", ".java"],
    "skip_paths": ["skip/**"],
    "builtin": ["except-pass"],
    "patterns": [
        {"id": "bare-except", "regex": r"^\s*except\s*:\s*(?:#.*)?$", "why": "裸 except"},
        {"id": "empty-catch", "regex": r"catch\s*(?:\([^)]*\))?\s*\{\s*\}", "why": "空 catch"},
    ],
}


def _selftest() -> int:
    import tempfile

    failures = 0

    def case(name: str, files: dict, expect: int) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            # 与 main() 同样的目标筛选：skip_paths 在这一层生效
            targets = [rel for rel in files if not is_skipped(rel, _SELFTEST_RULES["skip_paths"])]
            total = []
            for rel in targets:
                total.extend(check_one(rel, root, _SELFTEST_RULES))
            if len(total) == expect:
                print(f"  [v] {name}")
            else:
                print(f"  [x] {name}\n      期望 {expect} 处，实际 {len(total)}：{total}")
                failures += 1

    case("正常异常处理 → 通过", {"ok.py": "try:\n    f()\nexcept ValueError as e:\n    log.warning(str(e))\n"}, 0)
    case("裸 except → 违规", {"bad1.py": "try:\n    f()\nexcept:\n    pass\n"}, 2)  # 裸 except + except-pass 同段
    case("except Exception: pass → 违规", {"bad2.py": "try:\n    f()\nexcept Exception:\n    pass\n"}, 1)
    case("except 带注释后仍只 pass → 违规", {"bad3.py": "try:\n    f()\nexcept OSError:  # 这里没问题\n    pass\n"}, 1)
    case("except 里有真实处理 → 通过", {"ok2.py": "try:\n    f()\nexcept Exception:\n    log.exception('failed')\n"}, 0)
    case("JS 空 catch → 违规", {"bad.js": "try { f() } catch (e) {}\n"}, 1)
    case("JS 无参空 catch → 违规", {"bad2.js": "try { f() } catch {}\n"}, 1)
    case("JS catch 有处理 → 通过", {"ok.js": "try { f() } catch (e) { console.error(e) }\n"}, 0)
    case("skip_paths 命中的文件不查", {"skip/x.py": "try:\n    f()\nexcept Exception:\n    pass\n"}, 0)
    case("非目标扩展名不查", {"note.md": "except: pass\n"}, 0)
    case("pass 后面有其他语句不算 except-pass", {"ok3.py": "try:\n    f()\nexcept Exception:\n    pass\n    log.info('handled')\n"}, 0)
    # 字符串字面量里的代码样本不是真违规。这条是 0.8.0 提交时被自家 pre-commit
    # 拦出来的真误报：上面那两条 JS 自检用例（{"bad.js": "try { f() } catch (e) {}"}）
    # 本身就是「空 catch」文本，被扫全文必然命中。
    case("字符串里的空 catch 样本 → 不算违规", {"sample.py": "CASES = [{\"js\": \"try { f() } catch (e) {}\"}]\n"}, 0)
    case("注释里的空 catch → 不算违规", {"c.js": "// try { f() } catch (e) {}\n"}, 0)
    case("真实空 catch 仍要拦（屏蔽后不能漏报）", {"real.js": "try { f() } catch (e) {}\n"}, 1)

    # 基线过滤
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "x.baseline.json"
        bp.write_text(json.dumps({"entries": ["a.py:except-pass"]}), encoding="utf-8")
        keys = baseline_store.load(bp)
        fake = [{"file": "a.py", "pattern": "except-pass"}, {"file": "b.py", "pattern": "bare-except"}]
        fresh = [v for v in fake if violation_key(v) not in keys]
        if len(keys) == 1 and len(fresh) == 1 and fresh[0]["file"] == "b.py":
            print("  [v] 基线过滤（键 = 文件:模式id）")
        else:
            print(f"  [x] 基线逻辑异常：keys={keys} fresh={fresh}")
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：12/12")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="调试坏味检查（根因协议）")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--baseline", help="基线文件路径（默认自动发现 baselines/<rules 名>.baseline.json）")
    ap.add_argument("--update-baseline", action="store_true", help="把当前违规写入基线（棘轮登记），退出码 0")
    ap.add_argument(
        "--accept-debt",
        action="store_true",
        help="棘轮闸门：本次会新增债务时，显式认账放行（必须配 --debt-reason）",
    )
    ap.add_argument("--debt-reason", default="", help="认账理由，会记进基线文件的 accepted 段")
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

    try:
        baseline_path, baseline = baseline_store.open_baseline(Path(args.rules), args.baseline, args.update_baseline)
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(i) for i in issues]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path, keys, baseline, args.accept_debt, args.debt_reason, set(targets)
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [i for i in issues if violation_key(i) in baseline]
    fresh = [i for i in issues if violation_key(i) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处调试坏味：\n")
        for i in fresh:
            print(f"  {i['file']}:{i['line']}  [{i['pattern']}]")
            print(f"    {i['snippet']}")
            if i["why"]:
                print(f"    为什么拦：{i['why']}")
            print()
        print("让症状闭嘴不等于修好问题。要么给 except 里写清处理与证据（日志/上抛），")
        print("要么收窄异常类型。确实要暂时放行，走 --update-baseline 登记成显式债务。")
        print()
        if debts and not args.quiet:
            print(f"[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）")
        return 1

    if debts and not args.quiet:
        print(f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。")
    if not args.quiet:
        print(f"[v] 调试坏味检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
