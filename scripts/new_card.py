#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""new_card.py —— 新建一张编码卡片

卡片是唯一真源，所以这张卡片骨架的字段注解写得比较足，照着填就行。

用法：
    python scripts/new_card.py maven-build-troubleshoot --stage debugging
    python scripts/new_card.py api-contract-design --stage onboarding --title "接口契约设计"
    python scripts/new_card.py legacy-onboarding --force     # 覆盖同名卡片

建完之后：
    python scripts/build.py     # 卡片 → 生成物
    python scripts/check.py     # 三方对账

退出码：0 = 成功，1 = 参数问题，2 = 目标已存在。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CARDS_DIR = REPO_ROOT / "cards"
TEMPLATE = REPO_ROOT / "templates" / "card.md"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STAGES = ("onboarding", "coding", "debugging", "delivery")


def fail(msg: str, code: int = 1) -> None:
    print(f"[x] {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> int:
    ap = argparse.ArgumentParser(description="新建编码卡片")
    ap.add_argument("name", help="卡片名（小写字母/数字/连字符）")
    ap.add_argument("--stage", default="coding", choices=STAGES, help="所属阶段，默认 coding")
    ap.add_argument("--title", help="正文 H1 标题，默认由 name 自动拼")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的卡片")
    args = ap.parse_args()

    name = args.name.strip()
    if not NAME_RE.match(name):
        fail(
            f"名称不合法：{name!r}\n"
            "    只能用小写字母、数字、连字符；连字符不可连续或出现在首尾。\n"
            "    例：maven-build-troubleshoot / api-contract-design"
        )

    segments = name.split("-")
    if not 2 <= len(segments) <= 4:
        print(f"[!] 提醒：{name} 有 {len(segments)} 段，规范建议 2~4 段（见 docs/CARD-SPEC.md）")

    if not TEMPLATE.is_file():
        fail(f"模板不存在：{TEMPLATE}")

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    dest = CARDS_DIR / f"{name}.md"
    if dest.exists() and not args.force:
        fail(f"卡片已存在：{dest}\n    改名，或加 --force 覆盖", code=2)

    title = args.title or " ".join(w.capitalize() for w in segments)
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{NAME}}", name).replace("{{STAGE}}", args.stage).replace("{{TITLE}}", title)

    with dest.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    print(f"[+] 已创建：cards/{name}.md（阶段 {args.stage}）")
    print()
    print("必填项只有两个，但两个都决定这张卡片有没有用：")
    print("  description  —— 决定技能会不会被触发。触发词要写用户真会说的原话")
    print("  version      —— 改绑定关系时 +1")
    print()
    print("下一步：")
    print("  1. 编辑 cards/%s.md" % name)
    print("  2. python scripts/build.py")
    print("  3. python scripts/check.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
