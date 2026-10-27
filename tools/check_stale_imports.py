#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_stale_imports.py —— 失效导入路径检查

抓「模块被搬迁/改名后，引用没跟上」的失效导入：导入了当前仓库里根本不存在的
模块，运行时必然 ModuleNotFoundError / Cannot find module。这是分层方向检查
（只看方向）与文档同步检查（只看文档）之外的盲区。

支持两类语言，按规则文件的配置分别启用：
    - Python（AST 收集，含函数体延迟导入）：绝对导入按 packages 白名单校验，
      相对导入按 level 上溯还原后校验
    - JS/TS/Vue（正则收集，零依赖）：只查 `./` `../` 相对导入与规则配置的
      别名（如 `@/`）；裸说明符（第三方包 / Node 内建）天然跳过

解析优先级对齐 CPython FileFinder：`X/__init__.py`（常规包）> `X.py`（模块）>
`X/`（命名空间目录）。同名包/模块共存时包优先 —— 判错方向会把合法导入误报。

退出码：0 = 无新增违规（基线存量不阻断）；1 = 有新增违规；2 = 用法/规则错误
（绝不能当通过处理）。

基线（棘轮）：与 check_import_direction.py 同一约定 —— 键 = 「文件:模块」
（不含行号，行号随编辑漂移）。命中基线只提示不阻断，新增违规照常阻断。

用法：
    python tools/check_stale_imports.py --rules rules/stale-import-guard.json --all
    python tools/check_stale_imports.py --rules <rules.json> --files a.py b.js
    python tools/check_stale_imports.py --rules <rules.json> --update-baseline
    python tools/check_stale_imports.py --rules <rules.json> --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_store
import import_collectors

TIMEOUT_GIT = 30
PY_EXTS = {".py"}
JS_EXTS_DEFAULT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue")


class RuleError(Exception):
    """规则文件的问题。对应退出码 2。"""


# ────────────────────────── 规则 ──────────────────────────


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    for key in ("roots", "extensions"):
        if not isinstance(data.get(key), list) or not data[key]:
            raise RuleError(f"规则文件缺少 {key}（需要非空列表）")
    data["packages"] = data.get("packages") or []  # 空 = 不查 Python 绝对导入
    data["javascript"] = data.get("javascript") or None  # None = 不查 JS
    data.setdefault("check_relative", True)
    data.setdefault("skip_paths", [])
    return data


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """把 glob 转成正则。** 跨目录，* 不跨目录。"""
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


def is_skipped(rel: str, skip_patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(rel) for p in skip_patterns)


def violation_key(v: dict) -> str:
    """基线键 = 文件:模块（不含行号，行号随编辑漂移）。"""
    return f"{v['file']}:{v['module']}"


# ────────────────────────── 文件收集 ──────────────────────────


def git_staged_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=root, capture_output=True, timeout=TIMEOUT_GIT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError(f"读取 git 暂存文件失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuleError("git 命令失败，当前目录可能不是 git 仓库")
    out = proc.stdout.decode("utf-8", errors="replace")
    return [line.strip() for line in out.splitlines() if line.strip()]


def all_files(root: Path, rules: dict) -> list[str]:
    exts = {e.lower() for e in rules["extensions"]}
    out = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        rel = f.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in f"/{rel}":
            continue
        out.append(rel)
    return sorted(out)


# ────────────────────────── Python 解析 ──────────────────────────


def _descend(node: Path, part: str, is_last: bool) -> str:
    """在 node 下解析片段 part，优先级对齐 CPython FileFinder。

    同名 `X/__init__.py`（常规包）优先于 `X.py`（模块），两者皆无才是 `X/`
    （命名空间目录）。本项目里 `application/account_manager.py` 与
    `application/account_manager/` 同名共存，判错会让「包优先」的合法导入
    被误报成失效。

    返回 'leaf'（成功且最后一段）| 'descend'（成功且有下级）| 'miss' | 'blocked'
    （命中模块文件但还有下级，该 root 下不可能继续，需换 root）。
    """
    if not node.is_dir():
        return "miss"
    pkg_init = node / part / "__init__.py"
    mod_file = node / (part + ".py")
    if pkg_init.is_file():
        return "leaf" if is_last else "descend"
    if mod_file.is_file():
        return "leaf" if is_last else "blocked"
    if (node / part).is_dir():
        return "leaf" if is_last else "descend"
    return "miss"


def resolve_absolute(module: str, roots: list[Path]) -> bool:
    """Python 绝对模块路径能否落到某个 root 下的真实 .py 文件或目录。"""
    parts = module.split(".")
    for root in roots:
        if not root.is_dir():
            continue
        node, ok = root, False
        for i, part in enumerate(parts):
            verdict = _descend(node, part, i == len(parts) - 1)
            if verdict == "leaf":
                ok = True
                break
            if verdict == "descend":
                node = node / part
                continue
            break  # 'miss' / 'blocked' → 换下一个 root
        if ok:
            return True
    return False


def resolve_relative_py(level: int, module: str, file_abs: Path) -> "bool | None":
    """Python 相对导入。module 为空（`from . import x`）返回 None（不判）。"""
    if not module:
        return None
    base = file_abs.parent
    for _ in range(max(level - 1, 0)):
        base = base.parent
    node, parts = base, module.split(".")
    for i, part in enumerate(parts):
        verdict = _descend(node, part, i == len(parts) - 1)
        if verdict == "leaf":
            return True
        if verdict == "descend":
            node = node / part
            continue
        return False  # 'miss' / 'blocked'
    return False


def _py_roots(root: Path, rel: str, rules: dict) -> list[Path]:
    """解析根：规则声明的 roots + 被检查文件自身所在目录（覆盖「脚本按同级
    目录跑」的布局）。"""
    seen: list[Path] = []
    for cand in [(root / rel).resolve().parent, *((root / r).resolve() for r in rules["roots"])]:
        if cand not in seen:
            seen.append(cand)
    return seen


# ────────────────────────── JS / TS 解析 ──────────────────────────


def resolve_js(spec: str, file_abs: Path, root: Path, rules: dict) -> str:
    """JS 模块说明符判定。返回 'ok' | 'stale' | 'skip'。

    只查相对导入（./ ../）与别名（规则 javascript.aliases，如 {"@/": "src/"}，
    值相对项目根）；裸说明符（第三方包 / Node 内建）一律 skip —— node_modules
    解析不在范围内。
    """
    js = rules["javascript"]
    exts = tuple(js.get("extensions", JS_EXTS_DEFAULT))
    if spec.startswith("./") or spec.startswith("../"):
        base, tail = file_abs.parent, spec
    else:
        aliases = js.get("aliases") or {}
        hit = next((p for p in sorted(aliases, key=len, reverse=True) if spec.startswith(p)), None)
        if hit is None:
            return "skip"
        base, tail = (root / aliases[hit]).resolve(), spec[len(hit):]
    direct = base / tail
    if direct.is_file():
        return "ok"
    if any((base / (tail + e)).is_file() for e in exts):
        return "ok"
    if any((base / tail / ("index" + e)).is_file() for e in exts):
        return "ok"
    return "stale"


# ────────────────────────── 检查 ──────────────────────────


def check_one(root: Path, rel: str, rules: dict) -> list[dict]:
    path = root / rel
    if not path.is_file():
        return []
    ext = path.suffix.lower()
    if ext not in {e.lower() for e in rules["extensions"]}:
        return []
    try:
        if ext in PY_EXTS:
            imports, _tc = import_collectors.collect_python(path, rel)
        else:
            imports = import_collectors.collect_javascript(path, rel)
    except import_collectors.ParseError as exc:
        raise RuleError(str(exc)) from exc

    packages = set(rules["packages"])
    py_roots = _py_roots(root, rel, rules) if packages else []
    file_abs = path.resolve()
    violations = []
    for lineno, mod, text in imports:
        if ext in PY_EXTS:
            if mod.startswith("."):
                if not rules.get("check_relative", True):
                    continue
                level = len(mod) - len(mod.lstrip("."))
                if resolve_relative_py(level, mod.lstrip("."), file_abs) is False:
                    violations.append({"file": rel, "line": lineno, "module": mod, "text": text, "kind": "relative"})
            elif mod.split(".")[0] in packages and not resolve_absolute(mod, py_roots):
                violations.append({"file": rel, "line": lineno, "module": mod, "text": text, "kind": "absolute"})
        else:
            if resolve_js(mod, file_abs, root, rules) == "stale":
                violations.append({"file": rel, "line": lineno, "module": mod, "text": text, "kind": "javascript"})
    return violations


# ────────────────────────── 自检 ──────────────────────────

_SELFTEST_RULES = {
    "packages": ["pkg"], "roots": ["."], "extensions": [".py", ".js", ".vue"],
    "check_relative": True, "skip_paths": ["skip/**"],
    "javascript": {"aliases": {"@/": "src/"}, "extensions": (".js", ".ts", ".vue")},
}
_SELFTEST_LAYOUT = {
    "pkg/__init__.py": "", "pkg/mod.py": "", "pkg/res.py": "",
    "pkg/sub/__init__.py": "", "pkg/sub/deep.py": "",
    # 同名模块与包共存（CPython 包优先）；命名空间包（无 __init__）
    "pkg/dual.py": "", "pkg/dual/__init__.py": "", "pkg/dual/inner.py": "",
    "pkg/ns/thing.py": "", "pkg/nd.py": "", "pkg/nd/inner.py": "",
    "src/comp.js": "", "src/comp.ts": "", "src/box/index.ts": "",
}


def _selftest() -> int:
    import tempfile

    failures = 0

    def case(name: str, files: dict, expect: int, rules: dict | None = None) -> None:
        nonlocal failures
        active = rules or _SELFTEST_RULES
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for relp, content in {**_SELFTEST_LAYOUT, **files}.items():
                p = root / relp
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            targets = [r for r in files if not is_skipped(r, active["skip_paths"])]
            total: list = []
            try:
                for relp in targets:
                    total.extend(check_one(root, relp, active))
            except RuleError as exc:
                print(f"  [x] {name}\n      {exc}")
                failures += 1
                return
            if len(total) == expect:
                print(f"  [v] {name}")
            else:
                print(f"  [x] {name}\n      期望 {expect} 处，实际 {len(total)}：{total}")
                failures += 1

    # ── Python ──
    case("py 存在模块 → 通过", {"pkg/a.py": "from pkg.mod import X\n"}, 0)
    case("py 缺失模块 → 违规", {"pkg/a.py": "from pkg.gone import X\n"}, 1)
    case("py 模块下取子模块（不可能）→ 违规", {"pkg/a.py": "from pkg.res.sub import X\n"}, 1)
    case("py 目录形子包深层 → 通过", {"pkg/a.py": "from pkg.sub.deep import X\n"}, 0)
    case("py 第三方库 → 跳过", {"pkg/a.py": "import pandas\nfrom numpy import array\n"}, 0)
    case("py 非白名单顶层 → 跳过", {"pkg/a.py": "from other.gone import X\n"}, 0)
    case("py 函数体延迟导入缺失 → 仍违规", {"pkg/a.py": "def f():\n    from pkg.gone import X\n    return X\n"}, 1)
    case("py 同名包/模块共存 → 包优先", {"pkg/a.py": "from pkg.dual.inner import X\n"}, 0)
    case("py 命名空间包 → 通过", {"pkg/a.py": "from pkg.ns.thing import X\n"}, 0)
    case("py 同名模块优先于无__init__目录 → 违规", {"pkg/a.py": "from pkg.nd.inner import X\n"}, 1)
    case("py 相对导入缺失 → 违规", {"pkg/a.py": "from .gone import X\n"}, 1)
    case("py from . import 符号 → 不判", {"pkg/a.py": "from . import mod\n"}, 0)
    case("py 相对导入关闭时不检查",
         {"pkg/a.py": "from .gone import X\n"}, 0, {**_SELFTEST_RULES, "check_relative": False})
    case("py 白名单为空时绝对导入不查",
         {"pkg/a.py": "from pkg.gone import X\n"}, 0, {**_SELFTEST_RULES, "packages": []})
    case("skip_paths 命中的文件不查", {"skip/x.py": "from pkg.gone import X\n"}, 0)
    case("非目标扩展名不查", {"note.md": "from pkg.gone import X\n"}, 0)

    # ── JS / TS ──
    case("js 相对导入存在（带扩展名）→ 通过", {"a.js": "import {x} from './src/comp.js'\n"}, 0)
    case("js 相对导入存在（省扩展名 .ts）→ 通过", {"a.js": "import {x} from './src/comp'\n"}, 0)
    case("js 目录 index 解析 → 通过", {"a.js": "import {x} from './src/box'\n"}, 0)
    case("js 相对导入缺失 → 违规", {"a.js": "import {x} from './src/gone'\n"}, 1)
    case("js 别名导入存在 → 通过", {"a.js": "import {x} from '@/comp'\n"}, 0)
    case("js 别名导入缺失 → 违规", {"a.js": "import {x} from '@/gone/x'\n"}, 1)
    case("js 裸说明符（第三方/内建）→ 跳过",
         {"a.js": "import _ from 'lodash'\nconst fs = require('fs')\nimport('node:fs')\n"}, 0)
    case("js require 缺失 → 违规", {"a.js": "const x = require('./gone')\n"}, 1)
    case("js export from 缺失 → 违规", {"a.js": "export {x} from './gone'\n"}, 1)
    case("vue script 内导入缺失 → 违规",
         {"a.vue": "<template><div/></template>\n<script>\nimport {x} from './gone'\n</script>\n"}, 1)

    # 基线过滤：键 = 文件:模块
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "x.baseline.json"
        bp.write_text(json.dumps({"entries": ["pkg/a.py:pkg.gone"]}), encoding="utf-8")
        keys = baseline_store.load(bp)
        fake = [{"file": "pkg/a.py", "module": "pkg.gone"}, {"file": "pkg/b.py", "module": "pkg.gone"}]
        fresh = [v for v in fake if violation_key(v) not in keys]
        if len(keys) == 1 and len(fresh) == 1 and fresh[0]["file"] == "pkg/b.py":
            print("  [v] 基线过滤（键 = 文件:模块）")
        else:
            print(f"  [x] 基线逻辑异常：keys={keys} fresh={fresh}")
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：27/27")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="失效导入路径检查（Python + JS/TS）")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="扫描全部源文件")
    ap.add_argument("--files", nargs="*", help="只检查指定文件")
    ap.add_argument("--quiet", action="store_true", help="只在有违规时输出")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检")
    ap.add_argument("--baseline", help="基线文件路径（默认按约定自动发现 baselines/<rules 名>.baseline.json）")
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

    skip = rules["skip_paths"]
    try:
        targets = args.files if args.files else (
            all_files(root, rules) if args.all else git_staged_files(root)
        )
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    exts = {e.lower() for e in rules["extensions"]}
    targets = [t for t in targets if Path(t).suffix.lower() in exts and not is_skipped(t, skip)]

    all_violations: list[dict] = []
    for rel in targets:
        try:
            all_violations.extend(check_one(root, rel, rules))
        except (RuleError, import_collectors.ParseError) as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 2

    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline)
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(v) for v in all_violations]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path, keys, baseline, args.accept_debt, args.debt_reason, set(targets)
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [v for v in all_violations if violation_key(v) in baseline]
    fresh = [v for v in all_violations if violation_key(v) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处失效导入（目标模块在本仓库不存在）：\n")
        for v in fresh:
            print(f"  {v['file']}:{v['line']}  [{v['kind']}]  {v['module']}")
            print(f"    {v['text']}")
            print()
        print("这类导入运行时会 ModuleNotFoundError / Cannot find module。修法：把模块路径改成")
        print("搬迁后的实际位置，或模块已删则一并清理引用。确实要暂时放行，走 --update-baseline")
        print("登记成显式债务。")
        if debts and not args.quiet:
            print(f"\n[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）")
        return 1

    if debts and not args.quiet:
        print(f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。")
    if not args.quiet:
        print(f"[v] 失效导入路径检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
