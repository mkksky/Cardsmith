---
name: stale-import-guard
stage: coding
version: 3
description: >-
  失效导入路径检查：抓「模块被搬迁/改名后，引用没跟上」的失效导入 ——
  目标模块在当前仓库里根本不存在，运行时必然 ModuleNotFoundError /
  Cannot find module。AST 解析 Python 导入（含函数体延迟导入），
  正则解析 JS/TS/Vue 的 import/require。只查仓库内模块：Python 绝对
  导入按 packages 白名单、相对导入按 level 上溯；JS 只查 ./ ../ 与
  别名，裸说明符（第三方包）天然跳过。当用户说「模块找不到」「搬了
  文件以后 import 报错」「重构后引用失效」「ModuleNotFoundError」时使用。

tools:
  - id: stale-imports
    script: tools/check_stale_imports.py
    args: ["--rules", "rules/stale-import-guard.json"]
    inputs: staged-files
    baseline: true
    exit: "0=通过 1=新增违规 2=规则/用法错误"
    desc: 解析导入说明符，校验目标模块在仓库内真实存在

hooks:
  - event: PostToolUse
    matcher: "Write|Edit"
    tools: [stale-imports]
    blocking: false
    timeout: 15
  - event: pre-commit
    tools: [stale-imports]
    blocking: true
    timeout: 15

rules:
  - id: stale-import
    kind: stale-import
    severity: error
    source: rules/stale-import-guard.json
---

# 失效导入路径检查（stale-import-guard）

## 何时使用

- 重构搬了文件 / 改了模块名之后，确认所有引用都跟上了
- 合并大分支后，怀疑有「旧路径残留」
- 用户报告 ModuleNotFoundError、Cannot find module，但肉眼找不到问题

## 核心方法

这类失效的本质是**两份清单（文件系统 vs 引用）失同步**，靠肉眼 review
抓不全 —— 延迟导入藏在函数体里，JS 的 require 藏在任何表达式里。
本守卫做的是闭环校验：解析出每个导入的模块说明符 → 到文件系统里
解析它 → 解析不到即违规。

低误报的三道闸门：

1. **只查仓库内模块**。Python 绝对导入必须在 packages 白名单内才校验
   （第三方库天然跳过）；JS 裸说明符一律跳过（node_modules 解析不在范围）。
2. **解析优先级对齐语言运行时**。Python：`X/__init__.py` > `X.py` >
   `X/`（命名空间目录）—— 同名包/模块共存时包优先，判反了会把合法导入
   误报成失效（真实案例：某项目 `application/account_manager.py` 与
   `application/account_manager/` 共存）。
3. **符号导入放行**。Python 完整路径不存在但父级是模块文件 → 视为
   「从模块导入符号」，不算违规。

JS/TS 用正则而非 AST 是有意的取舍：零依赖换启发式，边界情况（字符串
里的 import 字样）最多造成一次误报，可被 skip_paths / 基线消化；
反之若漏报，真模块也不会漏 —— 误报可消化、漏报不发生，方向是对的。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| 规则 packages 配成空还期待查 Python 绝对导入 | 全量扫描 0 违规但明明有旧路径 | packages 填顶层包名列表；空 = 只查相对导入 |
| JS 构建别名没配进 javascript.aliases | `@/components/x` 全部误报失效 | 别名映射进规则（键以 `/` 结尾，值相对项目根） |
| `--update-baseline` 忘了配 `--all` | 基线缩塌，存量债务下次提交全变新增违规 | 更新基线必须配 `--all`，写入的是本次扫描到的全部违规 |
| 单体仓库把第三方 vendored 代码纳入扫描 | vendor 目录报成片失效 | skip_paths 排除 vendor / node_modules / site-packages |

## 实施步骤

1. 项目里定制 `rules/stale-import-guard.json`：Python 填 packages 白名单，
   JS 填 aliases 映射；都不用 JS/TS 就删掉 javascript 段
2. 首次接入有存量时：`--all --update-baseline` 登记成显式债务
3. pre-commit 自动跑（staged-files）；本地手动全量：`--all`

## 验证

- `python tools/check_stale_imports.py --rules rules/stale-import-guard.json --selftest`
  退出码 0，27/27 全绿
- 反向验证：临时把某个 import 改成不存在的模块 → pre-commit 阻断，
  输出 file:line 与模块路径
