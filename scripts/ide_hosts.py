#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/ide_hosts.py —— 多 IDE 钩子适配器

WorkBuddy 桌面端启动时传了 --setting-sources=user，项目级 settings 整体被屏蔽
（见 docs/HOOKS-HOST-GAP.md）。其他 IDE（Trae / Claude Code / Codex / Qoder）
的钩子加载链路不一样，读的是各自的项目目录。所以 install.py 在装运行时时，
应该检测项目根下存在哪些 IDE 目录，把 hooks 配置分别写进去 —— 适配多 IDE。

支持的 5 个 IDE 适配器：

  适配器 id   项目目录     配置文件              placement  备注
  ───────────────────────────────────────────────────────────────────────
  codebuddy  .codebuddy/   settings.json         embed      CodeBuddy / WorkBuddy
  trae       .trae/        hooks.json             top + v1   Trae 独立文件，顶层加 version:1
  claude     .claude/      settings.json         embed      Claude Code（Trae 也兼容读这份）
  codex      .codex/       hooks.json             top        Codex (OpenAI) 独立文件，无 version
  qoder      .qoder/       settings.json         embed      Qoder（阿里）

各 IDE 的钩子配置 schema 几乎一致（都是 hooks.<Event>.[].matcher + hooks[].command），
差异只在两点：

  ① 独立文件 vs embed settings
     Trae / Codex 独立（顶层就是 hooks 对象）
     CodeBuddy / Claude / Qoder 嵌在 settings 里作为 hooks 子 key
  ② 顶层 version 字段
     Trae 需要 version: 1，Codex 不要，其余不要

用法：
    from ide_hosts import IdeAdapter, ADAPTERS, detect, merge_for_ide

    # 自动检测项目里有哪些 IDE 目录
    detected = detect(project_root)
    # detected -> [IdeAdapter(...), IdeAdapter(...)]

    # install.py 循环 detected，逐个调用 merge_for_ide(adapter, project, hooks_json_text)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ────────────────────────── IDE 适配器定义 ──────────────────────────


@dataclass
class IdeAdapter:
    """一个 IDE 钩子配置的写入规则。"""

    # 内部标识（用于 --ide 参数）
    id: str
    # 显示名（日志输出用）
    display: str
    # 项目根下的目录名（存在性检测）
    dir_name: str
    # 目录内的配置文件相对路径
    config_rel: str
    # hooks 在最终配置里的放置方式：
    #   "top"   → 顶层直接就是 {"hooks": {...}}（Trae 独立 hooks.json）
    #   "embed" → 嵌进 settings.json 作为一个子 key（CodeBuddy / Claude）
    placement: str = "embed"
    # 顶层额外 key（如 Trae 的 version: 1）
    top_extra: Optional[dict] = None
    # 是否剥离顶层 description 字段（Trae 独立配置不需要）
    strip_top_description: bool = False

    def config_path(self, project: Path) -> Path:
        return project / self.dir_name / self.config_rel


# 注册表 —— install.py 从这里拿完整列表
# 顺序：CodeBuddy 先（最常用），然后 Trae，Claude，Codex，Qoder
#
# TODO / 暂不支持：
#   .vscode/    → 通用编辑器本身没有 AI hooks 机制，跳过
#   .cline/     → Cline 的 hooks 是 .cline/hooks/ 下的可执行脚本
#                 （按事件名命名，如 PostToolUse 无扩展名），
#                 不是 JSON 配置，需要单独的适配器（hooks 直接复制脚本文件）
ADAPTERS: list[IdeAdapter] = [
    IdeAdapter(
        id="codebuddy",
        display="CodeBuddy / WorkBuddy",
        dir_name=".codebuddy",
        config_rel="settings.json",
        placement="embed",
    ),
    IdeAdapter(
        id="trae",
        display="Trae",
        dir_name=".trae",
        config_rel="hooks.json",
        placement="top",
        top_extra={"version": 1},
        strip_top_description=True,
    ),
    IdeAdapter(
        id="claude",
        display="Claude Code",
        dir_name=".claude",
        config_rel="settings.json",
        placement="embed",
    ),
    IdeAdapter(
        id="codex",
        display="Codex (OpenAI)",
        dir_name=".codex",
        config_rel="hooks.json",
        placement="top",
        # Codex 独立 hooks.json，顶层直接 {"hooks": {...}}，不要 version/description
        strip_top_description=True,
    ),
    IdeAdapter(
        id="qoder",
        display="Qoder (阿里)",
        dir_name=".qoder",
        config_rel="settings.json",
        placement="embed",
    ),
]


# ────────────────────────── 检测 ──────────────────────────


def detect(project: Path, ide_filter: Optional[list[str]] = None) -> list[IdeAdapter]:
    """检测项目根下存在哪些 IDE 目录，返回对应的适配器列表。

    Args:
        project: 项目根路径
        ide_filter:
            None      → 自动检测：只返回目录实际存在的适配器
            []        → 显式跳过（返回空）
            [ids...]  → 显式指定：返回匹配的适配器（不管目录存不存在，
                        由调用方决定是否创建）
    """
    if ide_filter is not None and len(ide_filter) == 0:
        return []
    result: list[IdeAdapter] = []
    for ad in ADAPTERS:
        if ide_filter is not None and ad.id not in ide_filter:
            continue
        # 显式指定时跳过目录存在性检测，让调用方决定是否创建
        if ide_filter is not None or (project / ad.dir_name).is_dir():
            result.append(ad)
    return result


def get_by_id(ide_id: str) -> Optional[IdeAdapter]:
    """按 id 找适配器，没找到返回 None。"""
    for ad in ADAPTERS:
        if ad.id == ide_id:
            return ad
    return None


# ────────────────────────── 构建 hooks 配置内容 ──────────────────────────


def build_config(adapter: IdeAdapter, hooks_data: dict) -> dict:
    """按适配器规则，把 build.py 生成的 hooks dict 组装成最终配置文件内容。

    Args:
        adapter: 目标 IDE 适配器
        hooks_data: build.render_hooks_json() 产出的 dict，
                    形如 {"description": "...", "hooks": {Event: [...]}}

    Returns:
        可以直接 json.dump 到目标配置文件的 dict
    """
    hooks = dict(hooks_data.get("hooks") or {})

    if adapter.placement == "top":
        # Trae：顶层就是 version + hooks
        result: dict = {}
        if adapter.top_extra:
            result.update(adapter.top_extra)
        if not adapter.strip_top_description and hooks_data.get("description"):
            result["description"] = hooks_data["description"]
        result["hooks"] = hooks
        return result

    # embed 模式（CodeBuddy / Claude / Qoder）：只返回 hooks 子块
    # 完整的 settings.json 由 install.py 的 merge_for_ide 处理
    return hooks


# ────────────────────────── 配置合并 ──────────────────────────


def _replace_paths_in_hooks(ours: dict, abs_project: str) -> dict:
    """把 hooks dict 里所有 command 的占位符换成路径 + 解释器绝对路径。

    build.py 生成时用 ${CODEBUDDY_PROJECT_DIR} 作占位符，这里统一替换。
    同时把裸 python 换成 sys.executable 绝对路径（PATH 不完整时会挂）。

    项目路径含非 ASCII 字符时改用相对路径 "."：
        Windows 宿主（实测 Trae CN）会把钩子命令交给 PowerShell 执行，
        命令里的非 ASCII 字符按系统 ANSI 代码页解析成乱码，引号配对失败，
        整批钩子全部 exit 1 —— 表现为「装好了，但一次都没真正检查过」。
        宿主的钩子 cwd 就是项目根，换成相对路径可彻底绕开编码问题。

    路径不含空格时连双引号也去掉：
        同一套 PowerShell 执行层还会把命令里的双引号吞坏 / 错配，
        即使路径全是 ASCII，仍报「意外的标记 "./.codingskills/hooks/run.py"」。
        裸路径（无空格）PowerShell 能正确解析，实测可跑通并真实命中违规。
    """
    python_exe = sys.executable.replace("\\", "/")
    base = "." if any(ord(ch) > 127 for ch in abs_project) else abs_project
    unquoted = " " not in python_exe and " " not in base
    updated: dict = {}
    for event, groups in ours.items():
        new_groups = []
        for group in groups:
            new_group = dict(group)
            new_group["hooks"] = []
            for h in group.get("hooks", []):
                cmd = str(h["command"]).replace("${CODEBUDDY_PROJECT_DIR}", base)
                if cmd.startswith("python "):
                    cmd = f'"{python_exe}" ' + cmd[len("python ") :]
                if unquoted:
                    cmd = cmd.replace('"', "")
                new_group["hooks"].append(dict(h, command=cmd))
            new_groups.append(new_group)
        updated[event] = new_groups
    return updated


def _clean_our_hooks(existing_hooks: dict) -> dict:
    """从已有的 hooks 里摘掉本套件此前注入的条目（command 含 run.py 的）。"""
    cleaned: dict = {}
    for event, groups in existing_hooks.items():
        if not isinstance(groups, list):
            continue
        group_list = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list):
                inner = [h for h in inner if "run.py" not in str(h.get("command", ""))]
                if not inner:
                    continue
                group = dict(group, hooks=inner)
            group_list.append(group)
        if group_list:
            cleaned[event] = group_list
    return cleaned


def merge_for_ide(
    project: Path,
    adapter: IdeAdapter,
    hooks_json_text: str,
    dry: bool,
) -> int:
    """按 IDE 适配器规则，把生成的 hooks 写入对应的配置文件。

    两个 placement 模式：
        "embed" → 嵌进 settings.json 作为 hooks 子 key（CodeBuddy / Claude / Qoder）
                  只替换本套件注入的条目，别人的配置原样保留
        "top"   → 独立的 hooks.json（Trae / Codex），顶层就是 version + hooks
                  重装时同样清理旧 run.py 条目
    """
    config_path = adapter.config_path(project)
    generated = json.loads(hooks_json_text)
    ours_raw = generated.get("hooks", {})
    abs_project = str(project).replace("\\", "/")
    ours = _replace_paths_in_hooks(ours_raw, abs_project)
    group_count = sum(len(v) for v in ours.values())

    if dry:
        print(
            f"    [~] [{adapter.display}] hooks → {config_path}"
            f"（{group_count} 组，placement={adapter.placement}）"
        )
        return 0

    if adapter.placement == "top":
        # Trae / Codex 独立 hooks.json
        data: dict = {}
        if config_path.is_file():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                print(f"    [x] {config_path} 不是合法 JSON，未改动它：{exc}")
                return 1
        existing_hooks = _clean_our_hooks(data.get("hooks") or {})
        existing_hooks.update(ours)
        data = build_config(adapter, generated)
        if not adapter.strip_top_description and generated.get("description"):
            data["description"] = generated["description"]
        data["hooks"] = existing_hooks
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"    [+] [{adapter.display}] hooks → {config_path}")
        return 0

    # embed 模式：hooks 嵌在 settings.json 里
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            print(f"    [x] {config_path} 不是合法 JSON，未改动它：{exc}")
            return 1
    else:
        data = {}

    existing = data.get("hooks") or {}
    if not isinstance(existing, dict):
        print(f"    [x] {config_path} 里的 hooks 不是对象，未改动它")
        return 1

    existing = _clean_our_hooks(existing)
    for event, groups in ours.items():
        existing.setdefault(event, []).extend(groups)

    data["hooks"] = existing
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"    [+] [{adapter.display}] hooks → {config_path}")
    return 0
