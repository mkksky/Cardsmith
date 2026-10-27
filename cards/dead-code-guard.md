---
name: dead-code-guard
stage: coding
version: 1
description: >-
  死代码守卫：抓「代码死了但没人知道」的两类机器可裁断事实——
  unused-import（require 绑定名字后全文再没出现）和 dead-export（具名导出
  在全仓库含测试零文本引用）。只查事实不查判断：只被测试引用的导出不拦，
  转口文件的下游死链不拦（图可达性留给 v2），被 ...require 展开继承的文件
  整文件跳过，动态拼名访问天然漏报不误报。存量走棘轮基线，只阻断新增。
  当用户抱怨「模块里一堆死代码」「冗余导出」「没人用的函数」「失效 import」
  「清理死代码」时使用。触发词：死代码、冗余代码、失效导入、无用导出、
  dead code、unused import、dead export、代码清理。

tools:
  - id: dead-code
    script: tools/check_dead_code.py
    args: ["--rules", "rules/dead-code-guard.json"]
    inputs: staged-files
    baseline: true
    exit: "0=通过或仅存量债务 1=新增死代码 2=配置错误"
    desc: 检查暂存文件的 require 绑定使用情况与全仓库导出引用计数

hooks:
  - event: PostToolUse
    matcher: "Write|Edit"
    tools: [dead-code]
    blocking: false
    timeout: 20
  - event: pre-commit
    tools: [dead-code]
    blocking: true
    timeout: 20

rules:
  - id: dead-code-scan
    kind: dead-code
    severity: error
    source: rules/dead-code-guard.json
---

# 死代码守卫（dead-code-guard）

死代码不报错、不崩、不影响测试，只静静地占着 API 面误导后人——「这个函数有人用吗」这种问题，答案应该在机器手里而不是考古里。这张卡抓两类**机器能裁断的事实**，其余明确不管。

## 何时使用

- 权限/服务模块改了好几轮，怀疑留下一堆没人调用的导出
- code review 里看到可疑的 `require` 但找不到使用处
- 大扫除前先摸底：存量有多少、都堆在哪些目录
- 新增代码里混进了复制粘贴来的失效导入

## 核心方法

### 只查零引用，不查「似乎没用」

引用计数取保守方向：**字符串字面量里出现名字也算引用、测试文件里的引用也算引用**。这会漏掉一部分真死代码（比如只被注释掉的调用用过），但保证抓出来的每一处删了都不炸。门禁工具一旦误报，就会逼出 `--no-verify`，宁漏勿误。

### 棘轮：存量登记，新增阻断

首次 `--all` 摸底的存量走 `--update-baseline --accept-debt` 显式认账入基线；之后每次提交只对**新增**违规阻断。修掉存量后重跑 `--update-baseline --all` 收紧基线——基线只许收缩不许膨胀。

### 引用库必须全量

`--files` 只裁剪「被检查的文件」，**引用语料永远是全仓库**——只看暂存文件的引用计数会把「A 删了引用、B 的导出因此变死」漏成绿灯。倒排索引（符号 → 文件集合）一次分词 O(N) 构建，千文件仓库秒级完成（实例项目 8 秒），这是全量语料的代价上限。反例是逐目标重建正则扫描的 O(N²) 写法，千文件仓库要跑 4 分钟以上，门禁慢到没人等就形同虚设。

### 两类检查的判定

- **unused-import**：把所有 require 绑定声明 span 挖掉后，名字再没出现过 → 违规
- **dead-export**：`module.exports = {...}`（花括号配对 + 顶层逗号切分）、`exports.name =`、`module.exports = Class` 三种形态都收；方法简写 `gone() {}` / `async gone()` / `get foo()` 也识别

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **误报转口文件** | index.js 转出口的下游没人用，但 index.js 自己「引用」了下游 | v1 明确不拦转口链（图可达性是 v2 的事）；整文件死链靠人看 |
| **误报展开继承** | `...require('./x')` 的消费方不写名字，导出名像死了 | 工具内置：被展开 require 的文件整文件跳过导出检查 |
| **动态拼名访问** | `mod[varName]` 查不到，导出像死了但实际活着 | 接受漏报；真要放行走基线，别为它改工具 |
| **云函数入口误报** | `exports.main` 全仓无字面引用 | 实测未命中（`main` 在语料里总有别处出现）；若某项目真命中，基线登记一条即可 |
| **草稿目录刷屏** | `.local-evidence/` 之类临时目录一堆违规 | `skip_paths` 排除草稿/生成物，别把垃圾登记进基线 |
| **收紧基线漏了 --all** | `--update-baseline` 没带 `--all`，只登记了暂存文件 | 与 file-size-naming-guard 同坑：收紧必须配全量扫描 |

## 验证

```bash
# 自检
python tools/check_dead_code.py --rules rules/dead-code-guard.json --selftest  # 16/16

# 端到端：确认死导出真的会被拦
printf "module.exports = { totallyDead() {} };\n" > /tmp/__dc_test.js
python tools/check_dead_code.py --rules rules/dead-code-guard.json --files /tmp/__dc_test.js
echo "退出码=$?"   # 应为 1
rm /tmp/__dc_test.js
```

## 注意

- v1 只支持 CJS `require` + `module.exports`/`exports.x`（CJS 仓库够用）；ESM `import`/`export` 语法不在范围
- 「只被测试引用的导出」不拦——是否保留 API 面是人的事（如仅供一致性测试消费的导出）
- 规则文件改动要连着卡片 `version` 一起加一
- 解析层在 `tools/dead_code_collectors.py`（纯函数无 IO），行数守卫管得到它也管得到主工具
