#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hooks/run.py —— 钩子工具分发器

hooks.json 里每条钩子都长这样：

    python "<...>/hooks/run.py" <tool-id>

本脚本负责：从 stdin 读 WorkBuddy 传来的钩子输入 → 在 registry.json 里找到
对应脚本 → 决定检查哪些文件 → 执行 → 把结果按钩子协议回吐。

为什么不让 hooks.json 直接指向具体脚本：
    hooks.json 是生成物，命令写死会导致「换个项目路径就失效」。
    多一层分发器后，路径只在本脚本里解析一次，且 --prefix 变化时只改 registry。

退出码语义（WorkBuddy 钩子协议）：
    0 = 放行，stdout 可作为 additionalContext 注入
    2 = 阻断，stderr 回填给模型（仅当卡片的 hook 标了 blocking: true）

设计取舍：**脚本缺失不阻断，只警告**。
    钩子是实时提示层，check.py 与 pre-commit 才是阻断层。
    如果钩子因为「卡片声明了脚本但文件没同步过来」就把人卡住，会逼出 --no-verify 习惯。
    这类配置问题由 check.py 在提交前拦住更合适。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 运行时根：脚本与规则文件的基准目录。registry 里的 tools/x.py 就是相对它。
#   套件自身   → <仓库>/hooks/run.py     ⇒ 运行时根 = <仓库>
#   装到项目   → <项目>/.codingskills/hooks/run.py ⇒ 运行时根 = <项目>/.codingskills
RUNTIME_ROOT = HERE.parent
REGISTRY_FILE = HERE / "registry.json"
TOOL_TIMEOUT = 120

def project_root() -> Path:
    """被检查文件所属的项目根。

    与运行时根不是一个东西 —— 这点踩过坑：
    装到项目后，tools 在 <项目>/.codingskills/ 下，但被编辑的文件在 <项目>/ 下。
    两者混用会让文件路径无法转成相对路径，报错信息里全是不好读的绝对路径。
    """
    if RUNTIME_ROOT.name == ".codingskills":
        return RUNTIME_ROOT.parent
    return RUNTIME_ROOT


def _load_registry() -> dict:
    if not REGISTRY_FILE.is_file():
        return {}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_files(payload: dict) -> "list[str]":
    """从钩子输入里捞出被操作的文件，转成相对项目根的路径。"""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    root = project_root().resolve()
    out: list[str] = []
    for key in ("file_path", "filePath", "path", "notebook_path", "target_file"):
        value = tool_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        p = Path(value)
        try:
            out.append(p.resolve().relative_to(root).as_posix())
        except (ValueError, OSError):
            out.append(p.as_posix())
    return out


def check_runtime_version(root: "Path | None" = None) -> "str | None":
    """运行时与套件仓库的版本对账，落后 → 返回警告文本，否则 None。

    背景：运行时是安装时刻的快照。套件新增卡片后，旧运行时的 registry 里
    没有新工具，本脚本对未注册工具静默放行 —— 表现为「钩子没反应」
    （docs/HOOKS-VERIFY.md 实锤过的坑）。装运行时时 install.py 会把套件版本
    和源仓库路径写进 runtime-state.json，这里做一次**纯本地文件**对账，
    提前把「该重装了」告诉人。仓库不在这台机器上了就不误报。

    root 参数给自检用：默认用真实运行时根。
    """
    runtime_root = root if root is not None else RUNTIME_ROOT
    state_file = runtime_root / "runtime-state.json"
    if not state_file.is_file():
        return None  # 没有安装存档 → 本尊是套件仓库自己，不存在落后问题
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    src = str(state.get("source_repo") or "")
    mine = str(state.get("suite_version") or "")
    if not src or not mine:
        return None
    version_file = Path(src) / "VERSION"
    if not version_file.is_file():
        return None  # 套件仓库不在这台机器上了（拷到别的机器等）：不误报
    try:
        repo_ver = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not repo_ver or repo_ver == mine:
        return None
    return (
        f"[!] 运行时版本落后：运行时 {mine}，套件仓库 {repo_ver}。"
        f"旧运行时会静默放行新增的守卫。重装："
        f"python {Path(src) / 'scripts' / 'install.py'} --project <本项目路径>"
    )


def main() -> int:
    if "--selftest" in sys.argv:
        sys.path.insert(0, str(HERE))
        import run_selftest

        return run_selftest.run_selftest()

    warn = check_runtime_version()
    if warn:
        print(warn, file=sys.stderr)

    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not argv:
        return 0
    tool_id = argv[0]
    blocking = "--blocking" in sys.argv

    registry = _load_registry()
    entry = (registry.get("tools") or {}).get(tool_id)
    if entry is None:
        # 注册表里没有 → 卡片可能刚被删/改名，别卡住用户
        return 0
    if isinstance(entry, dict) and "command" in entry:
        # 直接命令形式：原样执行
        return _run_direct(entry, tool_id)

    script = RUNTIME_ROOT / str(entry.get("script", ""))
    if not script.is_file():
        print(
            f"[!] 钩子 {tool_id} 声明的脚本不存在：{entry.get('script')}\n"
            f"    跳过检查。请跑 python scripts/check.py 定位是哪张卡片的绑定断了。",
            file=sys.stderr,
        )
        return 0

    payload = _read_payload()
    # cwd 留在运行时根（--rules 这类相对路径要对得上），
    # 但要把项目根显式告诉工具 —— 被检查的文件是相对项目根的。
    cmd = [
        sys.executable,
        str(script),
        *[str(a) for a in (entry.get("args") or [])],
        "--root",
        str(project_root()),
        "--quiet",
    ]

    inputs = str(entry.get("inputs", "staged-files"))
    if inputs == "all-files":
        cmd.append("--all")
    elif inputs == "staged-files":
        files = _extract_files(payload)
        if files:
            cmd += ["--files", *files]
        # 拿不到文件时，留给脚本按 git 暂存自己取

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(RUNTIME_ROOT),
            capture_output=True,
            timeout=TOOL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"[!] 工具 {tool_id} 超时（{TOOL_TIMEOUT}s），已跳过", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"[!] 工具 {tool_id} 无法执行：{exc}", file=sys.stderr)
        return 0

    # Windows 下子进程写管道时 \n 会被翻译成 \r\n，回填上下文前统一归一
    stdout = proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    stderr = proc.stderr.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()

    if proc.returncode == 0:
        # --quiet 约定：退出码 0 但仍有输出 = 非阻断提醒（如 secrets 的 soft 层），
        # 照样回填上下文；成功时 --quiet 下无输出，不会注入噪音
        if stdout:
            event_name = str(payload.get("hook_event_name") or "PostToolUse")
            output = {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": stdout,
                },
            }
            print(json.dumps(output, ensure_ascii=False))
        return 0

    if proc.returncode == 2:
        # 配置错误：必须让人看见，但不阻断工作流
        print(f"[!] 工具 {tool_id} 报告配置问题：\n{stderr or stdout}", file=sys.stderr)
        return 0

    # 退出码 1 = 有违规
    message = stdout or stderr or f"{tool_id} 报告了问题"

    if blocking:
        print(message, file=sys.stderr)
        return 2

    # 非阻断：作为上下文提示注入
    # 事件名从输入回读，不能写死 PostToolUse —— 将来加 PreToolUse 非阻断钩子时，
    # 回填错误的 hookEventName 会让宿主忽略这条输出
    event_name = str(payload.get("hook_event_name") or "PostToolUse")
    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": f"[{tool_id}] {message}",
        },
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


def _run_direct(entry: dict, tool_id: str) -> int:
    """registry 里直接给了 command 的情况（给将来留的扩展口）。

    失败必须映射到退出码 2，而不是恒放行 —— 之前写成
    `return 0 if rc == 0 else 0`，等于这个入口永远不阻断（2026-09-24 修复）。
    """
    import shlex

    try:
        proc = subprocess.run(shlex.split(str(entry["command"])), cwd=str(RUNTIME_ROOT), timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[!] 钩子 {tool_id} 执行失败：{exc}", file=sys.stderr)
        return 2
    return 0 if proc.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
