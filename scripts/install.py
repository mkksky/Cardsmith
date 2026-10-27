#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 把套件装到本机 / 装到某个项目

两个装法，用途不同：

    ① 装技能（默认）
       技能 → ~/.workbuddy/skills/<name>/
       让 WorkBuddy 能加载这些技能。用户级，装一次全局可用。

    ② 装运行时（--project <路径>）
       运行时 → <项目>/.codingskills/
         tools/  rules/  hooks/  scripts/
       并检测项目根下存在哪些 IDE 目录
       （.codebuddy / .trae / .claude / .codex / .qoder），
       把 hooks 配置**合并**进对应的 IDE 配置文件。自动适配多 IDE。

    钩子必须「两层同时生效」缺一不可（老板 2026-10-24 定）：
       会话层 —— IDE hooks（Trae: .trae/hooks.json 等），拦 AI 会话里的写操作
       提交层 —— git 钩子（pre-commit / commit-msg / pre-push），拦人工提交
     IDE 钩子只管住 AI 的 Write/Edit，人手动改完直接 commit 它看不见；
     git 钩子只管提交，会话里改坏文件不能即时提醒 —— 所以 --project 模式
     下两层**默认都装**：IDE 层按目录检测自动写，git 层默认安装
     （宿主钩子链式接力不覆盖），不想装 git 层显式传 --no-git-hook。

用法：
    python scripts/install.py --dry-run                   # 先看会做什么
    python scripts/install.py                             # 装全部技能到用户级
    python scripts/install.py --only format-lint-guard
    python scripts/install.py --link                      # 链接安装，改仓库即时生效
    python scripts/install.py --project /path/to/your-project
    python scripts/install.py --project <路径>                # 两层钩子默认都装
    python scripts/install.py --project <路径> --no-git-hook  # 只装会话层
    python scripts/install.py --project <路径> --ide codebuddy trae   # 只装指定 IDE
    python scripts/install.py --project <路径> --ide                  # 跳过 IDE hooks

退出码：0 = 全部成功，1 = 有失败或冲突。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build  # noqa: E402
import git_hooks  # noqa: E402
import ide_hosts  # noqa: E402
from ide_hosts import merge_for_ide  # noqa: E402
import project_baselines  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_TARGET = Path.home() / ".workbuddy" / "skills"
RUNTIME_DIRNAME = ".codingskills"
STATE_FILE = ".codingskills-state.json"

RUNTIME_ITEMS = ("tools", "rules")


# ────────────────────────── 通用 ──────────────────────────


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def remove_tree(path: Path) -> None:
    """删除目录。链接只删链接本身，绝不跟进删源内容。"""
    if is_link(path):
        try:
            os.rmdir(path)
        except OSError:
            path.unlink()
        return
    shutil.rmtree(path)


def make_link(src: Path, dest: Path) -> None:
    if os.name == "nt":
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 不传 text=True：mklink 输出是控制台代码页字节，按 UTF-8 解会炸
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(src)], capture_output=True
        )
        if proc.returncode != 0:
            detail = (
                proc.stderr.decode(errors="replace").strip()
                or proc.stdout.decode(errors="replace").strip()
            )
            raise RuntimeError(f"mklink 失败（退出码 {proc.returncode}）：{detail}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, dest, target_is_directory=True)


def load_state(target: Path) -> set:
    f = target / STATE_FILE
    if not f.is_file():
        return set()
    try:
        return set(json.loads(f.read_text(encoding="utf-8")).get("installed", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_state(target: Path, names: set) -> None:
    with (target / STATE_FILE).open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {"installed": sorted(names), "source": str(REPO_ROOT)},
            fh,
            ensure_ascii=False,
            indent=2,
        )
        fh.write("\n")


# ────────────────────────── ① 装技能 ──────────────────────────


def install_skills(
    target: Path, names: "list[str]", use_link: bool, force: bool, dry: bool
) -> int:
    if not target.is_dir() and not dry:
        target.mkdir(parents=True, exist_ok=True)

    state = load_state(target)
    installed, replaced, conflicts, failed = [], [], [], []

    for name in names:
        src = REPO_ROOT / "skills" / name
        if not (src / "SKILL.md").is_file():
            failed.append((name, "缺少 SKILL.md（先跑 scripts/build.py）"))
            continue

        dest = target / name
        exists = dest.exists() or is_link(dest)
        if exists and name not in state and not force:
            conflicts.append(name)
            continue

        if dry:
            (replaced if exists else installed).append(name)
            continue

        try:
            if exists:
                remove_tree(dest)
            if use_link:
                make_link(src, dest)
            else:
                shutil.copytree(src, dest)
            (replaced if exists else installed).append(name)
        except Exception as exc:  # noqa: BLE001 —— 单个失败不该中断整批
            failed.append((name, str(exc)))

    print(f"  {'[dry-run] ' if dry else ''}技能 → {target}")
    mode = "链接" if use_link else "复制"
    print(f"  方式：{mode}")
    if installed:
        print(f"    [+] 新装 {len(installed)}：{', '.join(installed)}")
    if replaced:
        print(f"    [~] 更新 {len(replaced)}：{', '.join(replaced)}")
    if conflicts:
        print(f"    [x] 冲突 {len(conflicts)}：{', '.join(conflicts)}")
        print("        目标已存在同名目录且不是本套件装的，确认可覆盖后加 --force")
    if failed:
        for n, err in failed:
            print(f"    [!] {n}：{err}")

    if not dry and (installed or replaced):
        save_state(target, state | set(installed) | set(replaced))

    return 1 if (conflicts or failed) else 0


# ────────────────────────── ② 装运行时到项目 ──────────────────────────


def _suite_version() -> str:
    """读套件根的 VERSION。文件缺失返回空串（对账侧会跳过）。"""
    f = REPO_ROOT / "VERSION"
    if not f.is_file():
        return ""
    return f.read_text(encoding="utf-8").strip()


def install_runtime(
    project: Path,
    cards: "list[dict]",
    use_link: bool,
    dry: bool,
    with_git_hook: bool,
    ide_filter: "list[str] | None" = None,
) -> int:
    runtime = project / RUNTIME_DIRNAME
    print(f"  {'[dry-run] ' if dry else ''}运行时 → {runtime}")

    actions: "list[str]" = []

    # tools/ 与 rules/：内容由卡片声明，缺了就是卡片对不上（check.py 会抓）
    for item in RUNTIME_ITEMS:
        src = REPO_ROOT / item
        if not src.is_dir():
            actions.append(f"[!] 源目录不存在，跳过：{item}/")
            continue
        dest = runtime / item
        if dry:
            actions.append(f"[+] {item}/ → {dest}")
            continue

        if item == "rules":
            # 规则文件装完后归项目所有（层级 pattern / 阈值 / skip_paths 要按真实目录
            # 定制）。整体复制会把项目对齐冲掉 —— 在 某 Python 项目 上踩过：
            # 重装一次，presentation 层定义和 .venv 排除全没了，基线跟着作废。
            # 所以这里逐文件：已有的保留，只补套件新增的。
            dest.mkdir(parents=True, exist_ok=True)
            added = kept = 0
            for f in sorted(src.rglob("*.json")):
                dest_f = dest / f.relative_to(src)
                if dest_f.is_file():
                    kept += 1
                    continue
                dest_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest_f)
                added += 1
            actions.append(f"[+] {item}/（新补 {added} 个，保留项目定制 {kept} 个）")
            continue

        if dest.exists():
            remove_tree(dest)
        if use_link:
            make_link(src, dest)
        else:
            shutil.copytree(src, dest)
        actions.append(f"[+] {item}/ → {dest}")

    # baselines/ 与 rules/ 方向相反：rules 缺了就补，baselines 只补「声明了棘轮又还没有文件」的。
    # 项目里的基线是债务清单，重装绝不能覆盖 —— 那等于替项目认账或销账。
    actions.extend(project_baselines.init(runtime, cards, dry))

    # hooks/：run.py 复制；registry/hooks.json 按 prefix 重新生成
    hooks_dest = runtime / "hooks"
    if not dry:
        hooks_dest.mkdir(parents=True, exist_ok=True)
    for rel_src, text in (("run.py", None),):
        src = REPO_ROOT / "hooks" / rel_src
        if src.is_file():
            if dry:
                actions.append(f"[+] hooks/{rel_src} → {hooks_dest / rel_src}")
            else:
                shutil.copy2(src, hooks_dest / rel_src)
                actions.append(f"[+] hooks/{rel_src}")

    prefix = f"{RUNTIME_DIRNAME}/"
    generated = build.tree(cards, prefix=prefix)
    for rel in (
        "hooks/registry.json",
        "hooks/hooks.json",
        "hooks/pre-commit-plan.json",
        "hooks/commit-msg-plan.json",
        "hooks/pre-push-plan.json",
        "RUNTIME-README.md",
    ):
        if rel not in generated:
            continue
        # README 落为运行时根的 README.md —— 换机器克隆项目后，.codingskills/ 不再是黑盒
        dest = runtime / ("README.md" if rel == "RUNTIME-README.md" else rel)
        if dry:
            actions.append(f"[+] {rel} → {dest}（前缀 {prefix}）")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(generated[rel])
            actions.append(f"[+] {rel}（路径前缀 {prefix}）")

    # scripts/run_precommit.py：git 钩子要用
    if with_git_hook:
        src = REPO_ROOT / "scripts" / "run_precommit.py"
        dest = runtime / "scripts" / "run_precommit.py"
        if src.is_file():
            if dry:
                actions.append(f"[+] scripts/run_precommit.py → {dest}")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                actions.append("[+] scripts/run_precommit.py")

    # runtime-state.json：版本对账的存档。run.py 每次调用时拿它和源仓库的
    # VERSION 对撞，落后就警告 —— 旧运行时静默放行新守卫的坑（HOOKS-VERIFY）。
    if not dry:
        state = {
            "suite_version": _suite_version(),
            "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_repo": str(REPO_ROOT),
        }
        (runtime / "runtime-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        actions.append(
            f"[+] runtime-state.json（套件版本 {state['suite_version'] or '未知'}）"
        )

    for a in actions:
        print(f"    {a}")

    # 多 IDE hooks 检测：项目根下存在哪些 IDE 目录就往哪些写
    rc = 0
    detected = ide_hosts.detect(project, ide_filter)
    if detected:
        print(
            f"    IDE 目录检测到 {len(detected)} 个："
            f"{', '.join(a.display for a in detected)}"
        )
        # Trae 官方说会兼容读取 .claude/settings.json，如果 Trae + Claude 都装了
        # 同一条钩子在 Trae 里可能跑两次 —— 提醒用户
        ids = {a.id for a in detected}
        if "trae" in ids and "claude" in ids:
            print(
                "    [!] Trae 会兼容读取 .claude/settings.json，"
                "Trae 会话里同一条钩子可能跑两次。"
                "如果只用 Trae，删掉 .claude/ 或用 --ide trae 显式指定"
            )
        hooks_text = generated["hooks/hooks.json"]
        for ad in detected:
            rc |= merge_for_ide(project, ad, hooks_text, dry)
    else:
        dirs = ", ".join(f"{a.dir_name}/" for a in ide_hosts.ADAPTERS)
        print(f"    [·] 未检测到任何 IDE 目录（{dirs}），跳过 IDE hooks 写入")

    if with_git_hook:
        rc |= git_hooks.install(project, dry)

    return rc


# IDE hooks 合并逻辑（merge_for_ide / _replace_paths_in_hooks / _clean_our_hooks）
# 已拆到 scripts/ide_hosts.py —— 适配器注册 + 配置合并自成一体，install.py
# 只管 detect + 循环调用。


# install_git_hook 已拆到 scripts/git_hooks.py：钩子合并（探测宿主钩子 +
# 链式接力 + 卸载还原）自成一体，install.py 只管调用。


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="安装套件到本机或某个项目")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="只处理指定卡片")
    ap.add_argument("--target", default=str(DEFAULT_SKILLS_TARGET), help="技能安装目录")
    ap.add_argument("--project", help="额外把运行时装到这个项目，并注入 hooks 配置")
    ap.add_argument(
        "--link", action="store_true", help="链接安装（Windows 用 junction）"
    )
    ap.add_argument("--force", action="store_true", help="覆盖非本套件安装的同名目录")
    ap.add_argument(
        "--with-git-hook",
        action="store_true",
        help="(默认已启用) 兼容保留：给项目装 git 钩子（宿主钩子链式接力，不覆盖）",
    )
    ap.add_argument(
        "--no-git-hook",
        action="store_true",
        help="--project 模式下跳过 git 提交层钩子（默认两层都装，见文件头说明）",
    )
    ap.add_argument(
        "--uninstall-git-hook",
        action="store_true",
        help="卸载本套件 git 钩子并还原原 core.hooksPath（需配 --project）",
    )
    ap.add_argument(
        "--ide",
        nargs="*",
        metavar="IDE",
        help=(
            "显式指定要写入哪些 IDE 的钩子配置，不传则自动检测项目目录。"
            f"可选值：{', '.join(a.id for a in ide_hosts.ADAPTERS)}；"
            "传 --ide 不加值则跳过 IDE 钩子写入"
        ),
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改任何东西")
    args = ap.parse_args()

    try:
        cards = build.load_all_cards(args.only)
    except build.CardError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1

    if not cards:
        print("[!] cards/ 下没有卡片，无事可做。")
        return 0

    names = [c["name"] for c in cards]
    rc = 0

    if not args.project:
        rc |= install_skills(
            Path(args.target).expanduser().resolve(),
            names,
            args.link,
            args.force,
            args.dry_run,
        )
        if not args.dry_run and rc == 0:
            print()
            print("新装的技能需要重开会话才会被加载。")
        return rc

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"[x] 项目目录不存在：{project}", file=sys.stderr)
        return 1

    if args.uninstall_git_hook:
        print(f"卸载 git 钩子：{project}\n")
        return git_hooks.uninstall(project, args.dry_run)

    print(f"安装目标：{project}\n")
    rc |= install_skills(
        Path(args.target).expanduser().resolve(),
        names,
        args.link,
        args.force,
        args.dry_run,
    )
    print()
    # 两层钩子默认都装：IDE 会话层（按目录检测）+ git 提交层（链式接力）。
    # --no-git-hook 显式退出提交层。
    with_git_hook = not args.no_git_hook
    rc |= install_runtime(
        project, cards, args.link, args.dry_run, with_git_hook, ide_filter=args.ide
    )

    if not args.dry_run and rc == 0:
        print()
        print("装完了。验证钩子是否真的生效：")
        print("  1. 在项目里改一个源文件（触发 Write/Edit）")
        print(f"  2. 或直接跑：python {PROJECT_HINT}")
        print("     （把 <tool-id> 换成 hooks/registry.json 里的任意一个 id）")
        print("  3. 若没有反应，检查 settings.json 是否被 WorkBuddy 读到 ——")
        print("     项目级配置位置在不同版本可能不同（.codebuddy / .workbuddy），")
        print("     见 docs/HOOKS-VERIFY.md 的实测方法")
    return rc


PROJECT_HINT = os.path.join(RUNTIME_DIRNAME, "hooks", "run.py") + " <tool-id>"


if __name__ == "__main__":
    sys.exit(main())
