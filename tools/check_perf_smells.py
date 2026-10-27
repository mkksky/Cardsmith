#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_perf_smells.py —— 加载链路性能坏味检查（性能守卫的执行体）

抓的不是「慢」，是**能被机器确认的慢**：把本可以并发/合并/省略的工作，
写成了排队、重复、无谓等待。三类：

    1. 串行 N+1       循环体内 await —— N 次往返排队，改成收集后 Promise.all 就完了
    2. 渲染抖动       循环体内 setData —— 每次 setData 都是一次逻辑层→渲染层通信
    3. 列表复用失效   wx:for 缺 wx:key / wx:key 用 index —— diff 退化成全量重填
    4. 假同步         await new Promise(r => setTimeout(r, N)) —— 用睡眠等异步完成
    5. forEach + async 不等待 —— 遍历结束就往下走，回调还在飞

设计取舍（继承 check_debug_smells 的经验）：
    - **只做确定性判定**。块级结构（循环体范围）用括号/缩进感知的检测器，
      正则做不准；单行模式才用正则。宁可漏报，不可误报 ——
      误报过三成的门禁会把人逼出 --no-verify，比没有门禁更糟
    - 判定不了的（"这个接口该不该合并"、"图片要不要裁剪"）不做，
      写进卡片的「守不住的」一节，靠 review
    - 基线（棘轮）与其它工具同一约定，键 = 文件:模式id

被卡片引用：
    cards/perf-path-guard.md

用法：
    python tools/check_perf_smells.py --rules rules/perf-guard.json --all
    python tools/check_perf_smells.py --rules rules/perf-guard.json --files a.js b.wxml
    python tools/check_perf_smells.py --rules rules/perf-guard.json              # 默认取 git 暂存文件
    python tools/check_perf_smells.py --rules rules/perf-guard.json --update-baseline
    python tools/check_perf_smells.py --rules rules/perf-guard.json --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_store, perf_exemptions
from import_collectors import mask_js_strings_and_comments

TIMEOUT_GIT = 30
# 块级检测器注册表：id -> (适用扩展名, 块内要命中的正则, 说明)
BUILTIN_SPECS = {
    "await-in-loop": (
        {".js", ".ts", ".jsx", ".tsx", ".vue", ".py"},
        re.compile(r"\bawait\b"),
        "循环体里 await：N 次往返依次排队。除非下一次的参数依赖上一次的结果，否则应先收集再加 Promise.all",
    ),
    "setdata-in-loop": (
        {".js", ".ts", ".jsx", ".tsx", ".vue"},
        re.compile(r"\bsetData\s*\("),
        "循环里 setData：每次都是一趟逻辑层→渲染层通信，且触发多次 diff。应先在内存里攒好再一次性 setData",
    ),
    "wx-for-no-key": (
        {".wxml"},
        None,  # 标签级判定，不走块内正则
        "wx:for 没有 wx:key：diff 无法识别节点身份，刷新时整段重建",
    ),
}


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
    for b in builtins:
        if b not in BUILTIN_SPECS:
            raise RuleError(f"未知内置检测器：{b!r}（可用：{', '.join(sorted(BUILTIN_SPECS))}）")

    if not patterns and not builtins:
        raise RuleError("规则文件既没有 patterns 也没有 builtin，等于什么都没检查")

    data.setdefault("extensions", [".py", ".js", ".ts", ".wxml"])
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
            # 路径解析不到文件 → 检查静默通过
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


# ────────────────────────── 遮罩 ──────────────────────────


def _mask_span(text: str, start: int, end: int) -> str:
    """把 [start,end) 换成空格，换行逐字保留（行号靠数 \\n 算）。"""
    seg = text[start:end]
    return text[:start] + re.sub(r"[^\n]", " ", seg) + text[end:]


def _mask_html_comments(text: str) -> str:
    """wxml 只剥 <!-- --> 注释。

    不能复用 mask_js_strings_and_comments：它会把双引号里的内容一起抹掉，
    而 wxml 的属性值全在双引号里 —— wx:key="index" 的 index 会被抹成空格，
    整个检查就静默失效了。
    """
    out = text
    for m in reversed(list(re.finditer(r"<!--.*?-->", out, re.S))):
        out = _mask_span(out, m.start(), m.end())
    return out


# ────────────────────────── 块级检测 ──────────────────────────

_JS_LOOP = re.compile(r"\b(for|while)\s*\(")
_PY_LOOP = re.compile(r"^([ \t]*)(?:async\s+)?(?:for|while)\b[^:\n]*:\s*(?:#.*)?$")


def _match_pair(src: str, i: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    n = len(src)
    while i < n:
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _js_loop_spans(scan: str) -> "list[tuple[int, int]]":
    """返回 JS/TS 里每个循环体的 (start, end) 字符区间。

    只认 for(...)/while(...)；`for await` 是流式顺序消费，天然串行，跳过。
    """
    spans = []
    for m in _JS_LOOP.finditer(scan):
        if scan[max(0, m.start() - 4) : m.start()].endswith("await "):
            continue
        popen = m.end() - 1
        pclose = _match_pair(scan, popen, "(", ")")
        if pclose == -1:
            continue
        j = pclose + 1
        n = len(scan)
        while j < n and scan[j] in " \t":
            j += 1
        if j < n and scan[j] == "{":
            end = _match_pair(scan, j, "{", "}")
            if end == -1:
                continue
            spans.append((j, end))
        else:
            # 无大括号的单语句形式：for (...) await f(x);
            semi = scan.find(";", j)
            nl = scan.find("\n", j)
            end = min(x for x in (semi, nl, n) if x != -1)
            spans.append((j, end))
    return spans


def _py_loop_spans(text: str) -> "list[tuple[int, int]]":
    """返回 Python 里每个循环体的 (start, end) 字符区间（按缩进判定块边界）。"""
    lines = text.split("\n")
    offsets = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    spans = []
    for idx, line in enumerate(lines):
        m = _PY_LOOP.match(line)
        if not m:
            continue
        base = len(m.group(1).expandtabs())
        end_line = idx
        k = idx + 1
        while k < len(lines):
            cur = lines[k]
            if not cur.strip() or cur.strip().startswith("#"):
                k += 1
                continue
            if len(cur[: len(cur) - len(cur.lstrip())].expandtabs()) > base:
                end_line = k
                k += 1
                continue
            break
        if end_line > idx:
            spans.append((offsets[idx + 1], offsets[end_line] + len(lines[end_line])))
    return spans


def _line_snippet(text: str, pos: int) -> str:
    """取 pos 所在**整行**作为片段。

    不从 pos 开始截：块级命中的 pos 落在循环体的 `{` 上，从那里截只会得到一行
    `{`，看不出是哪一层循环；正则命中从 `wx:key` 起截，前面的 `<view wx:for=...>`
    就丢了。报错要能直接当行动清单，片段就得是整行。
    """
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end != -1 else len(text)].strip()[:120]


def _mask_py(text: str) -> str:
    """Python 侧遮罩：抹字符串内容，再抹 `#` 注释。

    顺序不能反 —— 先剥注释会把字符串里的 `#` 当注释起点，连带吃掉后面的真代码。
    先抹字符串之后，剩下的 `#` 只可能是注释，此时按 `#[^\\n]*` 抹才安全。
    """
    return re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), mask_js_strings_and_comments(text))


def _block_issues(rel: str, text: str, ext: str, bid: str) -> "list[dict]":
    langs, inside, why = BUILTIN_SPECS[bid]
    if ext not in langs:
        return []
    # 块范围在屏蔽后的文本上算，命中也要在屏蔽后的文本上找 —— 否则 `.py` 文件里
    # 写在字符串中的代码样本（自检用例、文档示例）会被当成真违规。
    masked = _mask_py(text) if ext == ".py" else mask_js_strings_and_comments(text)
    spans = _py_loop_spans(masked) if ext == ".py" else _js_loop_spans(masked)
    issues = []
    for start, end in spans:
        if not inside.search(masked, start, end):
            continue
        line_no = masked.count("\n", 0, start) + 1
        issues.append(
            {"file": rel, "pattern": bid, "line": line_no, "snippet": _line_snippet(text, start), "why": why}
        )
    return issues


_TAG = re.compile(r"<([a-zA-Z][\w-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>", re.S)


def _wxml_missing_key(rel: str, text: str) -> "list[dict]":
    issues = []
    masked = _mask_html_comments(text)
    why = BUILTIN_SPECS["wx-for-no-key"][2]
    for m in _TAG.finditer(masked):
        attrs = m.group(2)
        if not re.search(r"\bwx:for\s*=", attrs):
            continue
        if re.search(r"\bwx:key\s*=", attrs):
            continue
        line_no = masked.count("\n", 0, m.start()) + 1
        issues.append(
            {"file": rel, "pattern": "wx-for-no-key", "line": line_no, "snippet": m.group(0).strip()[:120], "why": why}
        )
    return issues


# ────────────────────────── 检查 ──────────────────────────


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

    enabled = rules.get("builtin", [])
    issues: "list[dict]" = []

    # 扩展名要先判：`_wxml_missing_key` 用的是标签正则，对 .py/.js 里
    # 写在字符串中的 wxml 样本照样能匹配（工具自身自检用例就中过招）
    if "wx-for-no-key" in enabled and ext in BUILTIN_SPECS["wx-for-no-key"][0]:
        issues.extend(_wxml_missing_key(rel, text))
    for bid in ("await-in-loop", "setdata-in-loop"):
        if bid in enabled:
            issues.extend(_block_issues(rel, text, ext, bid))

    # 行级正则跑在「屏蔽了字符串与注释」的文本上：自检用例、文档示例里的代码样本
    # 本身就是被检测的写法，扫全文必然命中。误报不能用基线赦免（基线是给真违规留的出口）。
    if ext in {".wxml", ".html", ".vue"}:
        scan_src = _mask_html_comments(text)
    else:
        scan_src = mask_js_strings_and_comments(text)

    seen_spans: "set[tuple[int, int]]" = set()
    for p in rules["patterns"]:
        for m in re.finditer(p["regex"], scan_src, re.MULTILINE):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            seen_spans.add(span)
            line_no = scan_src.count("\n", 0, m.start()) + 1
            issues.append(
                {
                    "file": rel,
                    "pattern": p["id"],
                    "line": line_no,
                    "snippet": _line_snippet(text, m.start()),
                    "why": p.get("why", ""),
                }
            )
    return issues


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """回归用例在 check_perf_smells_selftest.py —— 内联会让本文件超过 500 行上限。"""
    from check_perf_smells_selftest import run

    return run()


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="加载链路性能坏味检查（性能守卫）")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--baseline", help="基线文件路径（默认自动发现 baselines/<rules 名>.baseline.json）")
    ap.add_argument("--update-baseline", action="store_true", help="把当前违规写入基线（棘轮登记），退出码 0")
    ap.add_argument("--accept-debt", action="store_true", help="棘轮闸门：本次会新增债务时显式认账（须配 --debt-reason）")
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
        exemptions = perf_exemptions.load(rules)
    except (RuleError, perf_exemptions.ExemptionError) as exc:
        print(f"[x] 规则或豁免配置问题：{exc}", file=sys.stderr)
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
    exempted, issues = perf_exemptions.partition(issues, exemptions)

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
        print(f"[x] 发现 {len(fresh)} 处性能坏味：\n")
        for i in fresh:
            print(f"  {i['file']}:{i['line']}  [{i['pattern']}]")
            print(f"    {i['snippet']}")
            if i["why"]:
                print(f"    为什么拦：{i['why']}")
            print()
        print("这些都是「本可以并发/合并/省略」的工作被写成了排队。三个常见改法：")
        print("  循环 + await      → 先收集 Promise，再 Promise.all 一次等（注意别超过并发上限）")
        print("  循环 + setData    → 内存里攒成一个对象，循环外 setData 一次")
        print("  wx:key 用 index   → 改成稳定唯一字段（id / _id / 业务主键）")
        print()
        if debts and not args.quiet:
            print(f"[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）")
        return 1

    if debts and not args.quiet:
        print(f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。")
    if exempted and not args.quiet:
        print(f"[i] {len(exempted)} 处命中已按规则显式豁免（非待修债务）。")
    if not args.quiet:
        print(f"[v] 性能坏味检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
