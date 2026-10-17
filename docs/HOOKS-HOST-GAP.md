# 宿主为什么不触发钩子 —— 根因定位（2026-09-25 实测）

> 结论：**配置位置没错，是宿主启动参数把 `project` 作用域整体关掉了。**
> `docs/HOOKS-VERIFY.md` 里"唯一剩下的未知"已有答案，且其中的**预案方向是错的**，
> 照做解决不了问题（见第五节）。

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
`%USERPROFILE%\.workbuddy\skills\`（`.cardsmith-state.json` 亦在此），
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
