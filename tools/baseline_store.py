#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/baseline_store.py —— 棘轮基线的存取

把「存量违规登记为债务、只阻断新增」的记账逻辑独立成模块，
被 check_import_direction.py 与 check_size_naming.py 共用。

约定：
    基线文件放在 <rules 所在目录>/../baselines/<rules 名>.baseline.json。
    放在 rules 目录之外的原因：install.py 重装时整体复制 rules/，
    基线是项目自己的债务清单，放外面才不会被冲掉。

    基线键由各工具自定义（import 用 文件:模块，size 用 文件:类型），
    共同点是**不含行号** —— 行号随编辑漂移，不能当键。

错误映射：BaselineError → 调用工具的退出码 2（配置错误，绝不能当通过处理）。
"""

from __future__ import annotations

import json
from pathlib import Path


class BaselineError(Exception):
    """基线文件的问题。对应工具退出码 2。"""


def default_baseline_path(rules_path: Path) -> Path:
    return rules_path.parent.parent / "baselines" / (rules_path.stem + ".baseline.json")


def load(path: Path) -> set:
    """读基线。文件不存在 = 没启用基线，返回空集合（不报错）。"""
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"基线文件不是合法 JSON：{path}\n    {exc}") from exc
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise BaselineError(f"基线文件缺少 entries 列表：{path}")
    return {str(e) for e in entries}


def write(path: Path, keys: "list[str]") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "description": "存量违规债务清单（棘轮基线）。命中者只提示不阻断；修掉后重跑 --update-baseline 收紧。",
        "entries": sorted(set(keys)),
    }
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def open_baseline(rules_path: Path, explicit: "str | None", update: bool) -> "tuple[Path, set]":
    """定位并加载基线，返回 (路径, 已登记键集合)。

    显式指定 --baseline 但文件不存在且不是 update 模式 → 抛 BaselineError：
    错路径静默当空基线，会让「新配置」伪装成「零债务」。
    """
    path = Path(explicit) if explicit else default_baseline_path(rules_path)
    if explicit and not path.is_file() and not update:
        raise BaselineError(
            f"显式指定的基线文件不存在：{path}\n    先跑 --update-baseline 生成，或检查路径"
        )
    return path, load(path)
