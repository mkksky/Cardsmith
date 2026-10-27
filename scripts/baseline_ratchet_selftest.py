#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/baseline_ratchet_selftest.py —— 棘轮闸门自检

为什么单独一个文件而不是塞进某个工具的 --selftest：
    闸门是六个工具共用的，横跨层。塞进任一工具的 --selftest，
    另外五个工具的回归就没人跑了 —— 而「只减不增」这条性质一旦破了，
    六个工具会同时失去意义。

锁住的性质（每一条都是老板实际踩过或真实项目踩过的）：

    1. 首建基线不受闸门约束 —— 否则接老项目的第一步永远做不成
    2. 只减不增直接放行 —— 这是棘轮的正常用法，不能被误伤
    3. 有新增键 → 拒绝，且必须列出具体是哪几条（只说「有新增」等于没报）
    4. 拒绝时磁盘上的基线文件不被改动 —— 闸门拦下不等于顺手写了一半
    5. --accept-debt 缺理由 → 拒绝（没理由的认账无法 review）
    6. --accept-debt + 理由 → 放行，理由与日期进 accepted 段
    7. accepted 段只追加不覆盖 —— 每笔认账都要留痕
    8. 报错文案要给出可执行的三条出路，不能只说「不行」

用法：
    python scripts/baseline_ratchet_selftest.py
退出码：0 = 全绿，1 = 有用例失败
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import baseline_store  # noqa: E402


class Case:
    """极简断言收集。不用 unittest 是为了跟 import_collectors_selftest.py 保持一致。"""

    def __init__(self) -> None:
        self.failures = 0
        self.count = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.count += 1
        if ok:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}")
            if detail:
                for line in detail.splitlines():
                    print(f"      {line}")
            self.failures += 1

    def raises(self, name: str, exc_type, fn, *args, **kwargs):
        """断言抛出了指定异常，并返回异常对象供进一步检查。"""
        self.count += 1
        try:
            fn(*args, **kwargs)
        except exc_type as exc:
            print(f"  [v] {name}")
            return exc
        except Exception as exc:  # noqa: BLE001 —— 抛错类型不对也是失败
            print(f"  [x] {name}\n      抛的是 {type(exc).__name__}，期望 {exc_type.__name__}：{exc}")
            self.failures += 1
            return None
        print(f"  [x] {name}\n      期望抛 {exc_type.__name__}，实际没抛")
        self.failures += 1
        return None


def run_selftest() -> int:
    c = Case()

    # ── 1. 首建不受闸门约束 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        plan = baseline_store.apply_ratchet(p, ["a.py:1", "b.py:2"], set())
        data = json.loads(p.read_text(encoding="utf-8"))
        c.check(
            "首建基线放行（old 为空集时全是新增，但语义是登记存量）",
            plan.get("first_build") is True and plan["new_count"] == 2,
            f"plan={plan}",
        )
        c.check(
            "首建留下 initial 记录（谁、何时、多少条）",
            data.get("initial", {}).get("count") == 2 and bool(data.get("initial", {}).get("date")),
            f"initial={data.get('initial')}",
        )

    # ── 2. 只减不增 → 放行 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1", "b.py:2", "c.py:3"], set())
        plan = baseline_store.apply_ratchet(p, ["a.py:1", "b.py:2"], {"a.py:1", "b.py:2", "c.py:3"})
        data = json.loads(p.read_text(encoding="utf-8"))
        c.check(
            "收紧（只减不增）直接放行",
            not plan["added"] and plan["removed"] == ["c.py:3"] and plan["new_count"] == 2,
            f"plan={plan}",
        )
        c.check("收紧后磁盘上的 entries 真的变少了", data["entries"] == ["a.py:1", "b.py:2"], f"entries={data['entries']}")
        c.check("纯收紧不写 accepted 段（没认账就不该有痕迹）", "accepted" not in data, f"accepted={data.get('accepted')}")

    # ── 2b. 键集合不变 → 也放行 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        keys = ["a.py:1", "b.py:2"]
        baseline_store.apply_ratchet(p, keys, set())
        plan = baseline_store.apply_ratchet(p, keys, set(keys))
        c.check(
            "重跑同一批违规（无变化）放行",
            not plan["added"] and not plan["removed"],
            f"plan={plan}",
        )

    # ── 3. 有新增 → 拒绝 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1"], set())
        exc = c.raises(
            "新增债务被拒绝（棘轮只降不升）",
            baseline_store.RatchetError,
            baseline_store.apply_ratchet,
            p,
            ["a.py:1", "d.py:4"],
            {"a.py:1"},
        )
        if exc:
            msg = str(exc)
            c.check(
                "拒绝文案列出了具体是哪几条键（只说「有新增」等于没报）",
                "d.py:4" in msg,
                f"msg={msg}",
            )
            c.check(
                "拒绝文案给出了可执行的三条出路（修代码 / 显式认账 / 修规则）",
                all(k in msg for k in ("修代码", "--accept-debt", "误报")),
                f"msg={msg}",
            )

            # ── 4. 拒绝时磁盘不被改动 ──
            after = json.loads(p.read_text(encoding="utf-8"))
            c.check(
                "拒绝后基线文件未被改动（拦下 ≠ 顺手写了一半）",
                after["entries"] == ["a.py:1"] and "accepted" not in after,
                f"entries={after['entries']}",
            )

    # ── 5. accept-debt 缺理由 → 拒绝 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1"], set())
        c.raises(
            "--accept-debt 缺理由被拒绝",
            baseline_store.RatchetError,
            baseline_store.apply_ratchet,
            p,
            ["a.py:1", "e.py:5"],
            {"a.py:1"},
            True,
            "   ",
        )

    # ── 6. accept-debt + 理由 → 放行并留痕 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1"], set())
        plan = baseline_store.apply_ratchet(p, ["a.py:1", "e.py:5"], {"a.py:1"}, True, "第三方 SDK 升级引入，本周内修")
        data = json.loads(p.read_text(encoding="utf-8"))
        accepted = data.get("accepted") or []
        c.check("--accept-debt + 理由放行", plan["added"] == ["e.py:5"], f"plan={plan}")
        c.check(
            "认账理由与日期进 accepted 段",
            len(accepted) == 1
            and "SDK" in accepted[0]["reason"]
            and accepted[0]["keys"] == ["e.py:5"]
            and bool(accepted[0]["date"]),
            f"accepted={accepted}",
        )

        # ── 7. accepted 只追加不覆盖 ──
        baseline_store.apply_ratchet(p, ["a.py:1", "e.py:5", "f.py:6"], {"a.py:1", "e.py:5"}, True, "第二条债")
        accepted2 = json.loads(p.read_text(encoding="utf-8")).get("accepted") or []
        c.check(
            "accepted 段只追加，第二笔认账不覆盖第一笔",
            len(accepted2) == 2 and "SDK" in accepted2[0]["reason"] and "第二条债" in accepted2[1]["reason"],
            f"accepted={accepted2}",
        )

    # ── 8. 纯函数 ratchet_plan 的三分类 ──
    plan = baseline_store.ratchet_plan({"a", "b", "c"}, {"b", "c", "d"})
    c.check(
        "ratchet_plan 正确拆分 added / removed / kept",
        plan["added"] == ["d"] and plan["removed"] == ["a"] and plan["kept"] == ["b", "c"],
        f"plan={plan}",
    )

    # ── 9. 范围守卫：没扫到的文件的债务不能被当成「已修复」销掉 ──
    # 真实事故（2026-10-17 某小程序项目）：detail.wxss 1902 行、远超 500 行上限，
    # 但 .wxss 压根没进 rules 的键空间 → --all 永远扫不到 →
    # 一次 --update-baseline 把它从基线里静默删掉了。
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1", "big.wxss:file-lines"], set())
        exc = c.raises(
            "未扫到的文件其债务消失 → 拒绝（不是修好了，是没扫到）",
            baseline_store.RatchetError,
            baseline_store.apply_ratchet,
            p,
            ["a.py:1"],
            {"a.py:1", "big.wxss:file-lines"},
            False,
            "",
            {"a.py"},
        )
        if exc:
            msg = str(exc)
            c.check(
                "范围守卫文案点名了涉及文件，并解释了三种可能原因",
                "big.wxss" in msg and "没扫到" in msg,
                f"msg={msg}",
            )
        after = json.loads(p.read_text(encoding="utf-8"))
        c.check(
            "范围守卫拦下后基线未被改动",
            set(after["entries"]) == {"a.py:1", "big.wxss:file-lines"},
            f"entries={after['entries']}",
        )

        # 扫到了就允许销掉（那是真修好了）
        plan = baseline_store.apply_ratchet(
            p, ["a.py:1"], {"a.py:1", "big.wxss:file-lines"}, False, "", {"a.py", "big.wxss"}
        )
        c.check(
            "文件扫到了、违规消失 → 允许销掉（真修好了不该拦）",
            plan["removed"] == ["big.wxss:file-lines"],
            f"plan={plan}",
        )

    # ── 9b. 不传 scanned 时跳过范围检查（兼容旧调用方）──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1", "big.wxss:1"], set())
        plan = baseline_store.apply_ratchet(p, ["a.py:1"], {"a.py:1", "big.wxss:1"})
        c.check("scanned=None 时不做范围检查（向后兼容）", plan["removed"] == ["big.wxss:1"], f"plan={plan}")

    # ── 9c. 范围守卫优先于新增闸门（两者同时成立时报更根本的那个）──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        baseline_store.apply_ratchet(p, ["a.py:1", "gone.wxss:1"], set())
        exc = c.raises(
            "范围守卫优先于新增闸门",
            baseline_store.RatchetError,
            baseline_store.apply_ratchet,
            p,
            ["a.py:1", "new.js:1"],
            {"a.py:1", "gone.wxss:1"},
            False,
            "理由",
            {"a.py"},
        )
        if exc:
            c.check("同时存在时先报范围问题", "没覆盖到" in str(exc), f"msg={exc}")

    # ── 10. load 容错：坏 JSON 抛 BaselineError 而非裸崩 ──
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.json"
        p.write_text("{ 这不是合法 JSON", encoding="utf-8")
        c.raises("坏基线文件抛 BaselineError（映射退出码 2）", baseline_store.BaselineError, baseline_store.load, p)

    print()
    if c.failures:
        print(f"自检失败：{c.failures}/{c.count} 项异常")
        return 1
    print(f"自检全绿：{c.count}/{c.count}")
    return 0


if __name__ == "__main__":
    sys.exit(run_selftest())