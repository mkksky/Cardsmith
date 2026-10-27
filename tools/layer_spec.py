#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/layer_spec.py —— 分层规则的定义与匹配

从 check_import_direction.py 拆出来的一块：规则加载与校验、glob → regex、
以及「这条 import / 这个文件属于哪一层」的判定。都是无状态的纯逻辑，
不碰 git、不读源码，所以单独成模块，主工具只负责收集与比对。

拆的理由是行数：check_import_direction.py 在 0.10.3 之前已经 498 行，
贴着 500 的线（套件自己的 size-naming 守卫上限）。往里加 project_packages
必然超标，被门禁拦下。按职责切比削注释诚实 —— 这个文件承担的是
「规则是什么」，主文件承担的是「拿规则去比什么」，本来就是两件事。

被谁使用：
    tools/check_import_direction.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path


class RuleError(Exception):
    """规则文件的问题。对应退出码 2。"""


def load_rules(path: Path) -> dict:
    if not path.is_file():
        raise RuleError(f"规则文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则文件不是合法 JSON：{path}\n    {exc}") from exc

    layers = data.get("layers")
    if not isinstance(layers, list) or not layers:
        raise RuleError("规则文件缺少 layers（需要非空列表）")
    for i, layer in enumerate(layers):
        for field in ("name", "rank", "patterns"):
            if field not in layer:
                raise RuleError(f"layers[{i}] 缺少字段 {field!r}")
        if not isinstance(layer["patterns"], list) or not layer["patterns"]:
            raise RuleError(f"layers[{i}].patterns 必须是非空列表")

    data.setdefault("policy", "outer-may-depend-on-inner")
    data.setdefault("exempt", {})
    data.setdefault("skip_paths", [])
    comp = data.get("composition_roots", [])
    if not isinstance(comp, list) or any(not isinstance(p, str) for p in comp):
        raise RuleError("composition_roots 必须是字符串数组（glob pattern）")
    pkgs = data.get("project_packages", [])
    if not isinstance(pkgs, list) or any(not isinstance(p, str) or not p for p in pkgs):
        raise RuleError('project_packages 必须是非空字符串数组（如 ["com.congge"]）')
    return data


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """把 glob 转成正则。** 跨目录，* 不跨目录。"""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i : i + 3] == "**/":
                    out.append(r"(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == ".":
            out.append(r"\.")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def layer_of_path(rel_path: str, layers: list[dict]) -> "dict | None":
    """按文件路径判断所属层。同一文件匹配多层时报错，避免静默取第一个。"""
    hits = []
    for layer in layers:
        for pat in layer["patterns"]:
            if glob_to_regex(pat).match(rel_path):
                hits.append(layer)
                break
    if not hits:
        return None
    if len(hits) > 1:
        names = ", ".join(h["name"] for h in hits)
        raise RuleError(
            f"文件同时匹配多个层：{rel_path} → {names}\n"
            "    请把 layers[].patterns 改成互斥的，否则层归属不确定"
        )
    return hits[0]


def layer_of_module(module_path: str, layers: list[dict]) -> "dict | None":
    """按被导入的模块路径判断它属于哪一层。

    两种匹配方式，先精确后兜底：
      1. 模块路径转成候选文件路径后匹配 patterns（精确）
      2. 模块路径中出现了某个层的名字段（兜底，覆盖「项目根即层根」的布局）
    """
    if not module_path:
        return None
    parts = module_path.split(".")
    candidates = [
        "/".join(parts) + ".py",
        "/".join(parts) + "/__init__.py",
    ]
    for cand in candidates:
        hit = layer_of_path(cand, layers)
        if hit:
            return hit
    # 兜底：模块名里直接带层名
    for layer in layers:
        if layer["name"] in parts:
            return layer
    return None
