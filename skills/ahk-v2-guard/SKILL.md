---
name: ahk-v2-guard
description: "AutoHotkey v2 语法守卫：抓「照着 v1 的记忆写 AHK v2」。v1 写法在 v2 里不是编译 失败就是静默变行为——后者更贵，因为脚本能跑但行为不对。查四类机器可裁断的事实： 缺 #Requires v2.0、v2 已移除的内置变量、单引号字符串未闭合（报 Missing \"'\"）、 auto-concat 缺空格。另抓 v1 命令式语法与裸赋值。当用户抱怨\"AHK 脚本跑不了、 报 Missing 引号、行为和预期不一致\"时使用。触发词：AHK、AutoHotkey、ahk 脚本、 v2 语法、v1 兼容、Missing 引号、AHK 报错。"
agent_created: true
---

# AutoHotkey v2 语法守卫（ahk-v2-guard）

AHK 从 v1 到 v2 不是加功能，是**换语言**。v1 的写法搬到 v2，多数会编译失败——
这类反而便宜，改一下就好。贵的是另一半：**能编译，但行为变了**。

```
; v1                                  ; v2
MsgBox, hello                         MsgBox("hello")
x = 5                                 x := 5
y := A_LocalAppData                    y := EnvGet("LOCALAPPDATA")
Send {Enter}                          Send("{Enter}")
```

`Send {Enter}` 在 v2 里不是报错，是**把 `{Enter}` 当字面文本敲出去**。
脚本跑完了，什么都发生了，就是不是你要的那件事。

## 何时使用

- 项目里有 `.ahk` 脚本，要统一到 v2
- AHK 脚本报 `Missing "'"` 之类的怪错
- 改完 AHK 脚本，行为和预期不一致（八成是 v1 写法被静默接受了）
- 新人往 v2 项目里提交 v1 代码

## 核心方法

### 只查机器能裁断的事实，不查「像不像 v1」

这张卡**不做语法解析**。它只做四件字面层面的事：

| 检查 | 判据 | 抓什么 |
|---|---|---|
| `requires-v1` | 缺 `#Requires AutoHotkey v2.0` 或写着 v1.1 | 缺这行会按 v1 解释器跑 |
| `legacy-builtin` | 变量名在 v2 移除/改名清单里 | `A_LocalAppData`、`A_EnvVar`、`Clipboard` … |
| `unterminated-string` | 本行未转义单引号数为**奇数** | 字符串没闭合 → `Missing "'"` |
| `concat-no-space` | 单引号串（含双引号）后零空白紧贴标识符 | v2 强制 auto-concat 要空格 |
| `legacy-assign` | 行首 `name = value`（非 `:=`、非函数调用） | v1 裸赋值 |
| `legacy-command` | `MsgBox, hi` 这种逗号命令 | v1 命令式语法 |

**为什么不做完整解析**：门禁一旦误报，就会逼出 `--no-verify`，那整套守卫就白装了。
宁可放过少见的 v1 写法，也不误报合法的 v2 代码。

### `unterminated-string` 这条为什么用「奇数个单引号」

这是本卡最实用的一条，也是本项目真实踩过的坑。

AHK 里 `'"`（单引号包双引号）是**官方推荐**的表示字面双引号的写法，
官方文档明确说了这种写法成立。但它有个硬约束——**auto-concat 必须留空格**：

```
Run('"' path '" --src1')     ✓ 合法
Run('"'path'" --src1')       ✗ 报 Missing "'"
```

第二种写法为什么炸：单引号串里的双引号是**字面量**，不是定界符。
`'"path'` 里的双引号不会让字符串结束，解析器一路找闭合单引号，
于是整行后半截都被吞进去，最后报「缺一个引号」。

而「本行单引号数为奇数」正是这个症状的**充分信号**——正常代码单引号必然成对。
比起去解析「这个单引号串到底闭合了没」，数奇偶简单得多，也不会误报。

### 校验命令用 Windows 路径，别用 MSYS 路径

```bash
"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut /validate "脚本.ahk"
```

两个坑：

1. **不能用 `/tmp/x.ahk`** —— AutoHotkey 是 Windows 程序，打不开 MSYS 风格的路径，
   会返回 exit=2。**这个 exit=2 看起来像语法错，其实是「文件没找到」**，
   会把人带进完全错误的排查方向（本项目就因此浪费过一轮二分定位）。
2. **报错是 GBK 编码**，管道出来要转码才看得清：
   `... /validate "x.ahk" 2>&1 | iconv -f GBK -t UTF-8`

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **`A_LocalAppData` / `A_EnvVar`** | v2 里不存在，报「never be assigned a value」 | 用 `EnvGet("LOCALAPPDATA")` / `EnvGet("X")` |
| **`A_Is64BitProcess` / `A_Is64BitOS`** | 同上 | v2 移除了，需要自己判断 |
| **单引号串里的双引号** | `Missing "'"` | 拼接处留空格；或改用 `Chr(34)` |
| **`Loop Reg` 遍历注册表** | 本机实测读不到子键 | 别用；改走文件探测 |
| **`;` 当语句分隔符** | `Illegal character in expression` | v2 不支持，必须换行 |
| **`//` 当注释** | `does not contain a recognized action` | AHK 只有 `;` 注释 |
| **`MsgBox, hi`** | v2 里是函数调用，逗号会传错参 | 写 `MsgBox("hi")` |

## 实施步骤

1. 装套件到项目：
   ```bash
   python scripts/install.py --project /path/to/your-project --with-git-hook
   ```
2. 规则归项目所有：装完后 `rules/ahk-v2-guard.json` 在项目里，可按需调整
   `extensions`（如加 `.ahk2`）与 `skip_paths`。
3. 校验：
   ```bash
   python .codingskills/tools/check_ahk_v2.py --rules .codingskills/rules/ahk-v2-guard.json --all
   ```

## 与其他守卫的关系

- `format-lint-guard`：管 prettier/eslint 之类。AHK 没有 prettier，所以本卡
  是 AHK 唯一的**语法层**门禁，不与它重叠。
- `dead-code-guard`：管 JS/TS 的失效导入。AHK 不在其扩展名内，本卡是 AHK 的
  对应物，但**只管语法，不管死代码**。

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/ahk-v2-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `ahk-v2` | `tools/check_ahk_v2.py` | 校验 .ahk 脚本是否用了 AutoHotkey v2 语法 | staged-files | 0=通过 1=有 v1 残留或语法问题 2=配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `ahk-v2` | 否 | 15s |
| `pre-commit` | — | `ahk-v2` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/ahk-v2-guard.md 生成，请勿直接修改。 -->
