#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py —— 把套件装到本机 / 装到某个项目

两个装法，用途不同：

    ① 装技能（默认）
       技能 → ~/.workbuddy/skills/<name>/
       让 WorkBuddy 能加载这些技能。用户级，装一次全局可用。

    ② 装运行时（--project <路径>）
       运行时 → <项目>/.cardsmith/
         tools/  rules/  hooks/  scripts/
       并把 hooks 配置**合并**进 <项目>/.codebuddy/settings.json
       让这个项目里的编辑动作能触发卡片声明的检查。

    默认不碰项目的 git 配置。要同时装 git 钩子加 --with-git-hook：
    宿主钩子不会被覆盖 —— 安装时探测后链式接力，卸载用 --uninstall-git-hook
    （机制见 scripts/git_hooks.py）。

用法：
    python scripts/install.py --dry-run                   # 先看会做什么
    python scripts/install.py                             # 装全部技能到用户级
    python scripts/install.py --only format-lint-guard
    python scripts/install.py --link                      # 链接安装，改仓库即时生效
    python scripts/install.py --project /path/to/your-project
    python scripts/install.py --project <路径> --with-git-hook

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

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_TARGET = Path.home() / ".workbuddy" / "skills"
RUNTIME_DIRNAME = ".cardsmith"
SETTINGS_REL = Path(".codebuddy") / "settings.json"
STATE_FILE = ".cardsmith-state.json"

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
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(src)], capture_output=True)
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip() or proc.stdout.decode(errors="replace").strip()
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
        json.dump({"installed": sorted(names), "source": str(REPO_ROOT)}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# ────────────────────────── ① 装技能 ──────────────────────────


def install_skills(target: Path, names: "list[str]", use_link: bool, force: bool, dry: bool) -> int:
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


def install_runtime(project: Path, cards: "list[dict]", use_link: bool, dry: bool, with_git_hook: bool) -> int:
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
        # README 落为运行时根的 README.md —— 换机器克隆项目后，.cardsmith/ 不再是黑盒
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
        actions.append(f"[+] runtime-state.json（套件版本 {state['suite_version'] or '未知'}）")

    for a in actions:
        print(f"    {a}")

    # 合并 hooks 配置进项目的 settings.json
    rc = merge_settings(project, generated["hooks/hooks.json"], dry)

    if with_git_hook:
        rc |= git_hooks.install(project, dry)

    return rc


def merge_settings(project: Path, hooks_json_text: str, dry: bool) -> int:
    """把生成的 hooks 合并进项目的 settings.json。

    只替换本套件的条目（命令里含 run.py 的），别人加的钩子原样保留。
    settings.json 由其它工具管理，整文件覆盖会毁掉用户的其它配置。
    """
    settings = project / SETTINGS_REL
    generated = json.loads(hooks_json_text)
    ours = generated.get("hooks", {})
    # 生成时写的是 ${CODEBUDDY_PROJECT_DIR}/<prefix>hooks/run.py，
    # 这里把它还原成**项目根**的绝对路径（prefix 已经在字符串里了，别再拼一次）
    abs_project = str(project).replace("\\", "/")
    if dry:
        print(f"    [~] 合并 hooks 到 {settings}（{sum(len(v) for v in ours.values())} 组）")
        return 0

    data: dict = {}
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"    [x] {settings} 不是合法 JSON，未改动它：{exc}")
            return 1

    existing = data.get("hooks") or {}
    if not isinstance(existing, dict):
        print(f"    [x] {settings} 里的 hooks 不是对象，未改动它")
        return 1

    # 先摘掉本套件此前注入的条目
    for event in list(existing.keys()):
        groups = existing[event]
        if not isinstance(groups, list):
            continue
        cleaned = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list):
                inner = [h for h in inner if "run.py" not in str(h.get("command", ""))]
                if not inner:
                    continue
                group = dict(group, hooks=inner)
            cleaned.append(group)
        if cleaned:
            existing[event] = cleaned
        else:
            existing.pop(event, None)

    # 再注入新的，同时把占位符换成本机真实路径 ——
    # 钩子命令用绝对路径最稳，文档也这么建议；裸 python 在 PATH 不完整时会挂
    python_exe = sys.executable.replace("\\", "/")
    for event, groups in ours.items():
        target_groups = existing.setdefault(event, [])
        for group in groups:
            new_group = dict(group)
            new_group["hooks"] = []
            for h in group.get("hooks", []):
                cmd = str(h["command"]).replace("${CODEBUDDY_PROJECT_DIR}", abs_project)
                if cmd.startswith("python "):
                    cmd = f'"{python_exe}" ' + cmd[len("python ") :]
                new_group["hooks"].append(dict(h, command=cmd))
            target_groups.append(new_group)

    data["hooks"] = existing
    settings.parent.mkdir(parents=True, exist_ok=True)
    with settings.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"    [+] hooks 已合并进 {settings}")
    return 0


# install_git_hook 已拆到 scripts/git_hooks.py：钩子合并（探测宿主钩子 +
# 链式接力 + 卸载还原）自成一体，install.py 只管调用。


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="安装套件到本机或某个项目")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="只处理指定卡片")
    ap.add_argument("--target", default=str(DEFAULT_SKILLS_TARGET), help="技能安装目录")
    ap.add_argument("--project", help="额外把运行时装到这个项目，并注入 hooks 配置")
    ap.add_argument("--link", action="store_true", help="链接安装（Windows 用 junction）")
    ap.add_argument("--force", action="store_true", help="覆盖非本套件安装的同名目录")
    ap.add_argument("--with-git-hook", action="store_true", help="同时给项目装 git 钩子（宿主钩子链式接力，不覆盖）")
    ap.add_argument("--uninstall-git-hook", action="store_true", help="卸载本套件 git 钩子并还原原 core.hooksPath（需配 --project）")
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
        rc |= install_skills(Path(args.target).expanduser().resolve(), names, args.link, args.force, args.dry_run)
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
    rc |= install_skills(Path(args.target).expanduser().resolve(), names, args.link, args.force, args.dry_run)
    print()
    rc |= install_runtime(project, cards, args.link, args.dry_run, args.with_git_hook)

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
