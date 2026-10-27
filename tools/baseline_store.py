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
新增：RatchetError → 调用工具的退出码 1（本次操作被棘轮闸门拒绝）。
"""

from __future__ import annotations

import json
from pathlib import Path


class BaselineError(Exception):
    """基线文件的问题。对应工具退出码 2。"""


class RatchetError(Exception):
    """棘轮方向被违反：本次操作会新增债务。对应工具退出码 1。

    与 BaselineError 分开是刻意的：BaselineError 是「环境/配置坏了」，
    RatchetError 是「环境没问题，但你不该这么做」—— 前者重试或改配置，
    后者必须改代码或显式认账。混成一个会让门禁输出说错话。
    """


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
    """无闸门的裸写入。**只保留给自检与迁移脚本，不要在检查工具里调用。**

    留着它是因为它是不带任何方向判断的「原始写」，自检需要用它构造前置状态。
    但检查工具一旦开始调它，就等于给自己开了个绕过 apply_ratchet() 的后门 ——
    棘轮的价值全在于「没有第二条路能新增债务」。
    新写逻辑一律走 apply_ratchet()。
    """
    _dump(
        path,
        {
            "version": 2,
            "description": "存量违规债务清单（棘轮基线）。命中者只提示不阻断；修掉后重跑 --update-baseline 收紧。",
            "entries": sorted(set(keys)),
        },
    )


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


# ──────────────────── 棘轮闸门：只降不升 ────────────────────
#
# 为什么需要闸门（2026-10-17 补，借鉴 pine-of-glass / issue-orchestrator#6671）：
#
# 棘轮的最大死法不是没人跑 --update-baseline，而是**有人跑得太顺手**。
# 一条命令、一屏绿，diff 里看着像「顺手维护基线」，实际是把这次新增的违规
# 一起赦免了。等到发现时，债务已经涨了几个数量级，而基线文件里没有任何
# 痕迹说明是哪一次提交放进去的。
#
# 所以闸门的规则是：**默认只允许收紧**。
#   - 只减不增 → 直接放行（这是棘轮的正常用法）
#   - 有新增键 → 拒绝，并把新增的键逐条列出来，要求显式 --accept-debt + 理由
#   - 认账的记录写进基线文件的 accepted 段，让「谁、为什么、什么时候」留在版本历史里
#
# 首建基线是例外：文件本来就不存在，此时「新增」是登记存量，不是赦免新增。
# 若不放过这条，接入老项目的第一步就永远做不成。


def ratchet_plan(old: set, new: "iterable[str]") -> dict:
    """比对现有基线与待写入键，算出收紧/新增/保持三部分。纯函数，不碰磁盘。"""
    new_set = set(new)
    added = new_set - old
    return {
        "added": sorted(added),
        "removed": sorted(old - new_set),
        "kept": sorted(old & new_set),
        "old_count": len(old),
        "new_count": len(new_set),
    }


def apply_ratchet(
    path: Path,
    keys: "list[str]",
    old: set,
    accept_debt: bool = False,
    reason: str = "",
    scanned: "set[str] | None" = None,
) -> dict:
    """执行棘轮写入。默认拒绝新增键，返回 plan 字典描述发生了什么。

    首建基线（文件原本不存在）不受闸门约束 —— 此时 old 为空集，
    「全部是新增」是登记存量债务的正常语义，不是赦免。不放开这条，
    接老项目的第一步就永远做不成。首建同样会记 initial 段，留下痕迹。

    accept_debt=True 时允许新增，但理由必填 —— 没理由的认账无法 review，
    等于把问题换个地方存着。

    scanned = 本次扫描覆盖到的文件路径集合。传入后启用**范围守卫**
    （见 _find_unscanned_disappearances）；不传则跳过，用于兼容旧调用方。
    """
    first_build = not path.is_file()
    plan = ratchet_plan(old, keys)

    if first_build:
        plan["first_build"] = True
        stamp = _today()
        payload = {
            "version": 2,
            "description": "存量违规债务清单（棘轮基线）。命中者只提示不阻断；修掉后重跑 --update-baseline 收紧。",
            "entries": sorted(set(keys)),
            "initial": {
                "date": stamp,
                "count": len(set(keys)),
                "note": "首次登记的存量债务。此后棘轮只允许收紧；新增须 --accept-debt --debt-reason。",
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _dump(path, payload)
        return plan

    # ── 范围守卫：本次没扫到的文件，其存量债务不能被当成「已修复」销掉 ──
    # 这是一种比「新增违规」更隐蔽的基线损坏，且**早于本次改动就存在**：
    # 键里存的是 文件:类型，若某类文件（如 .wxss）压根没进 rules 的
    # file_lines.overrides / naming 键空间，--all 就永远扫不到它，
    # 一次 --update-baseline 就会把它的存量债务静默抹掉。
    # 实测（2026-10-17 某小程序项目）：detail.wxss 有 1902 行、远超 500 行上限，
    # 但基线里的那条在收紧时被删掉了 —— 风险从「新增违规」变成
    # 「超限文件不再被记为债务」，比原来更糟。
    unscanned = _find_unscanned_disappearances(old, keys, scanned)
    if unscanned:
        raise RatchetError(_format_unscanned_rejection(path, unscanned))

    if plan["added"] and not accept_debt:
        raise RatchetError(_format_ratchet_rejection(path, plan))

    if plan["added"] and not reason.strip():
        raise RatchetError(
            "--accept-debt 必须带理由（--debt-reason \"...\"）\n"
            "    没有理由的认账在 review 里等于没有记录，"
            "几个月后没人知道这几条债是怎么进来的。"
        )

    payload = {
        "version": 2,
        "description": "存量违规债务清单（棘轮基线）。命中者只提示不阻断；修掉后重跑 --update-baseline 收紧。",
        "entries": sorted(set(keys)),
    }

    # 认账记录：只在真的新增了键时写，且不覆盖历史 —— 每笔认账都要留下痕迹。
    # 刻意不取文件 mtime（会被 git checkout 改掉），用当天日期。
    if plan["added"]:
        prior = _load_accepted(path)
        prior.append(
            {
                "date": _today(),
                "reason": reason.strip(),
                "keys": plan["added"],
            }
        )
        payload["accepted"] = prior

    _dump(path, payload)
    return plan


def _find_unscanned_disappearances(old: set, new: "iterable[str]", scanned: "set[str] | None") -> list:
    """找出「本次没扫到、却被当成已修复而消失」的旧键。

    键格式是 文件:类型（最后一段是类型），所以文件部分 = 去掉最后一段。
    只在「该文件本次根本没进扫描范围」时报警 —— 文件扫到了但违规消失，
    那是真修好了，不该拦。scanned 为 None 时整体跳过（兼容旧调用方）。
    """
    if scanned is None:
        return []
    new_set = set(new)
    return [k for k in sorted(old - new_set) if _key_file(k) not in scanned]


def _key_file(key: str) -> str:
    """从 基线键 里取文件部分。键约定为 文件:类型，用 rsplit 切一次。"""
    return key.rsplit(":", 1)[0] if ":" in key else key


def _format_unscanned_rejection(path: Path, unscanned: list) -> str:
    """范围守卫的报错。必须说清「不是它被修好了，是没扫到」——
    这两者的区别就是全部：前者是好消息，后者是工具的盲区。"""
    files = sorted({_key_file(k) for k in unscanned})
    lines = [
        f"棘轮拒绝：本次扫描没覆盖到 {len(unscanned)} 条债务所属的文件，"
        f"不能把它们当成「已修复」销掉（基线 {path}）",
        "",
        f"  涉及 {len(files)} 个文件：",
    ]
    for f in files[:20]:
        lines.append(f"    ? {f}")
    if len(files) > 20:
        lines.append(f"    ...另有 {len(files) - 20} 个")
    lines += [
        "",
        "  它们不是被修好了，是**这次根本没扫到**。可能的原因：",
        "    1. 规则文件的 file_lines.overrides / naming 里没有这一类扩展名 →",
        "       该类型永远不进 --all 扫描（最常见，且表现为「检查全绿」）",
        "    2. skip_paths 把它们排除了",
        "    3. 这次没用 --all，只扫了暂存文件",
        "",
        "  先查原因，别直接 --accept-debt：认账只是让写入通过，",
        "  并不会让这些文件真的被检查。",
    ]
    return "\n".join(lines)


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_accepted(path: Path) -> list:
    """读历史认账记录。文件不存在或格式不对时返回空列表（不让坏记录挡住写入）。"""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    accepted = data.get("accepted")
    return accepted if isinstance(accepted, list) else []


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


def format_ratchet_report(path: Path, plan: dict) -> str:
    """写入成功后的报告。六个工具共用一份，避免各自措辞漂移。

    收紧是常态，必须报出具体少了哪些 —— 只报总数的话，
    「少了很多」和「少了一条但那一条很重要」看起来一模一样。
    """
    lines = [f"[+] 基线已更新：{path}"]
    if plan.get("first_build"):
        lines.append(f"    首次登记存量债务 {plan['new_count']} 条（首建不受棘轮闸门约束）。")
        lines.append("    此后棘轮只允许收紧；新增债务须 --accept-debt --debt-reason 显式认账。")
        return "\n".join(lines)

    if plan["removed"]:
        lines.append(f"    收紧：{plan['old_count']} → {plan['new_count']} 条，销掉 {len(plan['removed'])} 条：")
        for key in plan["removed"][:20]:
            lines.append(f"      - {key}")
        if len(plan["removed"]) > 20:
            lines.append(f"      ...另有 {len(plan['removed']) - 20} 条")
    else:
        lines.append(f"    维持 {plan['new_count']} 条不变。")

    if plan["added"]:
        lines.append(f"    [!] 已认账新增 {len(plan['added'])} 条（理由见基线文件 accepted 段）：")
        for key in plan["added"][:20]:
            lines.append(f"      + {key}")
        if len(plan["added"]) > 20:
            lines.append(f"      ...另有 {len(plan['added']) - 20} 条")
    return "\n".join(lines)


def _format_ratchet_rejection(path: Path, plan: dict) -> str:
    lines = [
        f"棘轮拒绝：本次会新增 {len(plan['added'])} 条债务（基线 {path}）",
        "",
        "  基线棘轮只允许收紧。以下键是本次新增的：",
    ]
    for key in plan["added"]:
        lines.append(f"    + {key}")
    lines += [
        "",
        "  这不是配置问题，重试没有意义。三条路，选一条：",
        "    1. 修代码 —— 新增违规本来就该修",
        f"    2. 如果确实要认下这笔债，显式写明理由：",
        f"       --accept-debt --debt-reason \"为什么现在不能修\"",
        "       认账会连同理由记进基线文件的 accepted 段，进版本历史",
        f"    3. 如果这些违规是**误报**（工具判断错了），修规则或加豁免，"
        f"别用认账掩盖",
        "",
        "  参考：pine-of-glass 的做法是 --update-baseline 「只允许删除签名或降低预算，"
        "拒绝新发现」，",
        "  破例必须手改并在 PR 里写明理由 —— 本工具把这个约定做成了机器闸门。",
    ]
    return "\n".join(lines)
