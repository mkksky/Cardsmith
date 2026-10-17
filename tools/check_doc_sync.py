#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_doc_sync.py —— 文档同步守卫

文档漂移是慢性的：代码改了文档没跟上，当时没人发现，三个月后文档比没有更毒
（照着文档调接口，参数对不上）。本工具抓两类**机器可裁断**的漂移：

    1. path-ref   文档里引用的文件/目录在磁盘上不存在（路径时效性）
    2. api-ref    文档声明的方法/类在源码 AST 里不存在（签名漂移，v1 只查名字）

明确不做的（做不了/不该做）：
    - 判断文档"内容还对不对" —— 那是判断题，工具只能查事实
    - 比对参数默认值 —— 误报高，v1 只查名字存在性

路径解析顺序（命中任一即通过；引用前导 `/` 按项目根相对处理）：
    <文档所在目录>/<ref>  →  <项目根>/<ref>  →  <项目根>/<source_root>/<ref>
    →  <项目根>/<cf_doc_bases 推导基>/<ref>
    第三级是为「文档省略仓库子目录前缀」的项目准备的（如 monorepo 里
    源码在 okxtradesys.gitee/ 下，文档写 application/...）；第四级是
    云函数文档邻域（docs/05-cloudfunctions/<fn>/ 的 README 写云函数内
    相对路径）。引用尾部的行号/符号锚点/查询串（`xxx.js:64` 等）查盘前
    剥掉；末段无扩展名的 pages/ 路由补 .js 探测。

退出码：0=通过或无可查文件 1=有新增违规 2=规则或配置错误
基线：键 = <文档>:<ref或符号>:<kind>，不含行号。--update-baseline 登记存量。

被卡片引用：
    cards/doc-sync-guard.md

用法：
    python tools/check_doc_sync.py --rules rules/doc-sync-guard.json
    python tools/check_doc_sync.py --rules <rules.json> --all
    python tools/check_doc_sync.py --rules <rules.json> --files README.md
    python tools/check_doc_sync.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store  # noqa: E402
from doc_sync_paths import candidate_refs, compile_allow_patterns, extra_bases  # noqa: E402
from doc_sync_paths import is_allowed_ref, strip_check_suffixes  # noqa: E402

TIMEOUT_GIT = 30

# 行内 code span（不跨行）
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# markdown 链接目标
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# api 引用：`name(...)`
_CALL_REF = re.compile(r"^([A-Za-z_]\w*)\(")
# 首尾要剥掉的标点（code span 常带句读）
_PUNCT = " \t.,;:!?，。；：！？）)】》\"'`"

_SCHEME = ("http://", "https://", "ftp://", "mailto:", "data:", "file://", "cloud://")


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
    for key in ("doc_roots", "doc_extensions", "path_suffixes", "skip_paths"):
        if not isinstance(rules.get(key), list):
            raise RuleError(f"规则缺少 {key} 列表：{path}")
    for key in ("source_roots", "allow_refs", "allow_ref_patterns", "skip_symbols", "api_docs", "cf_doc_bases"):
        rules.setdefault(key, [])
    rules.setdefault("check_bare_names", False)
    # 形态白名单在加载期编译：坏正则属配置错误（退出码 2），不能拖到检查时才崩
    try:
        rules["_compiled_allow_patterns"] = compile_allow_patterns(rules["allow_ref_patterns"])
    except (TypeError, re.error) as exc:
        raise RuleError(f"allow_ref_patterns 配置非法：{exc}") from exc
    return rules


def glob_to_regex(pattern: str):
    """支持 ** / * / ? 的 glob → regex。** 跨目录，* 不跨。

    开头的 **/ 按**标准 glob 语义**可匹配零层目录：**/node_modules/** 要能
    命中 node_modules/x.js 本身，否则跳过规则对"目录就在根下"的形态失效
    （secrets-guard 自检抓过：**/src/** 匹配不上 src/app.py）。
    """
    head = "^"
    if pattern.startswith("**/"):
        head = "^(?:.*/)?"
        pattern = pattern[3:]
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile(head + "".join(out) + "$")


def is_skipped(rel: str, skip_patterns: list) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


# ────────────────────────── 文档收集 ──────────────────────────


def collect_doc_files(root: Path, rules: dict) -> "list[str]":
    """按 doc_roots 白名单收集要查的文档，返回相对项目根的 posix 路径。"""
    exts = {e.lower() for e in rules["doc_extensions"]}
    out = set()
    for entry in rules["doc_roots"]:
        base = root / entry
        if base.is_file():
            out.add(Path(entry).as_posix())
        elif base.is_dir():
            for f in base.rglob("*"):
                if f.is_file() and f.suffix.lower() in exts:
                    rel = f.relative_to(root).as_posix()
                    if "/.git/" not in f"/{rel}" and "/__pycache__/" not in f"/{rel}":
                        out.add(rel)
        # 既不是文件也不是目录 → 声明有误，留着让用户发现（不静默）
    return sorted(out)


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
    return [
        ln.strip()
        for ln in proc.stdout.decode("utf-8", errors="replace").splitlines()
        if ln.strip()
    ]


# ────────────────────────── 引用抽取 ──────────────────────────


def _clean(token: str) -> str:
    """剥掉 code span 里混进来的句读与锚点，返回干净的疑似路径。

    注意不能对整串做 strip(_PUNCT)：前导的 "." 会被吃掉，./guide.md 变 /guide.md
    （自检抓过）。前导只剥空白和引号类标点，保留 . / 以识别相对路径。
    """
    tok = token.strip()
    tok = tok.lstrip(" \t,;:!?，。；：！？）)】》\"'`").rstrip(_PUNCT)
    if tok.startswith("./"):
        tok = tok[2:]
    anchor = tok.find("#")
    if anchor != -1 and "/" in tok:  # docs/x.md#section → docs/x.md
        tok = tok[:anchor]
    return tok.rstrip(_PUNCT).strip()


def _is_pathlike(tok: str, rules: dict) -> bool:
    if not tok or " " in tok:
        return False
    if tok.lower().startswith(_SCHEME):
        return False
    if tok.startswith("~") or tok.startswith("$"):
        return False
    if any(ch in tok for ch in "*<>{}|\"'`"):
        return False
    if "/" in tok:
        return True
    # 裸文件名（无斜杠）：散文里 "check.py 会报错" 多为泛指，默认不查，
    # check_bare_names: true 才查 —— 首轮真数据验证过的取舍（自检有覆盖）
    suffixes = tuple(s.lower() for s in rules["path_suffixes"])
    return bool(rules.get("check_bare_names")) and tok.lower().endswith(suffixes)


def extract_path_refs(text: str, rules: dict) -> "list[str]":
    """从 markdown 文本抽疑似路径引用（code span + markdown 链接），去重保序。"""
    seen, refs = set(), []
    tokens = [m.group(1) for m in _CODE_SPAN.finditer(text)]
    tokens += [m.group(1) for m in _MD_LINK.finditer(text)]
    for token in tokens:
        tok = _clean(token)
        if not _is_pathlike(tok, rules):
            continue
        if is_allowed_ref(tok, rules):  # 精确白名单 + 形态白名单（单位/数字比/证据目录）
            continue
        if tok not in seen:
            seen.add(tok)
            refs.append(tok)
    return refs


def extract_call_refs(text: str, rules: dict) -> "list[str]":
    """从 code span 里抽 `name(...)` 形态的方法引用。"""
    seen, names = set(), []
    for m in _CODE_SPAN.finditer(text):
        cm = _CALL_REF.match(m.group(1).strip())
        if not cm:
            continue
        name = cm.group(1)
        if name in rules.get("skip_symbols", []):
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


# ────────────────────────── 检查 ──────────────────────────


def _exists_somewhere(doc_rel: str, ref: str, root: Path, rules: dict) -> bool:
    """引用在 任一文档邻域 / 项目根 / source_roots / cf 邻域下存在 即通过。

    前导 `/` 的引用按项目根相对处理（小程序路由写法），不能用 pathlib
    直接 join —— Path('a') / '/b' 会得到绝对路径 /b，把前缀全丢掉。
    cf 邻域基（extra_bases）同时按「项目根相对」和「source_root 相对」
    两种布局探测 —— 扁平仓前者命中，monorepo（源码在子目录）后者命中。
    """
    ref = ref.lstrip("/")
    doc_dir = (root / doc_rel).parent
    srs = list(rules.get("source_roots", []))
    extras = extra_bases(doc_rel, rules)
    base_dirs = [doc_dir, root]
    base_dirs += [root / sr for sr in srs]
    base_dirs += [root / b for b in extras]
    base_dirs += [root / sr / b for sr in srs for b in extras]
    candidates = [d / r for d in base_dirs for r in candidate_refs(ref)]
    return any(c.exists() for c in candidates)


def check_path_refs(doc_rel: str, root: Path, rules: dict) -> "list[dict]":
    path = root / doc_rel
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    issues = []
    seen = set()
    for raw in extract_path_refs(text, rules):
        ref = strip_check_suffixes(raw)
        if not ref or ref in seen:  # foo.js:1 与 foo.js:2 剥完同径，只报一次
            continue
        seen.add(ref)
        if not _exists_somewhere(doc_rel, ref, root, rules):
            issues.append(
                {
                    "kind": "path-ref",
                    "file": doc_rel,
                    "ref": ref,
                    "why": "文档引用的路径在磁盘上不存在（项目根与 source_roots 都没有）",
                }
            )
    return issues


def collect_defined_symbols(root: Path, search_roots: "list[str]") -> set:
    """AST 收集 search_roots 下所有 .py 定义的类名/函数名/方法名。"""
    names: set = set()
    for sr in search_roots:
        base = root / sr
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    names.add(node.name)
    return names


def check_api_doc(entry: dict, root: Path, rules: dict) -> "list[dict]":
    doc_rel = str(entry.get("doc", "")).replace("\\", "/")
    if not doc_rel:
        raise RuleError("api_docs 条目缺少 doc 字段")
    doc_path = root / doc_rel
    issues: list = []
    if not doc_path.is_file():
        issues.append(
            {
                "kind": "api-doc-missing",
                "file": doc_rel,
                "ref": "(整个文件)",
                "why": "规则声明了这份 API 文档，但文件不存在",
            }
        )
        return issues
    search_roots = [str(s).replace("\\", "/") for s in entry.get("search_roots", [])]
    if not search_roots:
        raise RuleError(f"api_docs 条目 {doc_rel} 缺少 search_roots")
    defined = collect_defined_symbols(root, search_roots)
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    for name in extract_call_refs(text, rules):
        if name not in defined:
            issues.append(
                {
                    "kind": "api-ref",
                    "file": doc_rel,
                    "ref": name,
                    "why": f"文档声明的方法/类 {name} 在 search_roots 的源码里不存在（改代码没同步文档）",
                }
            )
    return issues


def violation_key(issue: dict) -> str:
    return f"{issue['file']}:{issue['kind']}:{issue['ref']}"


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="文档同步检查（路径时效性 + API 签名对账）"
    )
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
        if args.files is not None:
            # 只查指定的文档：md 走 path-ref，声明过的 api 文档走 api-ref
            given = [f.replace("\\", "/") for f in args.files if f]
            doc_set = set(given)
            issues = []
            for f in doc_set:
                if f.lower().endswith(
                    tuple(e.lower() for e in rules["doc_extensions"])
                ):
                    issues += check_path_refs(f, root, rules)
            for entry in rules.get("api_docs", []):
                if str(entry.get("doc", "")).replace("\\", "/") in doc_set:
                    issues += check_api_doc(entry, root, rules)
        elif args.all:
            issues = []
            for doc in collect_doc_files(root, rules):
                if not is_skipped(doc, rules["skip_paths"]):
                    issues += check_path_refs(doc, root, rules)
            for entry in rules.get("api_docs", []):
                issues += check_api_doc(entry, root, rules)
        else:
            staged = git_staged_files(root)
            return main_with_files(staged, root, rules, args)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    return report(issues, Path(args.rules), args)


def main_with_files(staged: "list[str]", root: Path, rules: dict, args) -> int:
    """staged 模式：暂存文件里**属于 doc_roots 白名单**的文档才查。

    白名单必须和 --all 同一套，否则生成的 SKILL.md、卡片示例这类非白名单
    文档会在提交时被扫出来（pre-commit 实测踩过）。"""
    doc_exts = tuple(e.lower() for e in rules["doc_extensions"])
    allowed = set(collect_doc_files(root, rules))
    api_docs = {
        str(e.get("doc", "")).replace("\\", "/") for e in rules.get("api_docs", [])
    }
    issues: list = []
    for f in staged:
        rel = f.replace("\\", "/")
        if is_skipped(rel, rules["skip_paths"]):
            continue
        if rel in allowed and rel.lower().endswith(doc_exts):
            issues += check_path_refs(rel, root, rules)
        if rel in api_docs:
            for entry in rules.get("api_docs", []):
                if str(entry.get("doc", "")).replace("\\", "/") == rel:
                    issues += check_api_doc(entry, root, rules)
    return report(issues, Path(args.rules), args)


def report(issues: "list[dict]", rules_path: Path, args) -> int:
    try:
        baseline_path, baseline = baseline_store.open_baseline(
            rules_path, args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(i) for i in issues]
        baseline_store.write(baseline_path, keys)
        print(f"[+] 基线已更新：{baseline_path}")
        print(
            f"    登记存量债务 {len(set(keys))} 条。新增违规仍会阻断；修掉存量后重跑本命令收紧。"
        )
        return 0

    debts = [i for i in issues if violation_key(i) in baseline]
    fresh = [i for i in issues if violation_key(i) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处文档漂移：\n")
        for i in fresh:
            print(f"  {i['file']}  [{i['kind']}]")
            print(f"    引用：{i['ref']}")
            if i["why"]:
                print(f"    为什么拦：{i['why']}")
            print()
        print("文档比没有更毒：照着过期文档调接口，参数对不上还以为是自己错。")
        print("改代码时同步改文档；确实暂时放行，走 --update-baseline 登记成显式债务。")
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
        print("[v] 文档同步检查通过")
    return 0


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """用例在 doc_sync_selftest.py —— 主文件守住 500 行上限（size-naming 门禁）。"""
    import doc_sync_selftest

    return doc_sync_selftest.run_selftest()


if __name__ == "__main__":
    sys.exit(main())
