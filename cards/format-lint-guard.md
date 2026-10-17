---
name: format-lint-guard
stage: coding
version: 1
description: >-
  把项目已有的格式化与 lint 工具（ruff / ruff-format / eslint 等）接进提交链路，
  统一退出码与阻断策略，并在工具缺失时安静跳过而不是卡住提交。
  当项目有 lint 配置但提交时不触发、想加格式化门禁、要统一代码风格，
  或遇到「格式不一致总靠人记得手动跑」「eslint 只在 CI 上才报错」时使用。
  触发词：代码风格不统一、格式化检查、接 lint、ruff、eslint、prettier、
  提交前格式化、格式化门禁、lint 不生效。
tools:
  - id: format-lint
    script: tools/check_format_lint.py
    args: ["--rules", "rules/format-lint-guard.json"]
    inputs: staged-files
    exit: "0=通过或跳过 1=有违规 2=规则文件错误"
    desc: 调用本机已装的 ruff / ruff-format / eslint，统一到套件退出码
hooks:
  - event: PostToolUse
    matcher: "Write|Edit"
    tools: [format-lint]
    blocking: false
    timeout: 20
  - event: pre-commit
    tools: [format-lint]
    blocking: true
rules:
  - id: format-and-lint-clean
    kind: format-lint
    severity: error
    source: rules/format-lint-guard.json
---

# 格式化与 lint 接入

## 何时使用

- 项目已经有 `ruff` / `eslint` 配置，但只在 CI 或手动跑时才执行
- 出现「上次提交格式是好的，这次又乱了」
- 想加格式化门禁，但担心把提交卡死

## 核心方法

### 不自己实现规则，只做接入

格式化规则由 ruff / eslint 自己维护，本套件**只负责把它们接到正确的位置**。自己实现一遍 lint 规则是重复造轮子，而且一定跟不上上游。

`tools/check_format_lint.py` 做的事只有三件：找出该检查的文件、按扩展名分派给对应工具、把退出码翻译成套件的约定。

### 工具不存在时放行，而不是报错

这是刻意设计：

> 因为本机没装 ruff 就把提交卡死，结果是使用者开始 `git commit --no-verify`。
> 一旦养成这个习惯，连真正重要的分层检查也一起失效了。

同理，格式化属于「有则更好」的一层。**分层的依赖方向检查是硬约束，格式化是软约束** —— 两者在阻断策略上不该一刀切。

跳过时脚本会明确打印 `[~] 本机未安装，已跳过：ruff`，不会静默通过假装检查过了。

### 编辑时提示、提交时阻断

| 场景 | 行为 | 理由 |
|---|---|---|
| 写完文件（PostToolUse） | 只提示，不阻断 | 写到一半被打断会打断思路；且此时文件可能还没写完 |
| 提交前（pre-commit） | 阻断 | 这是最后一道能保证仓库整洁的关口 |

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **`npx` 自动下载** | 项目没装 eslint，`npx eslint` 却从网上拉了个版本，行为与 CI 不一致 | 用 `npx --no-install`，本地没装就明确失败/跳过 |
| **格式检查跑完自动改文件** | 提交时工具悄悄改了文件，但那些改动没进本次提交 | 检查用 `--check` / `--no-fix` 只读模式；自动修复作为单独步骤显式执行 |
| **全量格式化一次提交几千行** | 首次接入时把整个仓库格式化了，diff 无法 review | 先只对**改动文件**生效（本套件默认 `staged-files`），存量留作后续独立提交 |
| **规则升级导致大面积报错** | 换个 ruff 版本，老代码突然多出几百个违规 | 锁定工具版本；或先只对改动文件生效，天然避开存量 |
| **Windows 上可执行名不同** | Linux 上叫 `ruff`，某些环境里是 `ruff.exe`，`shutil.which` 找不到 | `shutil.which` 已处理 `.exe` 后缀；仍找不到就检查是否只装在虚拟环境里 |
| **虚拟环境未激活** | 工具装在 `.venv` 里，但提交时用的是系统 Python，报「未安装」 | 用 venv 内的绝对路径，或在规则 `command` 里写完整路径 |

## 实施步骤

1. 确认项目里已有什么工具（`ruff --version` / `npx --no-install eslint --version`）
2. 编辑 `rules/format-lint-guard.json`：只保留本机实际有的工具，删掉不用的
3. 先跑一次 `--all` 看看存量违规量，判断要不要走棘轮
4. 接入钩子：`PostToolUse` 提示 + `pre-commit` 阻断

## 验证

```bash
# 确认工具能被找到（未安装会打印 [~] 跳过）
python tools/check_format_lint.py --rules rules/format-lint-guard.json --all

# 端到端：造一个格式违规的文件，确认退出码为 1
printf 'import os,sys\nx=1\n' > tmp_bad.py
python tools/check_format_lint.py --rules rules/format-lint-guard.json --files tmp_bad.py
echo "退出码=$?"
rm tmp_bad.py

# 自身用例
python tools/check_format_lint.py --selftest
```

## 注意

- **规则文件里只留你真的装了的工具**。留一堆没装的，输出里全是 SKIP 噪音，会让人不再看输出
- 本脚本不做任何自动修复。要修复请显式跑 `ruff format` / `eslint --fix`，然后作为独立提交 review
- 格式化工具版本要锁定。同一份代码在不同版本下结论不同，会让「通过」变得没有意义
