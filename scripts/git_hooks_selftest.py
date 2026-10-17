# -*- coding: utf-8 -*-
"""scripts/git_hooks_selftest.py —— git_hooks 的自检用例

独立成文件的原因：主文件守住 size-naming 的 500 行门禁（doc-sync 同款先例）。
跑法：python scripts/git_hooks.py --selftest

用例全部在临时目录里 git init，不碰任何真实仓库。
"""

import subprocess
import tempfile
from pathlib import Path

from git_hooks import EVENTS, current_hooks_path, install, load_record, uninstall


def _make_repo():
    """临时 git 仓库。返回 (上下文管理器, 根路径) —— 别让 td 提前销毁。"""
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    subprocess.run(
        ["git", "init", "-q"], cwd=str(root), check=True, capture_output=True
    )
    return td, root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_selftest() -> int:
    failures = 0

    def case(name: str, expect: bool, detail: str = "") -> None:
        nonlocal failures
        ok = expect
        print(f"  [{'v' if ok else 'x'}] {name}")
        if not ok:
            print(f"      {detail}")
            failures += 1

    # 1. 干净项目：接管但无宿主钩子
    td, root = _make_repo()
    try:
        rc = install(root)
        rec = load_record(root)
        case(
            "干净项目安装 → previous 为空、无宿主钩子",
            rc == 0
            and rec is not None
            and rec["previous_hooksPath"] == ""
            and all(rec[ev] is None for ev in EVENTS)
            and current_hooks_path(root) == ".githooks",
            f"rc={rc} rec={rec} hp={current_hooks_path(root)!r}",
        )
        # 5a. 卸载还原（原先没设 hooksPath）
        rc = uninstall(root)
        case(
            "卸载 → hooksPath 清空、钩子文件移除",
            rc == 0
            and current_hooks_path(root) == ""
            and not (root / ".githooks" / "pre-commit").exists()
            and load_record(root) is None,
            f"rc={rc} hp={current_hooks_path(root)!r}",
        )
    finally:
        td.cleanup()

    # 2. 默认钩子目录里有宿主 pre-commit
    td, root = _make_repo()
    try:
        _write(root / ".git" / "hooks" / "pre-commit", "#!/bin/sh\nexit 0\n")
        rc = install(root)
        rec = load_record(root)
        case(
            "默认目录宿主钩子 → 记录 .git/hooks/pre-commit",
            rc == 0 and rec["pre-commit"] == ".git/hooks/pre-commit",
            f"rc={rc} rec={rec}",
        )
    finally:
        td.cleanup()

    # 3 + 4. husky 形态：core.hooksPath=.husky，重装不覆盖存档
    td, root = _make_repo()
    try:
        _write(root / ".husky" / "pre-commit", "#!/bin/sh\nexit 0\n")
        _write(root / ".husky" / "commit-msg", "#!/bin/sh\nexit 0\n")
        subprocess.run(
            ["git", "config", "core.hooksPath", ".husky"],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
        install(root)
        rec1 = load_record(root)
        install(root)  # 幂等重装
        rec2 = load_record(root)
        case(
            "husky 宿主 → pre-commit/commit-msg 都接力",
            rec1["pre-commit"] == ".husky/pre-commit"
            and rec1["commit-msg"] == ".husky/commit-msg"
            and rec1["pre-push"] is None,
            f"rec1={rec1}",
        )
        case(
            "幂等重装 → previous 仍是 .husky（不被 .githooks 覆盖）",
            rec2["previous_hooksPath"] == ".husky"
            and rec2["pre-commit"] == ".husky/pre-commit",
            f"rec2={rec2}",
        )
        # 5b. 卸载还原（原先 hp=.husky）
        rc = uninstall(root)
        case(
            "卸载 → hooksPath 还原为 .husky、宿主钩子原样保留",
            rc == 0
            and current_hooks_path(root) == ".husky"
            and (root / ".husky" / "pre-commit").is_file()
            and not (root / ".githooks" / "pre-commit").exists(),
            f"rc={rc} hp={current_hooks_path(root)!r}",
        )
    finally:
        td.cleanup()

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：5/5")
    return 0
