---
name: module-contract-guard
description: "CommonJS 模块导出契约守卫：抓「消费方解构了某个成员，目标模块却没有导出」 的确定性运行时故障，例如定义了 exportObject 却忘记写 module.exports。 与死代码守卫互补：它查接口缺失，死代码查接口没人用。只解析可静态判定的 module.exports / exports.name / 对象变量 / 再导出；动态导出保守跳过。 触发词：漏导出、缺少导出、module.exports 漏写、undefined 解构、 模块契约、CommonJS 契约、Cannot read properties of undefined。"
agent_created: true
---

# 模块导出契约守卫（module-contract-guard）

死代码守卫回答「这个导出还有人用吗」；本守卫回答另一个方向：

> 消费方明明解构了这个名字，目标模块到底有没有导出它？

典型故障是模块里已经写好了 `exportObject`，却漏掉最后一行
`module.exports = exportObject`。文件语法合法、lint 通过、单测也可能通过，
直到真实加载时才出现 `Cannot read properties of undefined`。

## 何时使用

- 修过公共入口、`index.js`、聚合导出或 CommonJS 模块拆分
- 出现 `undefined` 解构、`Cannot read properties of undefined`
- 想确认改名后的导出和所有消费方仍然对齐
- 新项目接入时做一次全仓模块契约摸底

## 核心方法

### 双向契约，不猜业务

守卫只处理可裁断的事实：

- 消费方：`const { A, B: alias } = require('./mod')`
- 生产方：`module.exports = {...}`、`exports.A = ...`
- 对象变量：`const all = {...}; module.exports = all`
- 再导出：`module.exports = require('./base')`

目标模块缺少请求的源导出名就报违规。别名按**源导出名**检查，不按本地绑定名。

### 动态导出保守跳过

以下写法无法仅靠文本可靠求出完整导出面：

- `Object.assign(exports, loadExports())`
- `getExports()[key] = value`
- `module.exports = makeExports()`
- 导出对象后续按计算键补属性

遇到这些形态时整模块 `complete=False`，宁漏报不误报。门禁一旦频繁误报，
使用者就会习惯性绕过整个提交链。

### staged 与全仓的关系

`--files` / pre-commit 只决定**报告范围**，依赖关系仍从全仓消费者构建。
因此修改导出模块时，即使消费者文件没暂存，也能发现已有引用被改坏；
修改消费者时，也能立即发现它请求了目标模块不存在的成员。

### 基线

键格式为 `消费者:目标:导出名#missing-export`，不含行号。老项目首次接入可执行：

```bash
python tools/check_module_contracts.py \
  --rules rules/module-contract-guard.json --all --update-baseline \
  --accept-debt --debt-reason "首次接入登记存量契约债务"
```

安装器会预建空基线，所以首次登记存量也要显式认账；后续只允许收紧。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| 把 `exportObject` 定义当成已导出 | 全绿但运行时 undefined | 必须存在 `module.exports = exportObject` |
| 只扫暂存文件 | 改了生产模块，旧消费者没被复查 | 本工具始终扫描全仓契约，只按暂存范围过滤报告 |
| 动态导出被误报 | 属性由循环或工厂函数注入 | 保持保守跳过；不要为了一个项目改成猜测 |
| 路径本身不存在 | 契约守卫报缺失导出 | 这类问题归 `stale-import-guard`，本工具跳过 |
| 项目有构建别名 | `@/utils` 不解析 | 在规则 `aliases` 里配置前缀到项目目录 |

## 验证

```bash
python tools/check_module_contracts.py \
  --rules rules/module-contract-guard.json --selftest
```

自检覆盖漏写 `module.exports`、对象变量、别名解构、再导出、静态/动态 spread、
目录 index、规则别名、注释字符串/正则误匹配、skip_paths 与暂存过滤。

## 注意

- 当前只支持 CommonJS `require`；ESM `import {x} from` 不在范围
- 只检查仓库内相对路径和规则别名，第三方包天然跳过
- 规则改动需提升卡片 `version`

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/module-contract-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `module-contracts` | `tools/check_module_contracts.py` | 检查内部解构 require 请求的成员是否由目标模块真实导出 | staged-files | 0=通过或仅存量债务 1=新增契约违规 2=规则/用法错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `module-contracts` | 否 | 15s |
| `pre-commit` | — | `module-contracts` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/module-contract-guard.md 生成，请勿直接修改。 -->
