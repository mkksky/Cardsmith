#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py —— 三方对账

对账三方：

    ① 卡片      cards/<name>.md      ← 唯一真源，手写
    ② 生成物    skills/ hooks/       ← build.py 产出，不该手改
    ③ 磁盘实际  tools/ rules/        ← 卡片声明引用的脚本与规则文件

任一方跟另外两方对不上就报错。这就是「程序上的完全对应」——
不是靠人记得同步，是靠每次提交前跑一遍对账。

用法：
    python scripts/check.py                 # 全量对账
    python scripts/check.py --only <name>   # 只查某张卡片
    python scripts/check.py --strict        # 把 WARN 也当失败
    python scripts/check.py --quiet         # 只输出有问题的

退出码：0 = 无 ERROR，1 = 有 ERROR（或 --strict 下有 WARN）。

能查出
    - 卡片字段非法 / 命名违规 / description 不合格
    - 卡片声明的 tool 脚本不存在、rules 源文件不存在
    - tool id 跨卡片重复（钩子将无法区分该跑哪个）
    - 生成物与卡片不一致（卡片改了没重跑 build，或生成物被手改）
    - 生成物缺失
    - skills/ 下有目录但 cards/ 里没有对应卡片（孤儿）
    - registry 与卡片声明不符（反向漂移）

查不出
    - description 的触发词选得准不准
    - 规则阈值定得合不合理
    这两条只能人看。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CARDS_DIR = REPO_ROOT / "cards"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# 与 docs/CONVENTIONS.md「单段，长度 60 ~ 250 字」对齐；上限 400 是文档写明的 warn 线
DESC_MIN = 60
DESC_MAX = 400
PLACEHOLDER_MARKS = ("TODO 必填", "TODO:")


class Report:
    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.errors: "list[str]" = []
        self.warns: "list[str]" = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"  {self.scope}"]
        for e in self.errors:
            lines.append(f"    [x] {e}")
        for w in self.warns:
            lines.append(f"    [!] {w}")
        if self.ok and not self.warns:
            lines.append("    [v] 通过")
        return "\n".join(lines)


# ────────────────────────── 卡片侧 ──────────────────────────


def check_card(card: dict) -> Report:
    rep = Report(f"cards/{card['_file']}")
    name = card["name"]

    if not NAME_RE.match(name):
        rep.error(f"名称不合法：{name!r}（只允许小写字母/数字/连字符）")
    elif not 2 <= len(name.split("-")) <= 4:
        rep.warn(f"名称有 {len(name.split('-'))} 段，规范建议 2~4 段")

    desc = str(card["description"])
    if any(mark in desc for mark in PLACEHOLDER_MARKS):
        rep.error("description 仍是模板占位内容，未填写")
    else:
        flat = desc.strip()
        if len(flat) < DESC_MIN:
            rep.warn(
                f"description 仅 {len(flat)} 字，可能触发词不足（建议 {DESC_MIN}+）"
            )
        elif len(flat) > DESC_MAX:
            rep.warn(f"description 达 {len(flat)} 字，偏长（建议 <{DESC_MAX}）")

    if int(card.get("version", 0)) < 1:
        rep.error("version 必须 >= 1")

    try:
        build.validate_for_build(card)
    except build.CardError as exc:
        rep.error(str(exc))
        return rep

    # 卡片声明的脚本必须实际存在 —— 这是「程序上完全对应」的第一层
    for tool in card["tools"]:
        script = str(tool["script"])
        if not (REPO_ROOT / script).is_file():
            rep.error(
                f"tool {tool['id']!r} 声明的脚本不存在：{script}\n"
                f"        卡片说要有，磁盘上没有 —— 要么补文件，要么从卡片里删掉这条声明"
            )
        if not script.startswith("tools/"):
            rep.warn(
                f"tool {tool['id']!r} 的 script 不在 tools/ 下：{script}（规范要求放 tools/）"
            )

    # 规则源文件同理
    for rule in card["rules"]:
        src = rule.get("source")
        if src and not (REPO_ROOT / str(src)).is_file():
            rep.error(f"rule {rule['id']!r} 的 source 不存在：{src}")

    # 正文不能太空
    body_wo_comment = re.sub(r"<!--.*?-->", "", card["body"], flags=re.S)
    if len(re.sub(r"\s+", "", body_wo_comment)) < 40:
        rep.warn("正文去掉注释后不足 40 字，可能还没写")

    return rep


# ────────────────────────── 全局唯一性 ──────────────────────────


def check_tool_id_uniqueness(cards: "list[dict]") -> Report:
    rep = Report("tool id 唯一性")
    seen: dict = {}
    for card in cards:
        for tool in card["tools"]:
            tid = str(tool["id"])
            if tid in seen:
                rep.error(
                    f"tool id 重复：{tid!r}\n"
                    f"        出现在 {seen[tid]} 和 {card['name']}\n"
                    f"        钩子按 id 定位脚本，重复会让它不知道该跑哪个"
                )
            else:
                seen[tid] = card["name"]
    return rep


# ────────────────────────── 生成物侧 ──────────────────────────


def check_artifacts(cards: "list[dict]") -> Report:
    rep = Report("生成物与卡片的一致性")

    try:
        expected = build.tree(cards, prefix="")
    except build.CardError as exc:
        rep.error(f"无法计算预期产物：{exc}")
        return rep

    for rel, content in expected.items():
        path = REPO_ROOT / rel
        if not path.is_file():
            rep.error(f"生成物缺失：{rel}（跑 python scripts/build.py）")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != content:
            rep.error(
                f"生成物与卡片不一致：{rel}\n"
                f"        要么卡片改了没重跑 build，要么生成物被手改过。\n"
                f"        修法：改 cards/ 里的卡片，然后 python scripts/build.py"
            )

    # 反向：skills/ 下有目录却没卡片 → 孤儿
    card_names = {c["name"] for c in cards}
    if SKILLS_DIR.is_dir():
        for d in sorted(SKILLS_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            if d.name not in card_names:
                rep.error(
                    f"孤儿技能目录：skills/{d.name}（cards/ 里没有对应卡片）\n"
                    f"        所有技能都必须由卡片生成。要么补一张 cards/{d.name}.md，要么删掉这个目录"
                )

    return rep


def check_registry_consistency(cards: "list[dict]") -> Report:
    """registry / hooks.json 与卡片声明的反向对账：生成物里不能有卡片没声明的东西。"""
    rep = Report("生成物反向对账")
    declared = {str(t["id"]) for c in cards for t in c["tools"]}

    reg_path = REPO_ROOT / "hooks" / "registry.json"
    if reg_path.is_file():
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            rep.error(f"registry.json 不是合法 JSON：{exc}")
            reg = {}
        for tid in reg.get("tools") or {}:
            if tid not in declared:
                rep.error(
                    f"registry.json 里有卡片未声明的工具：{tid!r}\n"
                    f"        这是生成物被手改或卡片被删后没重跑 build 的迹象"
                )

    hooks_path = REPO_ROOT / "hooks" / "hooks.json"
    if hooks_path.is_file():
        try:
            hj = json.loads(hooks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            rep.error(f"hooks.json 不是合法 JSON：{exc}")
            hj = {}
        for event, groups in (hj.get("hooks") or {}).items():
            for group in groups:
                for h in group.get("hooks", []):
                    cmd = str(h.get("command", ""))
                    m = re.search(r"run\.py\"?\s+([\w\-]+)", cmd)
                    if not m:
                        rep.error(f"hooks.json 的 {event} 里有无法解析的命令：{cmd}")
                        continue
                    tid = m.group(1)
                    if tid not in declared:
                        rep.error(
                            f"hooks.json 的 {event} 引用了卡片未声明的工具：{tid!r}\n"
                            f"        命令：{cmd}"
                        )

    return rep


def check_readme(cards: "list[dict]") -> Report:
    """README 手写、不在 build 覆盖范围内，所以它的数量和版本号会自己漂。

    只对账两处机器可裁断的事实：守卫数量、守卫名字集合。措辞和正文不管。
    """
    rep = Report("README 与卡片 / VERSION 的一致性")
    path = REPO_ROOT / "README.md"
    if not path.is_file():
        rep.warn("README.md 不存在，跳过")
        return rep
    text = path.read_text(encoding="utf-8")

    ver_path = REPO_ROOT / "VERSION"
    if ver_path.is_file():
        ver = ver_path.read_text(encoding="utf-8").strip()
        m = re.search(r"当前版本[：:]\s*([0-9][0-9A-Za-z.\-]*)", text)
        if not m:
            rep.warn("README 里找不到「当前版本：x.y.z」")
        elif m.group(1) != ver:
            rep.error(
                f"README 声明版本 {m.group(1)}，VERSION 是 {ver}\n"
                f"        bump VERSION 时要连 README 一起改"
            )

    m = re.search(r"^## (\d+) 个守卫\s*$", text, re.M)
    if not m:
        rep.warn("README 里找不到「## N 个守卫」章节")
        return rep

    declared = int(m.group(1))
    if declared != len(cards):
        rep.error(
            f"README 写「{declared} 个守卫」，cards/ 实际 {len(cards)} 张卡片\n"
            f"        新增卡片后要同时补 README 的守卫表"
        )

    rest = text[m.end() :]
    nxt = re.search(r"^(## |---)", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    listed = set(re.findall(r"^\|\s*`([A-Za-z0-9\-_]+)`", section, re.M))

    names = {c["name"] for c in cards}
    for name in sorted(names - listed):
        rep.error(f"守卫表里缺卡片：{name}（cards/{name}.md 存在，README 没写）")
    for name in sorted(listed - names):
        rep.error(f"守卫表里有不存在的卡片：{name}（cards/ 里没有这个）")

    return rep


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="卡片 / 生成物 / 磁盘 三方对账")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="只查指定卡片")
    ap.add_argument("--strict", action="store_true", help="把 WARN 也视为失败")
    ap.add_argument("--quiet", action="store_true", help="只输出有问题的项")
    args = ap.parse_args()

    if not CARDS_DIR.is_dir():
        print(f"[x] 找不到卡片目录：{CARDS_DIR}", file=sys.stderr)
        return 1

    try:
        cards = build.load_all_cards(args.only)
    except build.CardError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1

    if not cards:
        print("[!] cards/ 下还没有卡片。用 scripts/new_card.py 建一张。")
        return 0

    reports = [check_card(c) for c in cards]

    # 只有全量对账时才做全局检查，--only 时按单卡片视角看
    if not args.only:
        reports.append(check_tool_id_uniqueness(cards))
        reports.append(check_artifacts(cards))
        reports.append(check_registry_consistency(cards))
        reports.append(check_readme(cards))

    failed = [r for r in reports if not r.ok]
    warned = [r for r in reports if r.ok and r.warns]

    if not args.quiet:
        for r in reports:
            print(r.render())
        print()

    total_errors = sum(len(r.errors) for r in reports)
    total_warns = sum(len(r.warns) for r in reports)
    passed = len([r for r in reports if r.ok and not r.warns])

    print(
        f"{len(reports)} 项检查：{passed} 全绿、{len(warned)} 有提醒、{len(failed)} 有错误（{total_errors} 错 / {total_warns} 警）"
    )

    if failed:
        for r in failed:
            print(f"  [x] {r.scope}: {r.errors[0].splitlines()[0]}")
        return 1
    if args.strict and warned:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
