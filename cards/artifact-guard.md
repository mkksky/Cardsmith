---
name: artifact-guard
stage: coding
version: 1
description: >-
  拦本次新增的构建产物入库（.venv/dist/target/coverage/logs/tmp），
  提示已被跟踪的存量产物与前端产物目录落后源码超过阈值天数。
  当项目出现"本地好的、线上是旧版"、仓库里混入构建产物、
  或需要把前端构建产物从 git 里清出去时使用。
  触发词：构建产物入库、dist 进 git、.venv 提交、static 落后、
  前端产物同步、仓库卫生、artifact guard、产物不应入库。
tools:
  - id: artifact-guard
    script: tools/check_artifact_guard.py
    args: ["--rules", "rules/artifact-guard.json"]
    inputs: staged-files
    baseline: false
    exit: "0=通过（含提示） 1=新增产物入库 2=规则或 git 错误"
    desc: git ls-files 识别新增产物，提示存量产物与构建目录落后天数
hooks:
  - event: pre-commit
    tools: [artifact-guard]
    blocking: true
rules:
  - id: build-artifact-not-in-git
    kind: artifact-guard
    severity: error
    source: rules/artifact-guard.json
---

# 仓库卫生与前端产物同步守卫

构建产物不该进 git —— 这句话说起来简单，实际上很容易被"顺手加一下"或者
"忘了把构建目录加 .gitignore"绕过去。本守卫分两档，刻意处理存量债。

## 核心方法

### 只拦新增，存量提示

| 档位 | 检查 | 处理 |
|------|------|------|
| **阻断档** | 本次 staged 里出现 `.venv/`、`dist/`、`target/`、`coverage/`、`*.log`、`tmp/` | exit 1，提交被拦 |
| **提示档** | 已经被 git 跟踪的存量产物；`static/` 落后 `src/` 超过 N 天 | exit 0 + 输出 `[!]` |

只拦新增的理由：装上规则的第一天如果直接阻断，每次提交都会红，
最后大家一定 `--no-verify` 绕过。把存量放提示档，**新账**才拦得住。

### 构建目录落后检查

前端产物目录（如 `backend/src/main/resources/static`）如果最后一次 git 提交
时间落后源码目录（如 `src/`）超过阈值（默认 7 天），给出提示。这是最容易出现
"本地代码改了、线上还是旧版"的地方。

## 规则真源

- `rules/artifact-guard.json` — `blocked_paths`（阻断）、`advisory_paths`（提示）、
  `build_sync.source` / `build_sync.artifact` / `build_sync.max_lag_days`（构建目录落后检查）
- 工具：`tools/check_artifact_guard.py`

## 自检

```bash
python tools/check_artifact_guard.py --rules rules/artifact-guard.json --selftest
```
