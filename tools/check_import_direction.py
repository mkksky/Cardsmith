#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_import_direction.py —— 分层依赖方向检查

检查代码是否违反分层架构的依赖方向：只允许外层 import 内层，反向即违规。

被卡片引用：
    cards/layered-architecture-guard.md
      tools[0].id = import-direction
      hooks: PostToolUse(Write|Edit) / pre-commit

用法：
    python tools/check_import_direction.py --rules rules/layered-architecture-guard.json --all
    python tools/check_import_direction.py --rules <rules.json> --files a.py b.py
    python tools/check_import_direction.py --rules <rules.json>          # 默认取 git 暂存文件
    python tools/check_import_direction.py --rules <rules.json> --update-baseline   # 棘轮：把当前违规登记为基线
    python tools/check_import_direction.py --rules <rules.json> --baseline <path>   # 显式指定基线文件
    python tools/check_import_direction.py --selftest                    # 自检，不读规则

退出码：
    0 = 无违规（基线里的存量债务不算违规）
    1 = 有新增违规
    2 = 用法错误 / 规则文件缺失或格式错误  ← 绝不能当通过处理

支持 Python（AST 解析，能抓函数体内延迟导入）与 Java（正则提取 package/import）。

基线（棘轮）：
    基线文件按约定与规则文件自动关联：<rules 所在目录>/../baselines/<rules 名>.baseline.json，
    也可用 --baseline 显式指定。文件存在即启用：命中基线的违规视为「已登记的存量债务」，
    只提示不阻断；**不在基线里的新增违规照常阻断**。存量修掉后重跑 --update-baseline 收紧。
    基线的键是「文件:模块」，不含行号 —— 行号会随编辑漂移，不能当键。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import baseline_store
import forbidden_checks
import import_collectors
from layer_spec import (
    RuleError,
    layer_of_module,
    layer_of_path,
    load_rules,
)
from source_scan import (
    all_source_files,
    git_staged_files,
    is_skipped,
    violation_key,
)

# ────────────────────────── 规则 ──────────────────────────
# 规则加载与「这条 import 属于哪一层」的判定见 layer_spec.py。
# 拆出去是为了让本文件回到 500 行以内（套件自己的 size-naming 上限），
# 那边是纯逻辑，这边只管收集与比对。

# ────────────────────────── 文件收集 ──────────────────────────
# 暂存区收集 / 全量扫描 / 路径豁免 / 基线键都在 source_scan.py

# ────────────────────────── 解析 ──────────────────────────
# 各语言的 import 收集器在 tools/import_collectors.py。
# 本工具只做层级判断：文件属于哪层 → 导入了谁 → 方向对不对。


# ────────────────────────── 检查 ──────────────────────────


def resolve_relative(mod: str, rel_path: str) -> str:
    """把相对导入的 ..x.y 还原成绝对模块路径（尽力而为）。"""
    if not mod.startswith("."):
        return mod
    level = len(mod) - len(mod.lstrip("."))
    tail = mod.lstrip(".")
    parts = Path(rel_path).with_suffix("").parts
    # 去掉当前模块文件名本身，再按 level 上溯
    base = list(parts[:-1])
    for _ in range(max(level - 1, 0)):
        if base:
            base.pop()
    if tail:
        base.extend(tail.split("."))
    return ".".join(base)


def check_file(
    root: Path,
    rel: str,
    layers: list[dict],
    exempt: dict,
    defs: dict,
) -> "list[dict]":
    path = root / rel
    if not path.is_file():
        return []

    # 组合根豁免：负责把具体实现装配进抽象接口的文件（组合根/桥接层），
    # 引用 infrastructure 是其本职，不是违规。典型：service_factory、application_context。
    if is_skipped(rel, defs.get("composition_roots") or []):
        return []

    src_layer = layer_of_path(rel, layers)
    if src_layer is None:
        return []  # 不属于任何层，不检查

    if rel.endswith(".py"):
        imports, tc_ranges = import_collectors.collect_python(path, rel)
        # 一次解析同时拿到导入与 TYPE_CHECKING 范围，不做二次 AST 解析
        if bool(exempt.get("type_checking_blocks", False)):
            imports = [
                (ln, m, t)
                for (ln, m, t) in imports
                if not any(s <= ln <= e for s, e in tc_ranges)
            ]
        lang = "python"
    elif rel.endswith(".java"):
        imports = import_collectors.collect_java(path, rel)
        lang = "java"
    else:
        return []

    violations = []
    # 只对项目自身的代码判层（project_packages 非空时生效）。
    # 第三方库的模块名里常常带着层名 —— jakarta.persistence.Entity、
    # org.springframework.dao.XxxRepository —— 被 **/persistence/**、**/repository/**
    # 这类 glob 一匹配，就成了「domain 反向依赖 infrastructure」的假警报。
    # 留空则判定范围不变（所有 import 都参与），所以是 opt-in。
    project_packages = defs.get("project_packages") or []
    for lineno, mod, text in imports:
        if lang == "python":
            mod = resolve_relative(mod, rel)
        if project_packages and not any(
            mod == pkg or mod.startswith(pkg + ".") for pkg in project_packages
        ):
            continue
        target_layer = layer_of_module(mod, layers)
        if target_layer is None or target_layer["name"] == src_layer["name"]:
            continue
        # 跨层直连：方向规则允许「外层依赖内层」，所以 presentation → infrastructure
        # 永远是合法的，但它绕过了 service 的业务规则与事务边界。判定见 forbidden_checks。
        violations.extend(
            forbidden_checks.dependency_violations(
                rel, lineno, text, src_layer, target_layer, mod, defs
            )
        )
        src_rank, dst_rank = src_layer["rank"], target_layer["rank"]
        policy = defs.get("policy", "outer-may-depend-on-inner")
        if policy == "outer-may-depend-on-inner":
            bad = src_rank < dst_rank
        else:
            bad = src_rank > dst_rank
        if bad:
            violations.append(
                {
                    "file": rel,
                    "line": lineno,
                    "from_layer": src_layer["name"],
                    "from_rank": src_rank,
                    "to_layer": target_layer["name"],
                    "to_rank": dst_rank,
                    "module": mod,
                    "text": text,
                }
            )

    # 层级禁用的注解：典型是 Controller 上写 @Transactional。
    violations.extend(
        forbidden_checks.annotation_violations(path, rel, src_layer, defs)
    )
    return violations


# ────────────────────────── 自检 ──────────────────────────


_SELFTEST_RULES = {
    "layers": [
        {"name": "domain", "rank": 0, "patterns": ["**/domain/**"]},
        {"name": "infrastructure", "rank": 2, "patterns": ["**/infrastructure/**"]},
    ],
    "policy": "outer-may-depend-on-inner",
    "exempt": {"type_checking_blocks": True},
    "skip_paths": [],
}


def _selftest() -> int:
    import tempfile

    failures = 0

    def case(name: str, files: dict, expect_violations: int) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            total = []
            for rel in files:
                try:
                    total.extend(
                        check_file(
                            root,
                            rel,
                            _SELFTEST_RULES["layers"],
                            _SELFTEST_RULES["exempt"],
                            _SELFTEST_RULES,
                        )
                    )
                except RuleError as exc:
                    print(f"  [x] {name}\n      {exc}")
                    failures += 1
                    return
            if len(total) == expect_violations:
                print(f"  [v] {name}")
            else:
                print(
                    f"  [x] {name}\n      期望 {expect_violations} 处违规，实际 {len(total)} 处：{total}"
                )
                failures += 1

    case(
        "内层引用外层 → 违规",
        {"src/domain/user.py": "from src.infrastructure.db import session\n"},
        1,
    )
    case(
        "外层引用内层 → 放行",
        {"src/infrastructure/db.py": "from src.domain.user import User\n"},
        0,
    )
    case(
        "同层引用 → 放行",
        {"src/domain/user.py": "from src.domain.order import Order\n"},
        0,
    )
    case(
        "函数体内延迟导入 → 仍违规",
        {
            "src/domain/user.py": "def f():\n    from src.infrastructure.db import s\n    return s\n"
        },
        1,
    )
    case(
        "TYPE_CHECKING 块 → 放行",
        {
            "src/domain/user.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from src.infrastructure.db import S\n"
        },
        0,
    )

    # 组合根豁免：composition_roots 命中的文件引用内层实现 → 放行
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "src/domain/user.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("from src.infrastructure.db import session\n", encoding="utf-8")
        rules2 = {**_SELFTEST_RULES, "composition_roots": ["**/user.py"]}
        got = check_file(
            root, "src/domain/user.py", rules2["layers"], rules2["exempt"], rules2
        )
        if got == []:
            print("  [v] 组合根豁免：composition_roots 命中即放行")
        else:
            print(f"  [x] 组合根豁免未生效：{got}")
            failures += 1

    # project_packages：只对项目自身代码判层，第三方库名里带的层名不算跨层
    # （jakarta.persistence.Table 的模块路径会被 **/persistence/** 误匹配成 infrastructure）
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "src/domain/entity/User.java"
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("import jakarta.persistence.Table;\n", encoding="utf-8")
        layers = [
            {"name": "domain", "rank": 0, "patterns": ["**/domain/**"]},
            {"name": "infrastructure", "rank": 2, "patterns": ["**/persistence/**"]},
        ]
        base = {**_SELFTEST_RULES, "layers": layers}
        unrestricted = check_file(root, rel, layers, base["exempt"], base)
        scoped = check_file(
            root,
            rel,
            layers,
            base["exempt"],
            {**base, "project_packages": ["com.congge"]},
        )
        if len(unrestricted) == 1 and scoped == []:
            print("  [v] project_packages：第三方库名带层名不再误判")
        else:
            print(
                f"  [x] project_packages 过滤异常：unrestricted={unrestricted} scoped={scoped}"
            )
            failures += 1

    # 跨层直连 + 层内禁用注解：判定在 forbidden_checks.py，单独自检
    failures += forbidden_checks.selftest()

    # 基线（棘轮）：命中基线的债务放行，未命中的算新增；约定路径定位正确
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "x.baseline.json"
        bp.write_text(json.dumps({"entries": ["a.py:m1", "b.py:m2"]}), encoding="utf-8")
        keys = baseline_store.load(bp)
        fake = [{"file": "a.py", "module": "m1"}, {"file": "c.py", "module": "m3"}]
        fresh = [v for v in fake if violation_key(v) not in keys]
        discovered = baseline_store.default_baseline_path(Path("rules/xxx.json"))
        if (
            len(keys) == 2
            and len(fresh) == 1
            and fresh[0]["file"] == "c.py"
            and discovered.as_posix() == "baselines/xxx.baseline.json"
        ):
            print("  [v] 基线过滤与约定路径定位")
        else:
            print(
                f"  [x] 基线逻辑异常：keys={keys} fresh={fresh} discovered={discovered}"
            )
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：8/8")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="分层依赖方向检查")
    ap.add_argument("--rules", help="规则 JSON 路径")
    ap.add_argument("--root", default=".", help="项目根，默认当前目录")
    ap.add_argument("--all", action="store_true", help="扫描全部源文件")
    ap.add_argument("--files", nargs="*", help="只检查指定文件")
    ap.add_argument("--staged", action="store_true", help="检查 git 暂存文件（默认）")
    ap.add_argument("--quiet", action="store_true", help="只在有违规时输出")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检")
    ap.add_argument(
        "--baseline",
        help="基线文件路径（默认按约定自动发现 baselines/<rules 名>.baseline.json）",
    )
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="把当前违规写入基线（棘轮登记），退出码 0",
    )
    ap.add_argument(
        "--accept-debt",
        action="store_true",
        help="棘轮闸门：本次会新增债务时，显式认账放行（必须配 --debt-reason）",
    )
    ap.add_argument(
        "--debt-reason", default="", help="认账理由，会记进基线文件的 accepted 段"
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

    layers = rules["layers"]
    exempt = rules["exempt"]
    skip = rules["skip_paths"]

    try:
        if args.files:
            targets = list(args.files)
        elif args.all:
            targets = all_source_files(root)
        else:
            targets = git_staged_files(root)
    except RuleError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    targets = [
        t
        for t in targets
        if (t.endswith(".py") or t.endswith(".java")) and not is_skipped(t, skip)
    ]

    # 零命中检测：某个层一个文件都没匹配到 → 多半是 pattern 写错了。
    # 这种「门禁在跑但什么都没检查」的假阳性最危险，必须报出来。
    if args.all:
        matched = {layer["name"]: 0 for layer in layers}
        for rel in targets:
            try:
                hit = layer_of_path(rel, layers)
            except RuleError:
                continue
            if hit:
                matched[hit["name"]] += 1
        empty = [n for n, c in matched.items() if c == 0]
        if empty:
            print(
                f"[!] 这些层没有匹配到任何文件，请检查 rules 里的 patterns：{', '.join(empty)}",
                file=sys.stderr,
            )

    all_violations: list[dict] = []
    for rel in targets:
        try:
            all_violations.extend(check_file(root, rel, layers, exempt, rules))
        except (RuleError, import_collectors.ParseError) as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 2

    # 提示档（mode=advisory）单独摘出来：打印但不影响退出码，也不进基线。
    # 阻断档（方向违规 / mode=blocking）才走棘轮。
    all_violations, advisory = forbidden_checks.split_advisory(all_violations)
    forbidden_checks.print_advisory(advisory)

    # ── 基线（棘轮）：存量债务放行，新增阻断 ──
    try:
        baseline_path, baseline = baseline_store.open_baseline(
            Path(args.rules), args.baseline, args.update_baseline
        )
    except baseline_store.BaselineError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        keys = [violation_key(v) for v in all_violations]
        try:
            plan = baseline_store.apply_ratchet(
                baseline_path,
                keys,
                baseline,
                args.accept_debt,
                args.debt_reason,
                set(targets),
            )
        except baseline_store.RatchetError as exc:
            print(f"[x] {exc}", file=sys.stderr)
            return 1
        print(baseline_store.format_ratchet_report(baseline_path, plan))
        return 0

    debts = [v for v in all_violations if violation_key(v) in baseline]
    fresh = [v for v in all_violations if violation_key(v) not in baseline]

    if fresh:
        print(f"[x] 发现 {len(fresh)} 处新增跨层依赖违规：\n")
        for v in fresh:
            print(f"  {v['file']}:{v['line']}")
            print(
                f"    {v['from_layer']}(rank {v['from_rank']}) → {v['to_layer']}(rank {v['to_rank']})"
            )
            print(f"    {v['text']}")
            print()
        print("分层规则：只允许外层依赖内层。修法二选一 ——")
        print("  1. 把被依赖的逻辑下沉到内层（推荐）")
        print("  2. 用接口/协议反转依赖：内层定义抽象，外层提供实现")
        if debts and not args.quiet:
            print(
                f"\n[!] 另有 {len(debts)} 处为基线登记的存量债务，不阻断（基线：{baseline_path}）"
            )
        return 1

    if debts and not args.quiet:
        print(
            f"[!] {len(debts)} 处存量债务在基线中，已放行。修掉后跑 --update-baseline 收紧。"
        )
    if not args.quiet:
        print(
            f"[v] 分层依赖方向检查通过（检查了 {len(targets)} 个文件，新增 0 处违规）"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
