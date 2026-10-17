#!/usr/bin/env python3
"""release-checklist 聚合器：交付前把全部守卫工具的 --all 结果汇总成一张体检表。

设计要点：
- 清单不硬编码 —— 从 hooks/registry.json（build.py 生成的唯一注册表）动态取，
  新增守卫卡片后体检表自动纳入，不需要改本工具。
- git 事件专用工具（commit-msg 需要 message 文件、sync-state 会联网）通过规则
  JSON 的 exclude 排除 —— 它们不适合在交付体检里跑。
- 退出码：任一检查 FAIL → 1；全过（含 WARN）→ 0。WARN = 工具退出 0 但有输出
  （套件约定的非阻断提醒）。

用法：
    python tools/release_checklist.py --rules rules/release-checklist.json
    python tools/release_checklist.py --rules ... --selftest

--root 接受两种值并统一收敛到运行时根（tools/ 的所在地）：手动执行给
<项目>/.cardsmith（默认），run.py 调用给项目根。见 resolve_runtime_root()。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

EXIT_PASS, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
DEFAULT_TIMEOUT = 120


def project_root(runtime: Path) -> Path:
    """被检查文件所属的项目根。**与运行时根不是一个东西。**

    装到项目后 tools/ 在 <项目>/.cardsmith/ 下，而被检查的文件在 <项目>/ 下：
        --registry / 找脚本 / cwd  → 运行时根
        传给子工具的 --root        → 项目根
    混成一个会让子工具去扫 .cardsmith 自己，表现为「domain / application /
    presentation 都没匹配到任何文件」这类**假通过/假警报**而不是报错。
    （与 hooks/run.py、scripts/run_precommit.py 里的同名函数同一约定；
      2026-09-25 在 某 Python 项目 上实锤：--root 传项目根 → 全部子工具报
      「脚本缺失」，传运行时根 → 子工具实际扫的是 .cardsmith。）
    """
    return runtime.parent if runtime.name == ".cardsmith" else runtime


def resolve_runtime_root(candidate: Path) -> Path:
    """把 --root 统一收敛到**运行时根**（tools/ 的所在地）。

    这个参数有两条来路，值不一样：
        手动执行  → 运行时根（<项目>/.cardsmith，也是默认值 REPO_ROOT）
        run.py 调用 → 项目根（hooks/run.py 对所有工具一律传项目根）

    本工具要用 root 定位脚本，所以只认运行时根。给到项目根时会去找
    <项目根>/tools/*.py —— 不存在，于是**每一行都报「脚本缺失」**，
    看着像套件坏了，其实是参数语义不对（2026-09-25 实测踩到）。
    判据用「tools/ 在不在」：能对上就直接用，对不上再看有没有 .cardsmith 子目录。
    """
    if candidate.name == ".cardsmith" or (candidate / "tools").is_dir():
        return candidate
    nested = candidate / ".cardsmith"
    return nested if (nested / "tools").is_dir() else candidate


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_lines(text: str, n: int) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]


def run_checks(root: Path, registry_path: Path, rules: dict,
               timeout: int) -> tuple[list[dict], bool]:
    """跑注册表里除 exclude 外的全部工具。返回 (结果行, 配置是否完整)。"""
    exclude = set(rules.get("exclude") or [])
    self_script = Path(__file__).resolve()
    reg = load_json(registry_path)
    tools = reg.get("tools") or {}
    configured = True
    rows: list[dict] = []
    for tid, entry in tools.items():
        if tid in exclude:
            rows.append({"id": tid, "card": entry.get("card", "?"),
                         "status": "SKIP", "note": "规则排除"})
            continue
        if (root / entry.get("script", "")).resolve() == self_script:
            rows.append({"id": tid, "card": entry.get("card", "?"),
                         "status": "SKIP", "note": "自身（不递归）"})
            continue
        script = root / entry.get("script", "")
        if not script.is_file():
            rows.append({"id": tid, "card": entry.get("card", "?"),
                         "status": "FAIL", "note": f"脚本缺失：{entry.get('script')}",
                         "output": []})
            configured = False
            continue
        cmd = [sys.executable, str(script),
               *[str(a) for a in (entry.get("args") or [])],
               "--all", "--root", str(project_root(root))]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, cwd=str(root))
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            rc, out = 124, f"超时（>{timeout}s）"
            note = f"超时（>{timeout}s）"
        else:
            note = f"exit={rc} {time.time() - t0:.1f}s"
        has_warn = any(ln.lstrip().startswith("[!]") for ln in out.splitlines())
        if rc == 0:
            status = "WARN" if has_warn else "PASS"
            tail = [ln for ln in out.splitlines() if ln.lstrip().startswith("[!]")]
        else:
            status = "FAIL"
            tail = _tail_lines(out, 5)
        rows.append({"id": tid, "card": entry.get("card", "?"),
                     "status": status, "note": note, "output": tail})
    return rows, configured


def print_report(rows: list[dict], quiet: bool) -> None:
    icon = {"PASS": "[v]", "FAIL": "[x]", "WARN": "[!]", "SKIP": "[-]"}
    for r in rows:
        if quiet and r["status"] in ("PASS", "SKIP"):
            continue
        print(f"  {icon[r['status']]} {r['id']:<18} {r['status']:<4} {r['note']}")
        for ln in r.get("output") or []:
            print(f"        {ln}")
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    detail = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    if any(r["status"] == "FAIL" for r in rows):
        print(f"[x] 交付体检未通过（{detail}）——先修复 FAIL 项再交付")
    else:
        print(f"[v] 交付体检通过（{detail}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="交付前全守卫聚合体检")
    ap.add_argument("--rules", help="规则 JSON 路径（exclude/timeout）")
    ap.add_argument("--registry", default=str(REPO_ROOT / "hooks" / "registry.json"),
                    help="工具注册表路径（默认 hooks/registry.json）")
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.rules:
        print("[x] 缺少 --rules 参数", file=sys.stderr)
        return EXIT_USAGE
    root = resolve_runtime_root(Path(args.root).resolve())
    rules = load_json(Path(args.rules))
    rows, ok = run_checks(root, Path(args.registry), rules,
                          int(rules.get("timeout_seconds") or DEFAULT_TIMEOUT))
    print_report(rows, args.quiet)
    if not ok:
        return EXIT_USAGE
    return EXIT_FAIL if any(r["status"] == "FAIL" for r in rows) else EXIT_PASS


# ────────────────────────── 自检 ──────────────────────────


def selftest() -> int:
    import tempfile
    import os

    failures = 0
    checks = 0

    def check(name, cond, detail=""):
        nonlocal failures, checks
        checks += 1
        if cond:
            print(f"  [v] {name}")
        else:
            failures += 1
            print(f"  [x] {name}  {detail}")

    fake_tool = 'import sys\nprint("OK")\n'
    fail_tool = 'import sys\nprint("boom"); sys.exit(3)\n'
    warn_tool = 'import sys\nprint("[!] 可疑但不拦")\n'
    slow_tool = "import time; time.sleep(3)\n"

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "fake").mkdir()
        for fname, body in (("t_pass.py", fake_tool), ("t_fail.py", fail_tool),
                            ("t_warn.py", warn_tool), ("t_slow.py", slow_tool)):
            (tdp / "fake" / fname).write_text(body, encoding="utf-8")
        reg = {"tools": {
            "pass": {"card": "c1", "script": "fake/t_pass.py", "args": []},
            "fail": {"card": "c2", "script": "fake/t_fail.py", "args": []},
            "warn": {"card": "c3", "script": "fake/t_warn.py", "args": []},
            "slow": {"card": "c4", "script": "fake/t_slow.py", "args": []},
            "excluded": {"card": "c5", "script": "fake/不存在.py", "args": []},
        }}
        reg_path = tdp / "registry.json"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        rules = {"exclude": ["excluded"], "timeout_seconds": 1}
        rules_path = tdp / "rules.json"
        rules_path.write_text(json.dumps(rules), encoding="utf-8")

        # 用例 1：全场景混合 —— fail 与 slow 超时应 FAIL，warn 应 WARN，excluded 应 SKIP
        rows, ok = run_checks(tdp, reg_path, rules, timeout=1)
        st = {r["id"]: r["status"] for r in rows}
        check("混合场景状态判定", st == {"pass": "PASS", "fail": "FAIL",
                                        "warn": "WARN", "slow": "FAIL",
                                        "excluded": "SKIP"}, f"实际 {st}")
        check("fail 项存在 → 整体失败判定", any(r["status"] == "FAIL" for r in rows))
        slow_row = next(r for r in rows if r["id"] == "slow")
        check("超时计入 FAIL 且注明", "超时" in slow_row["note"], slow_row["note"])

        # 用例 2：只留全过工具 → exit 0
        reg2 = {"tools": {"pass": reg["tools"]["pass"], "warn": reg["tools"]["warn"]}}
        (tdp / "reg2.json").write_text(json.dumps(reg2), encoding="utf-8")
        rows2, _ = run_checks(tdp, tdp / "reg2.json", rules, timeout=10)
        check("全过（含 WARN）判定通过", not any(r["status"] == "FAIL" for r in rows2))
        check("WARN 仍被标记（非阻断提醒可见）",
              any(r["status"] == "WARN" for r in rows2))

        # 用例 3：排除项真的没被执行（脚本缺失的 excluded 没报 FAIL）
        check("excluded 项未触发脚本缺失", st["excluded"] == "SKIP")

        # 用例 4：脚本缺失计 FAIL 且 configured=False
        reg4 = {"tools": {"ghost": {"card": "c", "script": "fake/nope.py", "args": []}}}
        (tdp / "reg4.json").write_text(json.dumps(reg4), encoding="utf-8")
        rows4, ok4 = run_checks(tdp, tdp / "reg4.json", rules, timeout=10)
        check("脚本缺失计 FAIL", rows4[0]["status"] == "FAIL" and not ok4)

        # 用例 5：main() 端到端退出码（patch argv）
        import importlib
        mod = importlib.import_module("release_checklist") if \
            "release_checklist" not in sys.modules else sys.modules["release_checklist"]
        saved = sys.argv
        try:
            sys.argv = ["release_checklist.py", "--rules", str(rules_path),
                        "--registry", str(reg_path), "--root", str(tdp)]
            rc = mod.main()
            check("main 混合场景 exit=1", rc == 1, f"rc={rc}")
            sys.argv = ["release_checklist.py", "--rules", str(rules_path),
                        "--registry", str(tdp / "reg2.json"), "--root", str(tdp)]
            rc = mod.main()
            check("main 全过场景 exit=0", rc == 0, f"rc={rc}")
        finally:
            sys.argv = saved

        # 用例 6：--root 收敛 —— 项目的 run.py 传项目根，手动执行传运行时根，都得出同一结果
        (tdp / ".cardsmith" / "tools").mkdir(parents=True)
        check("--root 给运行时根 → 原样使用",
              resolve_runtime_root(tdp / ".cardsmith") == tdp / ".cardsmith")
        check("--root 给项目根 → 收敛到 .cardsmith",
              resolve_runtime_root(tdp) == tdp / ".cardsmith")
        check("--root 给无关目录 → 原样返回（交给上层报脚本缺失）",
              resolve_runtime_root(tdp / "fake") == tdp / "fake")

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print(f"自检全绿：{checks}/{checks}")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
