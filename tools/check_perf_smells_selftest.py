#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_perf_smells_selftest.py —— 性能守卫的回归用例

单独成文件而不是内联在工具里：与 `scripts/baseline_ratchet_selftest.py` 同一理由
（闸门横跨多层，塞进任一工具，其余层的回归就没人跑了），这里还多一条更硬的理由 ——
内联后工具本体 577 行，超过本套件自己要求的 500 行上限。

用例覆盖的四类边界，每一条都对应一次真实踩坑：
    1. 合理串行不误伤   重试循环、`for await` 流式消费、收集后 Promise.all
    2. 块边界判定准     Python 缩进、JS 大括号、无大括号单语句
    3. 字符串不误报     自检用例自身、文档示例里的代码样本
    4. 扩展名收口       wxml 标签正则不能扫 .js/.py；`*this` 是正确写法

用法：
    python tools/check_perf_smells_selftest.py
    python tools/check_perf_smells.py --selftest      # 同一个入口

退出码：0 = 全绿，1 = 有用例失败
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import baseline_store
import perf_exemptions
from check_perf_smells import check_one, is_skipped, violation_key

RULES = {
    "extensions": [".py", ".js", ".wxml"],
    "skip_paths": ["skip/**"],
    "builtin": ["await-in-loop", "setdata-in-loop", "wx-for-no-key"],
    "patterns": [
        {"id": "wx-key-index", "regex": r'\bwx:key\s*=\s*"(?:index|idx)"', "why": "wx:key 用下标"},
        {"id": "async-in-foreach", "regex": r"\.forEach\s*\(\s*async\b", "why": "forEach 不等待 async"},
        {
            "id": "await-sleep",
            "regex": r"await\s+new\s+Promise\s*\(\s*(?:\(?\s*\w+\s*\)?\s*)?=>\s*setTimeout\s*\(",
            "why": "用睡眠等异步",
        },
    ],
}


def run() -> int:
    failures = 0
    total = 0

    def case(name: str, files: dict, expect: int) -> None:
        nonlocal failures, total
        total += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            targets = [rel for rel in files if not is_skipped(rel, RULES["skip_paths"])]
            found = []
            for rel in targets:
                found.extend(check_one(rel, root, RULES))
            if len(found) == expect:
                print(f"  [v] {name}")
            else:
                print(f"  [x] {name}\n      期望 {expect} 处，实际 {len(found)}：{[(f['pattern'], f['line']) for f in found]}")
                failures += 1

    # ── 串行与并发：只抓真正的 N+1，放过合理串行 ──
    case("JS for 循环内 await → 命中", {"a.js": "async function f(xs) {\n  for (const x of xs) {\n    await load(x);\n  }\n}\n"}, 1)
    case("JS 收集后 Promise.all → 通过", {"a.js": "async function f(xs) {\n  for (const x of xs) {\n    ps.push(load(x));\n  }\n  await Promise.all(ps);\n}\n"}, 0)
    case("JS while 内 await → 命中", {"a.js": "async function f() {\n  while (hasMore) {\n    await next();\n  }\n}\n"}, 1)
    case("JS for await 流式消费 → 通过", {"a.js": "async function f(s) {\n  for await (const c of s) {\n    await save(c);\n  }\n}\n"}, 0)
    case("循环外的 await 不算 → 通过", {"a.js": "async function f() {\n  const a = await one();\n  return a;\n}\n"}, 0)
    case("无大括号单语句循环 → 命中", {"a.js": "async function f(xs) {\n  for (const x of xs) await load(x);\n}\n"}, 1)

    # ── Python：块边界靠缩进 ──
    case("Python for 内 await → 命中", {"a.py": "async def f(xs):\n    for x in xs:\n        await load(x)\n"}, 1)
    case("Python 同级后续语句不算块内 → 通过", {"a.py": "async def f(xs):\n    for x in xs:\n        ps.append(load(x))\n    await asyncio.gather(*ps)\n"}, 0)
    case("Python 注释里的 await 不算 → 通过", {"b.py": "async def f(xs):\n    for x in xs:\n        # await load(x)\n        ps.append(load(x))\n"}, 0)

    # ── 渲染：setData 与列表 key ──
    case("循环内 setData → 命中", {"a.js": "Page({\n  f(list) {\n    for (let i = 0; i < list.length; i++) {\n      this.setData({ ['a' + i]: 1 });\n    }\n  }\n});\n"}, 1)
    case("setData 在循环外 → 通过", {"a.js": "Page({\n  f(list) {\n    const patch = {};\n    list.forEach((it, i) => { patch['a' + i] = it; });\n    this.setData(patch);\n  }\n});\n"}, 0)
    case("wx:for 缺 wx:key → 命中", {"a.wxml": '<view wx:for="{{list}}">{{item.name}}</view>\n'}, 1)
    case("wx:for 带 wx:key → 通过", {"a.wxml": '<view wx:for="{{list}}" wx:key="id">{{item.name}}</view>\n'}, 0)
    case("wx:key 用 index → 命中", {"a.wxml": '<view wx:for="{{list}}" wx:key="index">{{item.name}}</view>\n'}, 1)
    # *this 表示「item 自身」，对字符串/数字数组是唯一正确写法，拦它属于误伤
    case("字符串数组用 *this → 通过", {"a.wxml": '<view wx:for="{{tags}}" wx:key="*this">{{item}}</view>\n'}, 0)

    # ── 字符串与注释里的样本不算违规 ──
    case("wxml 注释里的样本不算 → 通过", {"a.wxml": '<!-- <view wx:for="{{l}}">x</view> -->\n<view>ok</view>\n'}, 0)
    # wx-for-no-key 用标签正则，对 js/py 字符串里的 wxml 样本也能匹配 —— 工具自身
    # 的自检用例就因此被自己报过两条违规。必须先按扩展名收口。
    case("js 字符串里的 wxml 样本不算 → 通过", {"a.js": 'const tpl = "<view wx:for=\\"{{l}}\\">x</view>";\n'}, 0)
    case("py 字符串里的循环样本不算 → 通过", {"a.py": 'CASES = [{"js": "for (const x of xs) { await load(x); }"}]\n'}, 0)

    # ── 其它模式 ──
    case("forEach + async → 命中", {"a.js": "items.forEach(async (it) => {\n  await load(it);\n});\n"}, 1)
    case("await 睡眠等异步 → 命中", {"a.js": "async function f() {\n  await new Promise((r) => setTimeout(r, 1000));\n}\n"}, 1)

    # ── 收集口径 ──
    case("skip_paths 命中不查", {"skip/a.js": "async function f(xs) {\n  for (const x of xs) {\n    await load(x);\n  }\n}\n"}, 0)
    case("非目标扩展名不查", {"note.md": "for (const x of xs) { await load(x); }\n"}, 0)

    # ── 显式豁免：只放行登记过的文件+模式，可用片段正则收窄到合法循环 ──
    with tempfile.TemporaryDirectory() as td:
        total += 1
        rules_path = Path(td) / "rules.json"
        rules_path.write_text(json.dumps({
            "exemptions": [{
                "file": "a.js",
                "pattern": "await-in-loop",
                "match": r"for \(let attempt",
                "reason": "重试必须等待上一次失败"
            }]
        }), encoding="utf-8")
        exemptions = perf_exemptions.load(json.loads(rules_path.read_text(encoding="utf-8")))
        issues = [
            {"file": "a.js", "pattern": "await-in-loop", "snippet": "for (let attempt = 0; attempt < 3; attempt++) {"},
            {"file": "a.js", "pattern": "await-in-loop", "snippet": "for (const item of items) {"},
            {"file": "b.js", "pattern": "await-in-loop", "snippet": "for (let attempt = 0; attempt < 3; attempt++) {"},
        ]
        exempted, remaining = perf_exemptions.partition(issues, exemptions)
        if len(exempted) == 1 and len(remaining) == 2:
            print("  [v] 显式豁免按文件、模式和片段精确过滤")
        else:
            print(f"  [x] 显式豁免过滤异常：exempted={exempted} remaining={remaining}")
            failures += 1

    with tempfile.TemporaryDirectory() as td:
        total += 1
        try:
            perf_exemptions.load({"exemptions": [{"file": "a.js", "pattern": "await-in-loop"}]})
        except perf_exemptions.ExemptionError:
            print("  [v] 显式豁免缺少 reason → 配置错误")
        else:
            print("  [x] 显式豁免缺少 reason 未报配置错误")
            failures += 1

    # ── 基线过滤 ──
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "x.baseline.json"
        bp.write_text(json.dumps({"entries": ["a.js:await-in-loop"]}), encoding="utf-8")
        keys = baseline_store.load(bp)
        fake = [{"file": "a.js", "pattern": "await-in-loop"}, {"file": "b.js", "pattern": "await-in-loop"}]
        fresh = [v for v in fake if violation_key(v) not in keys]
        if len(keys) == 1 and len(fresh) == 1 and fresh[0]["file"] == "b.js":
            print("  [v] 基线过滤（键 = 文件:模式id）")
        else:
            print(f"  [x] 基线逻辑异常：keys={keys} fresh={fresh}")
            failures += 1

    print()
    if failures:
        print(f"自检失败：{failures}/{total} 项异常")
        return 1
    print(f"自检全绿：{total}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
