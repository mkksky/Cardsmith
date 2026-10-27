#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""性能提示档的显式豁免。

基线记录的是待处理债务；豁免记录的是经核验后判定为合理实现、无需修改的命中。
两者分开保存，避免把「不是问题」混进债务清单。

规则字段：
    file     必填，仓库相对 POSIX 路径
    pattern  必填，规则 id
    match    可选，针对命中行片段的 Python 正则，用于同一文件多段循环中只放行特定循环
    reason   必填，说明为什么这不是待修性能问题
"""

from __future__ import annotations

import re


class ExemptionError(Exception):
    pass


def load(rules: dict) -> "list[dict]":
    entries = rules.get("exemptions", [])
    if not isinstance(entries, list):
        raise ExemptionError("exemptions 必须是数组")

    normalized = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ExemptionError(f"exemptions[{index}] 必须是对象")
        for field in ("file", "pattern", "reason"):
            if not entry.get(field):
                raise ExemptionError(f"exemptions[{index}] 缺少 {field}")
        pattern = entry.get("match")
        if pattern:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ExemptionError(f"exemptions[{index}].match 正则无效：{exc}") from exc
        normalized.append(entry)
    return normalized


def partition(issues: "list[dict]", exemptions: "list[dict]") -> "tuple[list[dict], list[dict]]":
    exempted = []
    remaining = []
    for issue in issues:
        key = f"{issue['file']}:{issue['pattern']}"
        matched = any(
            key == f"{entry['file']}:{entry['pattern']}"
            and (not entry.get("match") or re.search(str(entry["match"]), issue.get("snippet", "")))
            for entry in exemptions
        )
        (exempted if matched else remaining).append(issue)
    return exempted, remaining
