#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/forbidden_checks.py —— 分层规则里的「禁止依赖 / 禁止注解」判定

为什么单拆：分层主工具已经贴着 500 行上限，而这块与「方向对不对」无关 ——
方向规则允许外层依赖内层，所以 presentation → infrastructure 永远合法；
这里补的是「合法但违反架构约定」的第二类判断，正好是一个自然断点。

被谁使用：
    tools/check_import_direction.py
"""

from __future__ import annotations

import re
from pathlib import Path

from layer_spec import RuleError


def dependency_violations(
    rel: str,
    lineno: int,
    text: str,
    src_layer: dict,
    target_layer: dict,
    mod: str,
    defs: dict,
) -> list[dict]:
    """跨层直连：如 Controller 直接 import Repository。"""
    found = []
    for rule in defs.get("forbidden_dependencies") or []:
        if (
            rule.get("from_layer") == src_layer["name"]
            and rule.get("to_layer") == target_layer["name"]
        ):
            found.append(
                {
                    "file": rel,
                    "line": lineno,
                    "from_layer": src_layer["name"],
                    "from_rank": src_layer["rank"],
                    "to_layer": target_layer["name"],
                    "to_rank": target_layer["rank"],
                    # 键用规则 id 而不是模块名：同一文件里多个 Repository 只算一条，
                    # 修的时候本来就是整类依赖一起处理。
                    "module": rule.get("id") or mod,
                    "text": text,
                    "kind": "forbidden-dependency",
                    "rule": rule.get("id", ""),
                    "mode": rule.get("mode", "blocking"),
                    "why": rule.get("why", ""),
                }
            )
    return found


def annotation_violations(
    path: Path, rel: str, src_layer: dict, defs: dict
) -> list[dict]:
    """层级禁用的注解：如 Controller 上的 @Transactional。"""
    rules = [
        r
        for r in (defs.get("forbidden_annotations") or [])
        if r.get("layer") == src_layer["name"] and r.get("regex")
    ]
    if not rules:
        return []

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = []
    for rule in rules:
        try:
            rx = re.compile(rule["regex"])
        except re.error as exc:
            raise RuleError(
                f"forbidden_annotations 正则不合法：{rule['regex']}（{exc}）"
            ) from exc
        for lineno, line in enumerate(lines, 1):
            if rx.search(line):
                found.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "from_layer": src_layer["name"],
                        "from_rank": src_layer["rank"],
                        "to_layer": src_layer["name"],
                        "to_rank": src_layer["rank"],
                        "module": rule.get("id") or rule["regex"],
                        "text": line.strip(),
                        "kind": "forbidden-annotation",
                        "rule": rule.get("id", ""),
                        "mode": rule.get("mode", "blocking"),
                        "why": rule.get("why", ""),
                    }
                )
    return found


def split_advisory(violations: list[dict]) -> tuple[list[dict], list[dict]]:
    """拆成 (阻断, 提示)。提示档不进基线，也不影响退出码。"""
    blocking = [v for v in violations if v.get("mode") != "advisory"]
    advisory = [v for v in violations if v.get("mode") == "advisory"]
    return blocking, advisory


def print_advisory(advisory: list[dict]) -> None:
    """--quiet 也要打印：套件约定「退出码 0 但有输出 = 非阻断提醒」。"""
    if not advisory:
        return
    print(f"[!] {len(advisory)} 处架构提醒（非阻断，治理完成后可改为 blocking）：")
    for v in advisory:
        print(f"  {v['file']}:{v['line']}  [{v.get('rule', '')}] {v['text']}")
        if v.get("why"):
            print(f"    {v['why']}")
    print()


def selftest() -> int:
    """返回失败数。跨层直连与禁用注解各一条。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "src/controller/UserController.java"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "import com.congge.repository.UserRepository;\n"
            "@Transactional\n"
            "public class UserController {}\n",
            encoding="utf-8",
        )
        layers = {
            "presentation": {"name": "presentation", "rank": 3},
            "infrastructure": {"name": "infrastructure", "rank": 1},
        }
        defs = {
            "forbidden_dependencies": [
                {
                    "id": "controller-direct-repository",
                    "from_layer": "presentation",
                    "to_layer": "infrastructure",
                    "mode": "advisory",
                }
            ],
            "forbidden_annotations": [
                {
                    "id": "controller-transactional",
                    "layer": "presentation",
                    "regex": "@Transactional\\b",
                    "mode": "advisory",
                }
            ],
        }
        got = dependency_violations(
            rel,
            1,
            path.read_text(encoding="utf-8").splitlines()[0],
            layers["presentation"],
            layers["infrastructure"],
            "x",
            defs,
        )
        got += annotation_violations(path, rel, layers["presentation"], defs)
        kinds = sorted(v["kind"] for v in got)
        if kinds == ["forbidden-annotation", "forbidden-dependency"]:
            print("  [v] 跨层直连与 Controller 事务注解均被识别")
            return 0
        print(f"  [x] forbidden_* 识别异常：{got}")
        return 1
