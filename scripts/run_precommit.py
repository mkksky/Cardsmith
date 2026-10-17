#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/run_precommit.py —— git 钩子的执行体（pre-commit / commit-msg / pre-push）

被 .githooks/<event> 调用。做两件事：

    1. 套件自身对账（scripts/check.py）—— 卡片与生成物不许漂移
    2. 按 hooks/<event>-plan.json 跑卡片声明为该事件的工具

只有 blocking 的工具失败才会中断提交；非阻断的只打印提醒。

为什么用 core.hooksPath 而不是往 .git/hooks/ 里塞文件：
    .git/ 不入版本控制，每台机器都要重装一次，且钩子内容无法被 review。
    core.hooksPath 指向仓库内的 .githooks/，克隆下来配一条命令就生效。

退出码：0 = 放行，1 = 阻断提交。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOL_TIMEOUT = 180


def find_root() -> Path:
    """运行时根：scripts/ 的上一级。装到项目后是 <项目>/.codingskills。"""
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """被检查文件所属的项目根。与运行时根不是一个东西，见 hooks/run.py 的同名函数。"""
    runtime = find_root()
    if runtime.name == ".codingskills":
        return runtime.parent
    return runtime


def run(cmd: "list[str]", root: Path):
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, timeout=TOOL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{TOOL_TIMEOUT}s）"
    except OSError as exc:
        return 127, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace").strip(),
        proc.stderr.decode("utf-8", errors="replace").strip(),
    )


KNOWN_EVENTS = ("pre-commit", "commit-msg", "pre-push")


def main() -> int:
    # 调用方式（.githooks/<event> 传进来）：
    #   run_precommit.py                        → 默认 pre-commit（向后兼容老钩子脚本）
    #   run_precommit.py pre-commit             → 显式事件
    #   run_precommit.py commit-msg <msgfile>   → 把 commit message 文件路径转给工具
    #   run_precommit.py pre-push               → 推送前检查
    argv = sys.argv[1:]
    event = "pre-commit"
    event_args: "list[str]" = []
    if argv:
        event = argv[0]
        event_args = argv[1:]
    if event not in KNOWN_EVENTS:
        print(
            f"[x] 未知 git 钩子事件：{event!r}（可选：{', '.join(KNOWN_EVENTS)}）",
            file=sys.stderr,
        )
        return 1

    root = find_root()
    proj = project_root()
    blocking_failures: "list[tuple[str, str]]" = []
    warnings: "list[tuple[str, str]]" = []

    # ── 1. 套件自身对账 ──
    check_py = root / "scripts" / "check.py"
    if check_py.is_file():
        rc, out, err = run([sys.executable, str(check_py), "--quiet"], root)
        if rc == 1:
            blocking_failures.append(("卡片与生成物对账", out or err))
        elif rc not in (0, 1):
            blocking_failures.append(
                ("卡片与生成物对账", f"执行异常（退出码 {rc}）\n{err}")
            )

    # ── 2. 卡片声明为该 git 事件的工具 ──
    # 计划文件名约定与 build.py 一致：hooks/<event>-plan.json
    plan_file = root / "hooks" / f"{event}-plan.json"
    extra_tool_args: "list[str]" = []
    if event == "commit-msg":
        # git 把 message 文件路径作为 $1 传给钩子，这里转成工具参数。
        # 必须转成**绝对路径**：git 传进来的是相对仓库根的 .git/COMMIT_EDITMSG，
        # 而下面 run() 把工具的工作目录设成了运行时根（<项目>/.codingskills），
        # 相对路径在那里解析不到 → 工具报退出码 2 → 每次提交都被阻断。
        # （2026-09-25 实测：git 传 $1=[.git/COMMIT_EDITMSG]，钩子 cwd=仓库根）
        if not event_args:
            print(
                "[x] commit-msg 需要消息文件路径参数（git 会自动传 $1）",
                file=sys.stderr,
            )
            return 1
        msg_file = Path(event_args[0])
        if not msg_file.is_absolute():
            msg_file = proj / msg_file
        extra_tool_args = ["--msg-file", str(msg_file)]
    if plan_file.is_file():
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[x] {event}-plan.json 不是合法 JSON：{exc}", file=sys.stderr)
            return 1

        for tool in plan.get("tools", []):
            tid = tool.get("id", "?")
            script = root / str(tool.get("script", ""))
            if not script.is_file():
                # 脚本缺失是配置问题，必须暴露（check.py 也会报，这里是第二道）
                blocking_failures.append((tid, f"脚本不存在：{tool.get('script')}"))
                continue

            cmd = [
                sys.executable,
                str(script),
                *[str(a) for a in (tool.get("args") or [])],
                *extra_tool_args,
                "--root",
                str(proj),
                "--quiet",
            ]
            rc, out, err = run(cmd, root)

            if rc == 0:
                # --quiet 约定：退出码 0 但有输出 = 非阻断提醒（secrets 的 soft 层）
                if out:
                    warnings.append((tid, out))
                continue
            if rc == 2:
                # 配置错误：比违规更严重，因为它意味着检查根本没生效
                blocking_failures.append((tid, f"配置错误（退出码 2）：\n{err or out}"))
                continue
            message = out or err or f"{tid} 报告了问题"
            if tool.get("blocking"):
                blocking_failures.append((tid, message))
            else:
                warnings.append((tid, message))

    # ── 3. 输出 ──
    if warnings:
        print("─" * 60)
        print("以下检查报告了问题，但未配置为阻断（提交继续）：")
        for tid, msg in warnings:
            print(f"\n[{tid}]\n{msg}")
        print("─" * 60)

    if blocking_failures:
        print("─" * 60)
        print("提交被中断。以下阻断项未通过：\n")
        for tid, msg in blocking_failures:
            print(f"[x] {tid}")
            for line in msg.splitlines():
                print(f"    {line}")
            print()
        print("修完再提交。确实要跳过请用 git commit --no-verify，")
        print("但先想清楚：跳过的是这些检查，还是整个门禁的可信度。")
        print("─" * 60)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
