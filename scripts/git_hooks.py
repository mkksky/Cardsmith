#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/git_hooks.py —— git 钩子安装 / 卸载（钩子合并）

git 的钩子是「唯一插槽」：一个仓库只有一条 pre-commit 生效路径，core.hooksPath
是单值配置。早期版本装钩子时无条件把它指到 .githooks，项目自带的 pre-commit
（如 某 Python 项目 的 ruff/mypy/pytest 门禁）被整体绕过 —— 静默失效比没有更
危险（docs/HOOKS-VERIFY.md 实锤过）。

现在的做法（链式接力）：
    安装时探测宿主钩子 → 路径记录到 <项目>/.githooks/legacy-hook
    → .githooks/<event> 跑完套件检查后接力执行宿主钩子，聚合退出码
    → uninstall 还原原 core.hooksPath，删掉本套件装的钩子文件

记录文件 .githooks/legacy-hook（JSON，安装时生成，勿手改）：
    {
      "previous_hooksPath": "",                // 安装前 core.hooksPath 的值，没设过为 ""
      "pre-commit": ".git/hooks/pre-commit",   // 宿主钩子脚本（相对项目根），没有为 null
      "commit-msg": null,
      "pre-push": null
    }

被 scripts/install.py 调用：
    python -c "import git_hooks; git_hooks.install(project)"
    python -c "import git_hooks; git_hooks.uninstall(project)"

自检：python scripts/git_hooks.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_GIT = 30
EVENTS = ("pre-commit", "commit-msg", "pre-push")
HOOKS_DIR = ".githooks"
LEGACY_REL = HOOKS_DIR + "/legacy-hook"


class GitHookError(Exception):
    """git 命令层面的失败。调用方打印后返回退出码 1。"""


# ────────────────────────── git 探测 ──────────────────────────


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, timeout=TIMEOUT_GIT
    )


def current_hooks_path(project: Path) -> str:
    """读 core.hooksPath 的当前值。没设过返回 ""。"""
    proc = _git(project, "config", "--get", "core.hooksPath")
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


def default_hooks_dir(project: Path) -> "Path | None":
    """git 默认钩子目录（兼容 worktree：.git 可能是文件而非目录）。"""
    proc = _git(project, "rev-parse", "--git-path", "hooks")
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        return None
    p = Path(out)
    return p if p.is_absolute() else project / out


# ────────────────────────── 宿主钩子探测 ──────────────────────────


def detect_legacy(project: Path, hooks_path: str) -> dict:
    """探测宿主钩子。返回 {event: 相对路径或 None}。

    hooks_path 非空时以它为宿主目录（husky 等都走这条路）；为空时用
    git rev-parse --git-path hooks 拿默认目录。
    """
    if hooks_path:
        base = Path(hooks_path)
        base = base if base.is_absolute() else project / hooks_path
    else:
        base = default_hooks_dir(project)
    found = {}
    for ev in EVENTS:
        p = (base / ev) if base else None
        found[ev] = p.relative_to(project).as_posix() if p and p.is_file() else None
    return found


def load_record(project: Path) -> "dict | None":
    f = project / LEGACY_REL
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_record(project: Path, record: dict) -> None:
    f = project / LEGACY_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ────────────────────────── 安装 ──────────────────────────


def install(project: Path, dry: bool = False) -> int:
    """装三个钩子并接管 core.hooksPath。宿主钩子不覆盖，改链式接力。"""
    hook_dest = project / HOOKS_DIR
    repo = Path(__file__).resolve().parent.parent

    if dry:
        print(f"    [+] {HOOKS_DIR}/{{pre-commit,commit-msg,pre-push}} → {hook_dest}")
        print("    [~] core.hooksPath → .githooks（宿主钩子探测后链式接力）")
        return 0

    hook_dest.mkdir(parents=True, exist_ok=True)
    hp = current_hooks_path(project)

    if hp == HOOKS_DIR:
        # 重装：沿用已存的存档，别把 .githooks 自己记成宿主钩子
        record = load_record(project) or {
            "previous_hooksPath": "",
            **{ev: None for ev in EVENTS},
        }
    else:
        record = {"previous_hooksPath": hp, **detect_legacy(project, hp)}

    copied = []
    for name in EVENTS:
        src = repo / HOOKS_DIR / name
        if not src.is_file():
            print(f"    [!] 源钩子不存在，跳过：{name}")
            continue
        shutil.copy2(src, hook_dest / name)
        copied.append(name)
    save_record(project, record)

    proc = _git(project, "config", "core.hooksPath", HOOKS_DIR)
    if proc.returncode != 0:
        print("    [x] 设置 core.hooksPath 失败（可能不是 git 仓库）")
        return 1

    print(
        f"    [+] {HOOKS_DIR}/{'+'.join(copied) or '（空）'} 已装，"
        f"core.hooksPath 已指向 {HOOKS_DIR}"
    )
    chained = [f"{ev} ← {record[ev]}" for ev in EVENTS if record.get(ev)]
    if chained:
        print("    [+] 宿主钩子已探测到，套件检查通过后链式接力：")
        for c in chained:
            print(f"        {c}")
    else:
        print("    [·] 未发现宿主钩子，无接力（干净接管）")
    print(f"    [·] 还原：python scripts/install.py --project <项目> --uninstall-git-hook")
    return 0


# ────────────────────────── 卸载 ──────────────────────────


def uninstall(project: Path, dry: bool = False) -> int:
    """还原原 core.hooksPath，删掉本套件装的钩子文件与存档。"""
    record = load_record(project)
    hp = current_hooks_path(project)
    if hp != HOOKS_DIR and record is None:
        print("    [·] 未安装本套件 git 钩子，无需卸载")
        return 0

    prev = (record or {}).get("previous_hooksPath", "")
    if dry:
        target = prev or "（清空，回落 git 默认钩子目录）"
        print(f"    [~] core.hooksPath → {target}")
        return 0

    if prev:
        proc = _git(project, "config", "core.hooksPath", prev)
    else:
        proc = _git(project, "config", "--unset", "core.hooksPath")
    if proc.returncode != 0:
        print("    [x] 还原 core.hooksPath 失败")
        return 1
    print(f"    [+] core.hooksPath → {prev or '（已清空，回落默认）'}")

    hook_dest = project / HOOKS_DIR
    removed = []
    for name in (*EVENTS, LEGACY_REL.rsplit("/", 1)[-1]):
        f = hook_dest / name
        if f.is_file():
            f.unlink()
            removed.append(name)
    try:
        hook_dest.rmdir()  # 只在空目录时成功 —— 用户自己放的文件不会被动
    except OSError:
        print(f"    [·] {hook_dest} 还有其它文件，保留目录")
    print(f"    [+] 已移除：{', '.join(removed) or '（空）'}")
    return 0


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    """用例在 git_hooks_selftest.py —— 主文件守住 500 行上限。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import git_hooks_selftest

    return git_hooks_selftest.run_selftest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="git 钩子安装/卸载（钩子合并）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    print("用法见 scripts/install.py（--with-git-hook / --uninstall-git-hook）")
    sys.exit(2)
