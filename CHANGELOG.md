# 变更记录

版本号的唯一用途是**运行时对账**：`hooks/run.py` 拿项目里
`.cardsmith/runtime-state.json` 的 `suite_version`，与套件仓库根 `VERSION` 比对，
不一致就在 stderr 提示重装（机制见 `docs/HOOKS-VERIFY.md`）。

> ⚠️ **改了 `tools/` `rules/` `hooks/` 的行为，就必须 bump 根目录的 `VERSION`。**
> 只改代码不升版本，对账永远报"一致"，项目里跑的还是旧工具 ——
> 0.4.0 从落地到 0.5.0 之间，VERSION 一次都没动过，对账全程形同虚设。

---

## 0.8.0

**项目更名：codingskills → cardsmith**

- 28 个文件 97 处，涵盖代码、规则、卡片、文档与运行时路径
- **升级到 0.8.0 而非 0.7.2**：装到项目里的运行时目录名从 `.codingskills/`
  变成 `.cardsmith/`，旧项目原地升级会找不到工具。**从 0.7.x 升上来的项目
  必须先跑 `install.py --uninstall-git-hook` 再重装**，旧的 `.codingskills/`
  目录不会自动消失
- 改名涉及三处容易漏的运行时根判断（`hooks/run.py`、`run_precommit.py`、
  `release_checklist.py` 里 `runtime.name == ".cardsmith"`），
  以及 `build.py` / `install.py` 各定义一份的 `RUNTIME_DIRNAME`
- 更名依据：这批工具的名字赛道很挤 —— Ratchet、Writ、Driftless、Lattice、
  Vigil、Portcullis、Palisade 都有定位相近的开源项目。选 cardsmith 是因为
  「卡片驱动」是竞品普遍缺失的机制差异点

---

## 0.7.1

**修 stale-import-guard 的注释误报：JS/TS 导入收集先剥注释**

- `tools/import_collectors.py` 新增 `mask_js_comments()`，
  `collect_javascript()` 在跑正则前把注释内容替换成空格。
  换行逐字保留 —— 调用方靠 `src.count("\n", 0, m.start())` 算行号，
  抹掉换行会让所有报错行号错位
- **实测数据**（某小程序项目首次接入 0.7.0）：11 处报失效导入里 **7 处是误报**，
  真断链只有 4 处。误报源不是边角料，是最常见的代码形态 ——
  「修复记录」注释：`// require('../../../services/x')`、
  `/* 原实现: require('../../common/y') */`。
  blocking 守卫误报率过六成，第一次提交就会被当成噪声绕过去，
  比没有这个守卫更糟（它会给人「已检查过」的错觉）
- 剥法是轻量词法扫描，只区分字符串 / 行注释 / 块注释：
  ・字符串里的 `//`、`/*` 不误伤 —— 这正是不能直接用正则剥注释的原因，
    `const url = 'https://a.com/x'` 会被吃掉后半行
  ・普通字符串不跨行，遇换行强制退字符串态，一处未闭合引号不会吞掉后半文件
  ・正则字面量不特殊处理：JS 正则里的 `//` 必须写成 `\/`，
    写法上就不存在裸 `//`
- 新增 `scripts/import_collectors_selftest.py`（14 个用例）锁住
  「注释不算导入」与「行号对齐」两条底线。收集顺序也是契约：
  先 `JS_BARE_RES` 组、后 `JS_FROM_RE` 组，组内按出现位置
- 对 `check_import_direction.py` 的 JS 侧同样生效（同一收集器），
  属附带修复：以前注释里的跨层 require 也会被拿去判方向

---

## 0.7.0

**新增 stale-import-guard：失效导入路径检查（Python + JS/TS/Vue）**

- 新增 `tools/check_stale_imports.py` + `cards/stale-import-guard.md` +
  默认规则 `rules/stale-import-guard.json`。抓「模块被搬迁/改名后引用
  没跟上」的失效导入 —— 分层方向检查只看方向、文档同步检查只看文档，
  「目标模块是否存在」此前是盲区
- **Python** 走 AST 收集（含函数体延迟导入）：绝对导入按 packages 白名单
  校验，相对导入按 level 上溯还原后校验；解析优先级对齐 CPython
  FileFinder（常规包 > 模块 > 命名空间目录）—— 同名包/模块共存时判反
  会把合法导入误报成失效，真实案例见卡片「常见坑」
- **JS/TS/Vue** 用正则收集（零依赖）：只查 `./` `../` 相对导入与规则配置
  的构建别名（`javascript.aliases`）；裸说明符（第三方包 / Node 内建）
  天然跳过。`tools/import_collectors.py` 新增 `collect_javascript()`
- 接入 pre-commit（blocking）与 PostToolUse（提示）；基线键 = 文件:模块，
  与 import-direction 同一棘轮约定
- 规则向后兼容：旧项目已有的 `packages` 顶层键照常工作；`javascript`
  段缺省 = 不查 JS

---

## 0.6.0

**放宽 `file-size-and-naming-guard` 的 JS / TS / Vue 行数上限：400 → 600**

- `rules/file-size-and-naming-guard.json`：`*.js` / `*.ts` / `*.vue` 由 400 提到 600。
  400 对「一个页面的全部交互」这类文件偏紧 —— 某小程序项目项目里 `services/client.js`、
  `pages/*/detail.js` 天然在 500 上下，400 会逼着为过门禁而拆出缺上下文的碎片。
  600 与该项目 ESLint 的 `max-lines: 600` 取同一档，消除「600 才 warn、400 就硬拦」
  的错位：错位的阈值比没有阈值更容易让人误判门禁的真实意图
- `default` **保持 500**（`.py` / `.wxss` / `.wxml` 等仍按 500）；`*.java` 800 不变
- 卡片 `version` 5 → 6，阈值描述改为按类型分档的准确表述
- 卡片「常见坑」补一条：`--update-baseline` 必须配 `--all` ——
  它写入的是**本次扫描到的全部违规**，只扫暂存文件会把基线缩塌，
  其余存量债务到下次提交时一次性全变「新增违规」

> ⚠️ **对已安装项目不自动生效**：`install.py` 对 `rules/` 的策略是
> 「已有的保留，只补套件新增的」（阈值与 `skip_paths` 归项目所有）。
> 项目要切到新阈值，需手动同步该 JSON，再跑
> `python .cardsmith/tools/check_size_naming.py --rules .cardsmith/rules/file-size-and-naming-guard.json --all --update-baseline`
> 收紧棘轮（`--all` 不能省）。

---

## 0.5.1

**回写自某小程序项目项目的两处修复**

- 读暂存文件的工具统一改用 `git -c core.quotePath=false`（`check_doc_sync` /
  `check_size_naming` / `check_debug_smells` / `check_format_lint` /
  `check_import_direction` / `check_protected_paths`）—— 默认会把中文路径转义成
  `"\345\260..."` 字面串，路径解析不到文件，检查**静默通过**
- doc-sync 守卫新增 `allow_ref_patterns` 形态白名单（正则 `fullmatch`），放行
  「长得像路径但不是路径」的项目固定写法：价格单位（`元/㎡`、`万元/亩`）、
  纯数字比（`28/24/19`）、不入库的本地证据目录（`.local-evidence/`）。
  在 `load_rules` 加载期编译，坏正则按配置错误（退出码 2）处理，
  不拖到检查时才崩；自检用例补至 20 条
- secrets-guard 的 `--all` 摸底改为尊重 `.gitignore`
  （`git ls-files --cached --others --exclude-standard`）—— 原先 `rglob` 会把
  `.env` / `.env.local` 这类**永远进不了 git** 的本地文件判成「敏感文件被暂存」
  而 FAIL；而这些文件各机器本地存在与否不同，同一仓库在两台机器上会得出
  相反的体检结论

## 0.5.0

**doc-sync 守卫：引用解析增强**

- 路径候选拼装、后缀剥离抽成独立模块 `tools/doc_sync_paths.py`（本版新增）
- 行号 / 符号锚点 / 查询串后缀剥掉后，按纯路径再查一次
- `pages/` 下无扩展名路由补 `.js` 探测
- `cf_doc_bases` 支持云函数文档的邻域解析，含 monorepo 布局下按 `source_root` 拼接
- 自检用例补齐至 18 条

**sync-state 守卫：远端对账更硬**

- 远端名自动识别，不再要求显式传 `--remote`
- 追踪分支缺失或过期时判 `unknown` 并**阻断** —— 宁可拦下，也不在过期历史上开工

**release-checklist（新增）**

- 一个命令汇总全部守卫的 `--all` 结果；清单从 `hooks/registry.json` 动态取，
  后续新增守卫自动纳入，不用改本工具

**secrets-guard（新增）**

- 私钥块 / 云厂商 token / `.env` 类文件本身被暂存 → 高危阻断
- 敏感变量名 + 密钥形态的赋值 → 可疑，仅提醒不拦截
- 报告打码，基线存哈希不存原文

**build.py**

- SKILL.md 派生「运行时约定」一节，明确 `tools/` `rules/` 是相对**运行时根**
  而非项目根（避免读者照抄命令报 No such file）

## 0.4.0

首个带版本号的版本：引入根 `VERSION` 与运行时版本对账机制。

此前的内容（四类守卫、钩子合并与链式接力、`core.hooksPath` 接通、
卡片 → 生成物的 `build.py` / `check.py` 闭环等）见 `git log`。
