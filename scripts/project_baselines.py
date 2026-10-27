#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project_baselines.py —— 安装时给项目预置空的棘轮基线

从 `install.py` 拆出来的原因很实际：`ruff format` 展开 install.py 里原有的手工
长行后，它撞上了套件自己要求的 `.py` 500 行上限。这块逻辑与安装流程的其余部分
耦合很弱（输入是卡片声明，输出是 `baselines/` 下的文件），拆开正好各归各位。

## 为什么安装时就要建基线

`baseline_store.apply_ratchet()` 对「文件不存在」走 `first_build` 分支，**绕过
棘轮闸门** —— 那条路径会把当时扫到的违规全部静默登记成债务，一次命令就能把门禁
漂白。空文件（`entries: []`）存在时，同一条 `--update-baseline` 会被闸门拒绝
「本次会新增 N 条债务」，必须 `--accept-debt --debt-reason` 才算认账。卡片里本来
就写着「首次接入也要显式认账」，是 `first_build` 分支让实现和文档对不上；预置
空基线把实现拉回文档。

## 边界

必须是合法 JSON。0 字节文件会被 `load()` 判成坏文件，检查工具退出码 2（配置
错误），比没有基线更糟 —— 所以写入一律走 `apply_ratchet()`，不手写 JSON 模板。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import baseline_store  # noqa: E402


def rule_stems(cards: "list[dict]") -> "list[str]":
    """从卡片声明里取「实现了棘轮」的规则名。

    判定依据是 `tools[].baseline == true` —— 与 runtime_readme 生成基线清单用的是
    同一个字段。卡片声明了哪些，这里就建哪些，安装流程不另立一套判断，否则
    「README 列 8 个、实际跑出 9 个」这种漂移迟早出现（stale-import-guard 就是这么漏的）。
    """
    stems: "set[str]" = set()
    for card in cards:
        for tool in card.get("tools") or []:
            if not tool.get("baseline"):
                continue
            for arg in tool.get("args") or []:
                if not isinstance(arg, str) or not arg.endswith(".json"):
                    continue
                if "rules/" not in arg.replace("\\", "/"):
                    continue
                stems.add(Path(arg).stem)
    return sorted(stems)


def init(runtime: Path, cards: "list[dict]", dry: bool) -> "list[str]":
    """给声明了棘轮的规则补空基线；项目里已有的文件一律不碰。

    已有的不覆盖是硬要求：基线是项目的债务清单，重装时覆盖它等于替项目认账或销账。
    """
    stems = rule_stems(cards)
    if not stems:
        return []

    baselines_dir = runtime / "baselines"
    created = kept = 0
    broken: "list[str]" = []
    for stem in stems:
        dest = baselines_dir / f"{stem}.baseline.json"
        if dest.is_file():
            try:
                baseline_store.load(dest)
                kept += 1
            except baseline_store.BaselineError:
                broken.append(dest.name)
            continue
        if not dry:
            # 空列表 = 登记零条债务。首建分支会写 initial 段，留下「何时、多少条」的痕迹。
            baseline_store.apply_ratchet(dest, [], set())
        created += 1

    actions: "list[str]" = []
    if created:
        verb = "将新建" if dry else "新建"
        actions.append(
            f"[+] baselines/（{verb} {created} 个空基线；"
            "首建豁免就此关闭，新增债务必须 --accept-debt）"
        )
    if kept:
        actions.append(f"[=] baselines/（保留项目已有 {kept} 个，不覆盖）")
    if broken:
        actions.append(
            f"[!] baselines/ 有 {len(broken)} 个文件无法解析，未改动："
            f"{', '.join(broken)} —— 坏基线会让检查以退出码 2 失败，先修它"
        )
    return actions
