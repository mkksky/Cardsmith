---
# ── 卡片模板 ──────────────────────────────────────────────
# 用法：复制到 cards/<name>.md，或跑 python scripts/new_card.py <name>
# 字段含义见 docs/CARD-SPEC.md
# 注意：YAML 是严格子集，值里带 # 必须加引号；不支持 [a, b] 这种流式写法
name: {{NAME}}
stage: {{STAGE}}
version: 1
description: >-
  TODO 必填：先写「做什么 + 关键特征」，再写「当用户 XX 时使用」，
  最后把用户可能说的原话作为触发词列进去。写法见 docs/CONVENTIONS.md 第三节。

# ── 下面三段按需填写。什么都不绑的纯方法论技能可以整段删掉 ──

# tools:
#   - id: 工具标识（hooks 用它引用）
#     script: tools/xxx.py            # 相对仓库根，必须实际存在
#     args: ["--rules", "rules/xxx.json"]
#     inputs: staged-files            # staged-files | all-files | none
#     exit: "0=通过 1=违规 2=用法错误"
#     desc: 一句话说明

# hooks:
#   - event: PostToolUse              # 或 PreToolUse / pre-commit / SessionStart ...
#     matcher: "Write|Edit"           # 仅 PreToolUse/PostToolUse 用
#     tools: [工具标识]
#     blocking: false                 # true 时违规会阻断操作
#     timeout: 15

# rules:
#   - id: 规则标识
#     kind: import-direction          # file-lines | naming | api-contract | format-lint
#     severity: error                 # error 阻断 / warn 仅提示
#     source: rules/xxx.json          # 复杂规则外置
---

# {{TITLE}}

## 何时使用

- 

## 核心方法

<!-- 写「为什么这么做」和「不这么做会怎样」 -->

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
|  |  |  |

## 实施步骤

1. 

## 验证

<!-- 可观测判据：退出码、文件存在、计数对比 -->

- 
