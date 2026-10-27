# 宿主为什么不触发钩子 —— 根因定位（2026-09-25 实测）

> 结论：**配置位置没错，是宿主启动参数把 `project` 作用域整体关掉了。**
> `docs/HOOKS-VERIFY.md` 里"唯一剩下的未知"已有答案，且其中的**预案方向是错的**，
> 照做解决不了问题（见第五节）。

## 官方文档索引（钩子出问题，先查这里再动手修 · 老板 2026-10-24 定）

| 文档 | 链接 | 用途 |
|---|---|---|
| CodeBuddy CLI Hooks（v1.16.0，Beta） | https://www.codebuddy.ai/docs/zh/cli/hooks | 钩子 JSON 结构 / matcher / 事件 / 退出码 / 插件 hooks；注意它只描述**裸 CLI 默认值**，桌面端实际读哪个目录见 7.6/7.7 |
| Trae Hook 总览 | https://docs.trae.cn/ide_automate-actions-with-hooks | 六类事件、全局/项目 Hook、管理入口（设置 > Hooks）、运行日志查看 |
| Trae Hook 配置参考 | https://docs.trae.cn/ide/reference-for-hooks-configuration | `.trae/hooks.json` 精确 schema、各事件输入输出、Windows 用 PowerShell |

排障顺序：**先按上表查官方文档 → 再对照本档案各节实测结论 → 最后才改配置**。
改完任何钩子配置必须新开会话验证（快照机制），探针文件须真违规（第六节坑 3）。

---

## 一、结论先行

| 问题 | 答案 |
|---|---|
| 是技能包/运行时没同步吗？ | **不是**。`runtime-state.json` = `0.4.0`，套件仓库 `VERSION` = `0.4.0`，对账通过 |
| 是钩子配置位置错了吗？ | **不是**。`<项目>/.codebuddy/settings.json` 就是宿主读的项目级路径 |
| 是脚本层坏了吗？ | **不是**。方法 B 实测 7 条分发器行为完全正常 |
| 重装能解决吗？ | **不能**。重装只会把同一份配置写到同一个（被屏蔽的）位置 |
| 真正原因 | WorkBuddy 桌面端启动宿主 CLI 时传了 **`--setting-sources=user`**，`project` / `project-local` 两个作用域被排除，项目级 settings **从未被读取** |

---

## 二、实测证据

### 2.1 脚本层（方法 B）—— 正常

用带 `except Exception: pass` 的探针文件分别喂给 7 条分发器：

```
===== protected-paths | exit=0 =====   (静默：无输出)
===== doc-sync        | exit=0 =====   (静默：无输出)
===== size-naming     | exit=0 =====   (静默：无输出)
===== format-lint     | exit=0 =====   (静默：无输出)
===== import-direction| exit=0 =====   (静默：无输出)
===== debug-smells    | exit=0 =====   {"continue": true, "hookSpecificOutput": {
    "hookEventName": "PostToolUse", "additionalContext":
    "[debug-smells] [x] 发现 1 处调试坏味：\n\n  hook_probe_check.py:8  [except-pass] ..."}}
===== secrets         | exit=0 =====   (静默：无输出)
```

`debug-smells` 正确捕获并产出 `[debug-smells]` 前缀的 additionalContext；
干净文件静默放行。**脚本链路无需任何改动。**

### 2.2 宿主层（方法 A）—— 未触发

新建 7 条钩子当日（23:47）之后启动的本项目会话中，用 Write 工具创建
**明确含 `except-pass` 违规**的文件 → 工具返回里**无任何钩子提示**。
（同一会话内共 2 次 Write，均无输出。）

### 2.3 宿主源码：配置路径是对的

`cli/dist/codebuddy-lite-wb.mjs`（与 `codebuddy-headless.js` 一致）：

```js
getProjectHomeDir(){ return join(this.getWorkDir(), ".codebuddy") }
getHomeDir(){ return process.env.CODEBUDDY_CONFIG_DIR || join(homedir(), ".codebuddy") }
getWorkDir(){ return workspaceFolders[0]?.uri.fsPath || cwd }
```

→ 项目级 settings = `<工作区>/.codebuddy/settings.json`（**正是套件写入的位置**）
→ 用户级 settings = `CODEBUDDY_CONFIG_DIR/settings.json`

`hooks` 确实在受支持的配置键白名单里：

```js
let e_ = ["model","cleanupPeriodDays","env","includeCoAuthoredBy","permissions",
          "hooks","statusLine", ... "enabledPlugins", ...];
```

且钩子读取链真实存在：

```js
async getHooks(event){
  let list = [];
  let cfg = await this.settingsManager.get("hooks");   // ← 从 settings 读
  if (cfg) { let arr = cfg[event]; arr && list.push(...arr); }
  list.push(...await this.loadHooksFromPlugins(event));  // ← 插件链路
  ...
}
```

### 2.4 宿主日志：`--setting-sources=user`（决定性证据）

`~/.workbuddy/logs/daemon.log`：

```json
{"level":"warn","scope":"daemon-server:sidecar-manager","message":
 ["[sidecar-stderr] [Sidecar] Session __workbuddy_cli_host__-0-e8d8fc54 spawn details:
   command=D:\\Program Files\\WorkBuddy\\WorkBuddy.exe,
   args=<positional:...\\cli\\bin\\codebuddy> --serve --no-session-persistence
        --setting-sources=user --strict-mcp-config,
   cwd=%USERPROFILE%\\AppData\\Local\\Temp\\workbuddy-host-cli\\..."]}
```

配套实现（同 bundle）：

```js
// 默认值是包含 project 的，但被命令行参数覆盖
this.DEFAULT_SCOPES = [USER, PROJECT, PROJECT_LOCAL];

buildSettingScopes(){
  let s = this.cliProvider.parseOpts().opts.settingSources ?? getProcessSettingSources();
  if (!s) return [...this.DEFAULT_SCOPES];
  let parsed = this.parseSourcesString(s);      // "user" → [USER]
  ...
  return parsed;                                 // → 只剩 [USER]
}

async isScopeAllowed(scope){ return scope === CLI || (await this.getSettingScopes()).includes(scope) }

function isSettingScopeAllowedSync(scope, argv = process.argv){
  // 解析 --setting-sources；名单里没有该作用域 → false
  case PROJECT:       return list.includes("project");   // "user" → false ❌
  case PROJECT_LOCAL: return list.includes("local");     // "user" → false ❌
}
```

**作用域只剩 `user`，`project` / `project-local` 全被拒。**
因此 `settingsManager.get("hooks")` 只会拿到用户级钩子，项目级 7 条永不加载。

### 2.5 用户级路径的实际取值

`getHomeSkillsDir() = getHomeDir()/skills`，而本机技能装在
`%USERPROFILE%\.workbuddy\skills\`（`.codingskills-state.json` 亦在此），
且内置插件缓存在 `~/.workbuddy/plugins/cache/`（= `getHomePluginsDir()`）。

→ 反推 **`getHomeDir() = %USERPROFILE%\.workbuddy`**，
即 `CODEBUDDY_CONFIG_DIR` 被桌面端设成了 `.workbuddy`。

---

## 三、连带的副作用（同一个开关造成的）

`--setting-sources=user` 屏蔽的是**整个 project 作用域**，不止 hooks：

- `<项目>/.codebuddy/settings.json`（project）
- `<项目>/.codebuddy/settings.local.json`（project-local）
- 项目级 `permissions` / `sandbox` / `enabledPlugins` / `extraKnownMarketplaces`
- 项目级 `apiKeyHelper`

**任何"项目级配置文件"方案在当前桌面端启动方式下都不会生效。**

---

## 四、可行的两条修法

### 方案 A：改放用户作用域（最快，但有全局副作用）

把 `hooks` 块写进 `%USERPROFILE%\.workbuddy\settings.json`
（= `getHomeDir()/settings.json`，user 作用域在允许名单内）。

- ✅ 立刻能生效，不改套件安装逻辑
- ⚠️ **全局生效**：在**任何**项目里编辑文件都会跑这 7 条，命令里写死的绝对路径
  会让它们去查 某 Python 项目 的规则 → 需要在 `hooks/run.py` 加一条前置判断：
  **被编辑文件不在 `project_root()` 之下就静默 `return 0`**
- ⚠️ `~/.workbuddy/settings.json` 是**程序管理文件**（`sandbox.presetVersion` /
  `orderedRules` 由 WorkBuddy 自己维护）。实测其 mtime 停在 9-22 而期间应用多次重启，
  说明**不是每次启动都重写**；但 UI 里改设置时可能被重写并丢掉未知键。
  → 改动前**必须先备份**

### 方案 B：走插件链路（干净，推荐作为终态）

`HookExtensionLoader` 从**插件的 `hooks/hooks.json`** 加载，与
`--setting-sources` **无关**（内置 `sheetagent`、`tencent-docx` 正是这么做的）：

```js
let iv = "hooks/hooks.json";     // HookExtensionLoader 的固定文件名
```

把套件包成一个插件目录（`plugin.json` + `hooks/hooks.json`）放到
`~/.workbuddy/plugins/`，用 `enabledPlugins` 启用。

- ✅ 不受作用域开关影响，是官方内置插件走的同一条路
- ✅ 天然按项目/插件隔离
- ⚠️ 需要先摸清插件清单字段（`enrichPluginInfo` 读取的 `plugin.json` 键：
  `hooks/commands/skills/agents/mcpServers/lspServers/...` 与 `defaultEnabled`）

### 方案 C（兜底，已在生效）

git pre-commit 链式钩子。这一层走的是 `.githooks/`，**不受本次问题影响**，
提交时的架构/行数/lint/密钥检查确实在拦。实时提示层缺失≠门禁裸奔。

---

## 五、必须纠正的一处预案

`docs/HOOKS-VERIFY.md` 写的：

> 如果没反应，把 `install.py` 的 `SETTINGS_REL` 改成 `.workbuddy/settings.json` 重装即可。
> **（⚠️ 2026-10-23 注：`SETTINGS_REL` 已删除，现在自动检测多 IDE 目录。但本预案的方向——
>  把配置写到 `.workbuddy/`——本身就是错的，见下。）**

**方向错了，且不会有效**，两个原因：

1. 宿主读的项目级目录是 `getWorkDir()/.codebuddy`，**写死 `.codebuddy`**；
   项目级 `.workbuddy/settings.json` 不在任何加载路径上。
   （源码里 `.workbuddy/settings.json` 只出现在**沙箱文件保护清单**
   `productSettings.paths` 中，是"禁止改写"名单，与配置加载无关 ——
   原文档把这两件事混为一谈了。）
2. 就算路径对，`project` 作用域整体被 `--setting-sources=user` 排除，照样不生效。

**正确的下一步是方案 A（用户级）或方案 B（插件）。**

---

## 六、验证脚本的两个坑（补充）

除 `HOOKS-VERIFY.md` 已记录的两条（MSYS 路径转换污染、运行时会落后于套件）外：

3. **验证用例必须"确实违规"**。原文档的收官步骤写的是"用 Write/Edit 改一个源文件 →
   看有没有 `[debug-smells]` 提示"，但同一份文档自己也写了"干净文件 → 放行，无输出"。
   改一行注释属于干净文件 → **必然无输出**，会把"钩子没生效"和"钩子生效了但这文件没问题"
   混成同一个现象，得到**假阴性**。收官用例必须用带 `except-pass` 的探针文件
   （本次即如此设计）。
4. 探针文件用完即删；本次实测已确认工作区恢复原状。

---

## 七、后续进展（2026-10-23）

### 7.1 WorkBuddy 问题依然存在 —— 但影响范围缩小了

本档案定位的根因（`--setting-sources=user` 屏蔽 project 作用域）**在写这份分析时有效**，
至今未见 WorkBuddy 桌面端修复。CodeBuddy CLI 可能有不同行为（它不套壳 WorkBuddy.exe
启动），但桌面端用户仍然受影响。

### 7.2 `install.py` 改为多 IDE 适配 —— 核心缓解

**2026-10-23** `scripts/install.py` 改造完成：不再写死往 `.codebuddy/settings.json`
灌 hooks，而是**自动检测项目根下存在哪些 IDE 目录**，分别写入对应配置文件。

新增的 `scripts/ide_hosts.py` 适配了 5 个 IDE：

| IDE | 项目目录 | 配置文件 | placement | 宿主是否读 project 作用域 |
|---|---|---|---|---|
| CodeBuddy / WorkBuddy | `.codebuddy/` | `settings.json` | embed | ⚠️ WorkBuddy 桌面端屏蔽 |
| Trae | `.trae/` | `hooks.json` | top | ✅ 不受影响 |
| Claude Code | `.claude/` | `settings.json` | embed | ✅ 不受影响 |
| Codex (OpenAI) | `.codex/` | `hooks.json` | top | ✅ 不受影响 |
| Qoder（阿里） | `.qoder/` | `settings.json` | embed | ✅ 不受影响 |

→ **在 Trae / Claude Code / Codex / Qoder 里打开项目，实时编辑后提示应该正常触发。**

### 7.3 原方案 A / B 的状态

- **方案 A（用户级配置）**：仍然是 WorkBuddy 桌面端生效的最快路径，
  但全局生效的副作用没解决，且 `~/.workbuddy/settings.json` 仍由程序管理。
- **方案 B（插件链路）**：尚未实施，作为 WorkBuddy 终态修法保留。

### 7.4 方案 A 落地（2026-10-24）

- `hooks/run.py` 新增 `_belongs_to_project()` 守卫：钩子输入文件不在本项目根下
  → 退出码 0 静默放行（用户级部署的全局副作用已解决）。套件版本 bump 至 0.10.9
- OkxAutoTradeSys 的 `.codebuddy/settings.json`（10 条 PostToolUse，matcher
  `Write|Edit`）已合并进 `~/.workbuddy/settings.json`（合并前备份：
  `settings.json.bak-20261024-hooks`），运行时 `.codingskills/` 已同步新 run.py
- 实测：selftest 6/6；外项目路径静默放行；本项目 staged 违规文件
  `debug-smells --blocking` 退出码 2 + 回填详情
- **待办**：用户级 settings 由宿主启动时读取，需新开 WorkBuddy 会话验证方法 A
  （用带 `except-pass` 的探针文件走 Write，观察 `[debug-smells]` 上下文；
  注意第六节坑 3——干净文件必然无输出，收官用例必须真违规）
- 若新会话仍无反应，退路是方案 B（把套件包成 `~/.workbuddy/plugins/` 下的
  `plugin.json` + `hooks/hooks.json`，插件链路不受 `--setting-sources` 影响）

### 7.5 路径纠正（2026-10-24 晚，查官方文档后）

官方 Hook 文档（https://www.workbuddy.cn/docs/cli/hooks）明确：钩子配置
**只认 `.codebuddy/settings.json`**（用户级 `~/.codebuddy/settings.json` /
项目级 `<项目根>/.codebuddy/settings.json` / 项目本地 settings.local.json），
**不存在 `~/.workbuddy/settings.json` 这个挂载点**。7.4 节把 hooks 合并进
`~/.workbuddy/settings.json` 属于挂错位置（该文件宿主不读 hooks 键）。

已执行：
- 创建 `~/.codebuddy/settings.json`（用户级，10 条 PostToolUse，matcher
  `Write|Edit`，命令与项目级 `.codebuddy/settings.json` 一致）
- 从 `~/.workbuddy/settings.json` 移除误放的 hooks 键
  （备份：settings.json.bak-20261024-rmhooks）
- 项目级 `.codebuddy/settings.json` 保持原样（两作用域按官方规则合并）

另注（官方文档确认的生效机制）：
- 钩子在**会话启动时拍快照**，外部改配置文件当次会话不生效，
  需在 CLI `/hooks` 面板审查应用或新开会话
- Windows 上钩子命令**强制走 Git Bash**（cmd / PS 均不支持），
  命令须 bash 兼容——现有 python.exe 绝对路径写法兼容

### 7.6 三次纠正：以宿主源码为准，`~/.codebuddy/` 在桌面端确实不被读（2026-10-24 上午）

老板报告"对话中 hooks 仍未生效"，复查后发现 **7.5 的"纠正"本身是错的**：
官方文档（codebuddy.ai/docs/zh/cli/hooks）描述的是**裸 CLI 的默认行为**
（`getHomeDir() = CODEBUDDY_CONFIG_DIR || ~/.codebuddy`），
但 WorkBuddy 桌面端会覆盖这个环境变量。

**新证据（当前 app.asar / cli bundle，2026-10-20 版）**：

```js
// app.asar（桌面端主进程，启动 sidecar 时）：
let e = n.s();
process.env.CODEBUDDY_CONFIG_DIR = e;   // e = WorkBuddy 配置目录 = ~/.workbuddy

// cli/dist/codebuddy-lite-wb.mjs：
getHomeDir() = CODEBUDDY_CONFIG_DIR?.trim() ? env 值 : ~/.codebuddy
// → 桌面端下 getHomeDir() = ~/.workbuddy
// → 用户作用域 settings = ~/.workbuddy/settings.json（7.5 移出的位置才是对的）
```

**另一个关键变化：新版宿主不再屏蔽项目作用域。** 2026-09-25 定位的根因
`--setting-sources=user` 在 daemon.log **2026-10-23 起的 spawn 参数里已消失**，
且 bundle 中 `DEFAULT_SCOPES = [USER, PROJECT, PROJECT_LOCAL]` 恢复全量——
项目级 `.codebuddy/settings.json` 重新可用了（旧"二、实测证据"仅对旧版成立）。

**已执行的修复**：

1. `~/.workbuddy/settings.json` 回填 `hooks` 键（10 条 PostToolUse，
   matcher `Write|Edit`，命令与 `~/.codebuddy/settings.json` 完全一致，
   指向 OkxAutoTradeSys 运行时；合并前备份
   `settings.json.bak-20261024-hooksv2`）。跨项目误伤由
   `run.py` 的 `_belongs_to_project()` 守卫兜底（非本项目文件静默放行）
2. 新建 `codingskills/.codebuddy/settings.json`（项目级，12 条 PostToolUse，
   指向仓库自身 `hooks/run.py`，工具清单与 `hooks/hooks.json` 一致）
   —— 套件仓库自身的会话从此有实时钩子
3. `~/.codebuddy/settings.json` 原样保留（裸 CodeBuddy CLI 下仍有用，
   桌面端不读、无害）
4. 校验：两份配置逐条 diff 一致、JSON 解析通过、所有 python.exe /
   run.py 路径存在（verify 脚本一次性核验）

**验证步骤（新会话，当前会话快照不含新钩子）**：
在 codingskills 新开会话 → 让 Agent Write/Edit 探针文件
`hook_probe_check.py`（含 `except Exception: pass`，已建在仓库根，
未跟踪）→ 应看到 `[debug-smells]` 的 additionalContext；
干净文件必然无输出（勿用假阴性用例，见第六节坑 3）。验证完删除探针。

**教训**：桌面端环境下判断"读哪个配置"以 `app.asar` +
`cli/dist` 源码为准，官方文档只描述裸 CLI 默认值；
`process.env` 覆盖链（CODEBUDDY_CONFIG_DIR）是两个世界的分界线。

### 7.7 两份文档的区分（2026-10-24 补充，老板指正后逐节核对）

- `https://www.codebuddy.ai/docs/zh/cli/hooks`（CodeBuddy CLI v1.16.0，Beta）
  ≠ `https://www.codebuddy.cn/docs/cli/hooks`（WorkBuddy 桌面端）。前者是
  **独立 CLI 产品**的文档。
- codebuddy.ai 文档**全文未提** `CODEBUDDY_CONFIG_DIR` 与
  `--setting-sources`——它描述的 `~/.codebuddy/settings.json` 用户级路径
  只是裸 CLI 默认值，与"桌面端覆盖环境变量后读 `~/.workbuddy`"
  （7.6 的源码证据）不矛盾。
- 该文档确认的、对套件有用的机制：
  1. **作用域合并而非覆盖**（user + project + local + 企业策略并行生效）
     —— 7.6 修复后"用户级 10 条 + 项目级 12 条"叠加是官方支持形态；
  2. **插件 hooks（`hooks/hooks.json`）**：插件启用即自动合并、
     `${CODEBUDDY_PLUGIN_ROOT}` 引用、**不受 frontmatter 安全闸门约束**
     —— 即"方案 B"是官方机制，若用户级配置再被桌面端 UI 重写丢键，
     插件链路是终态退路；
  3. 快照 + `/hooks` 审核、Windows 强制 Git Bash、
     退出码语义（0=成功 / 2=阻断 / stderr 仅 fallback）——
     与 `run.py` 现有实现一致。

### 7.8 Trae 官方文档核对 + 两层钩子默认安装（2026-10-24，老板定：会话层与提交层缺一不可）

老板给出 Trae 官方文档（docs.trae.cn/ide_automate-actions-with-hooks 及
reference-for-hooks-configuration），逐项核对结果：

**适配器核对（`scripts/ide_hosts.py` 的 trae 适配器 → 全部吻合，无需改）**：
- 项目 Hook 路径：`$PROJECT_FOLDER/.trae/hooks.json` ✅（Windows 下全局是
  `~/.trae-cn/hooks.json`，项目级路径同形；我们只做项目级）
- 顶层结构：`{"version": 1, "hooks": {...}}`，`version` 仅支持 `1` ✅
  （适配器 `top_extra={"version": 1}` 正确）
- matcher：正则、区分大小写、`Write`/`Edit` 在 Trae 工具名表内 ✅
- Hook 定义：`{type: "command", command, timeout}` ✅（默认 30s，我们逐条给了 10-20s）
- PostToolUse 输出：stdout JSON `hookSpecificOutput.additionalContext` ✅；
  退出码 2 = stderr 传给模型上下文 ✅（run.py 两种模式都兼容）
- Windows 执行 shell：**Trae 默认 PowerShell**（不是 Git Bash）——
  ide_hosts.py 已按实测处理：非 ASCII 项目路径换相对路径 `.`、
  无空格路径去双引号（否则 PowerShell 编码/引号错配整批 exit 1）
- Trae 兼容读 `.claude/settings.json`，两者并存时同一条钩子会跑两次
  （install.py 已有提示）

**行为修正（`scripts/install.py`）**：
- 旧默认：git 提交层钩子要显式 `--with-git-hook` 才装 → 只装了会话层的
  安装是"半套"——IDE 钩子只拦 AI 的 Write/Edit，人工改完直接 commit
  它看不见；git 钩子只拦提交，会话里改坏文件没有即时提醒。
- 新默认（老板 2026-10-24 定）：**`--project` 模式下两层默认都装**——
  IDE 会话层按目录检测自动写入 + git 提交层默认安装（宿主钩子链式
  接力不覆盖）；`--no-git-hook` 显式退出提交层，`--with-git-hook`
  保留为兼容 no-op。dry-run 已验证：Trae 目录项目一键出两层。
- 换言之：装完即"会话中即时提醒 + 提交时强制门禁"双保险，
  与 7.3 方案 C（git 层兜底）的结论闭环。
