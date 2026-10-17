# 钩子生效验证

**这份文档存在的原因**：钩子的配置位置在本机**没有被实测确认过**。装完不等于生效，而一个静默失效的钩子比没有钩子更危险 —— 你以为有门禁，实际裸奔。

请按最后一节做一次实地验证。

---

## 一、已经确认的事实

这些是从 WorkBuddy 安装目录里读出来的，不是猜的：

| 事实 | 依据 |
|---|---|
| WorkBuddy 有 7 个事件：`PreToolUse` / `PostToolUse` / `SessionStart` / `UserPromptSubmit` / `Stop` / `SubagentStop` / `PreCompact` | 官方内置插件的 `hooks/hooks.json` 与实际文档 |
| 官方插件把钩子放在 `<插件目录>/hooks/hooks.json`，加载即生效，无需注册 | `tencent-docx`、`sheetagent` 两个内置插件都是这个结构 |
| 钩子命令支持 `${CODEBUDDY_PROJECT_DIR}` 与 `${CODEBUDDY_PLUGIN_ROOT}` | 同上 |
| 钩子脚本从 stdin 收 JSON，用退出码表达意图：`0` 放行、`2` 阻断（stderr 回填给模型） | 官方 Hooks 文档 |
| `~/.workbuddy/settings.json` 由 WorkBuddy 自己管理（含 `presetVersion` 等字段），**没有** `hooks` 字段 | 本机实际文件 |

## 二、还没确认的

**唯一剩下的未知：WorkBuddy 宿主是否真的读取 `<项目>/.codebuddy/settings.json` 并触发钩子。**

脚本链路已全部实测通过（见下节 2026-09-24 实测），配置文件也已就位。
**2026-09-24 已把 某 Python 项目 的运行时刷新到 0.4.0**（PostToolUse 从 3 条
补齐到 7 条，新增 secrets / debug-smells / doc-sync / protected-paths；
runtime-state.json 已写入，版本对账静默通过）。注意该项目的 `.githooks/pre-commit`
是**它自己版本化的链式钩子**（OKX 质量门禁 + 套件接力），重装运行时时
**不得带 `--with-git-hook`**，否则会把人家版本化的钩子当宿主覆盖。

最后一步「在 某 Python 项目 打开一个**新** WorkBuddy 会话 → 用 Write/Edit 改一个源文件 →
观察是否出现 `[debug-smells]` / `[import-direction]` 上下文提示」只能在该项目的会话里做，
本套件仓库的会话替不了。如果没反应，把 `install.py` 的 `SETTINGS_REL` 改成
`.workbuddy/settings.json` 重装即可。

### 2026-09-24 实测记录（某 Python 项目）

| 项 | 结果 |
|---|---|
| 方法 C：`.codebuddy/settings.json` | ✅ 已生成，PostToolUse × 5 条钩子，命令均为绝对路径（解释器 + 运行时） |
| 方法 B：run.py 非阻断（违规文件） | ✅ 退出码 0，stdout 输出 additionalContext JSON（`[debug-smells] 发现 1 处`） |
| 方法 B：run.py `--blocking`（违规文件） | ✅ 退出码 2，stderr 回填违规详情 |
| 方法 B：干净文件 | ✅ 放行，无输出 |
| 方法 A：宿主真实触发 | ⏳ 待新会话验证（本会话启动早于配置生成，无法判定） |

实测中踩的两个坑，后人注意：

1. **MSYS 路径转换污染测试输入**：Git Bash 里 `python -c "...'try:\n'..."`，
   `X:\n` 会被 MSYS 当盘符路径转成 `X:/n`，导致喂给检测器的代码文本全坏、
   检测器「该拦不拦」的假象。**验证脚本必须落盘执行，不要塞 `python -c`。**
2. **运行时会落后于套件**：运行时是安装时刻的快照，套件新增卡片后旧运行时
   注册表里没有新工具，`run.py` 对未注册工具**设计上静默放行**——表现为
   「钩子没反应」。重装一次运行时即恢复；`check.py` 与 pre-commit 对账层不受影响。
   **✅ 2026-09-24 已加版本对账**：安装时把套件版本与源仓库路径写进
   `.codingskills/runtime-state.json`，`run.py` 每次调用与套件根 `VERSION`
   对撞，落后就 stderr 警告（不阻断、不联网、仓库不在本机不误报）。
   自检：`python hooks/run.py --selftest`（6 用例）。

~~集成缺口：`--with-git-hook` 会把 `core.hooksPath` 指到 `.githooks/`，整体绕过项目
自己的 `.git/hooks/pre-commit`~~ **✅ 已修复（2026-09-24，钩子合并落地）**：
`scripts/git_hooks.py` 安装时探测宿主钩子（现有 `core.hooksPath` 或默认钩子目录），
路径记录到 `<项目>/.githooks/legacy-hook`；`.githooks/<event>` 跑完套件检查后
**链式接力**宿主钩子并聚合退出码——门禁叠加，谁也不顶掉谁；
`install.py --uninstall-git-hook` 卸载还原。某 Python 项目 自带的
ruff/mypy/pytest 门禁不再被顶掉。实测（临时仓库 + 伪造 legacy 钩子）：
legacy 失败 → commit 被拦；legacy 修复 → commit 通过且接力输出可见；
卸载后 hooksPath 还原、legacy 钩子原样保留。

> 以下为验证前的原始分析，保留备查。

官方文档说的是 `.codebuddy/settings.json`，但 WorkBuddy 的实际数据目录是 `.workbuddy`。本机两者都存在（`.codebuddy/` 里只有 logs，`.workbuddy/` 里有 settings.json）。

本套件的 `install.py` 目前写到 **`<项目>/.codebuddy/settings.json`**（因为 WorkBuddy 的权限预设里只登记了 `.codebuddy/settings.json` 这个项目级路径，没有 `.workbuddy/settings.json`）。

**如果实测发现不生效**，把 `install.py` 里的 `SETTINGS_REL` 改成 `.workbuddy/settings.json` 再装一次即可。别去手改 `~/.workbuddy/settings.json` —— 那个文件被程序管理，改动可能被覆盖。

## 三、三层保险（即使钩子没生效也不会裸奔）

这套设计的容错是分层的：

| 层 | 触发时机 | 依赖 WorkBuddy 钩子吗 |
|---|---|---|
| ① 编辑后提示 | `PostToolUse` | **是** |
| ② 提交前阻断 | git `pre-commit` | 否 —— 只需要 `core.hooksPath` |
| ③ 卡片对账 | `scripts/check.py` | 否 |

**第 ②③ 层已经实测通过**：在临时项目里造一个跨层导入，`git commit` 被拦下并返回退出码 1；修好后提交成功。

所以最坏情况是「编辑时没有实时提示」，但"坏代码进不了仓库"这条底线仍然守得住。

## 四、怎么验证钩子真的生效

### 方法 A：造一个必然违规的改动（最快）

```bash
# 1. 在你项目里找个分层目录下的文件，加一行跨层导入
echo "from <你的外层包> import x" >> <你的内层包>/xxx.py

# 2. 用 WorkBuddy 的文件编辑工具改一下这个文件（不要用命令行改，
#    钩子只挂在 Write/Edit 工具上）

# 3. 观察是否出现提示：
#      非阻断 → 对话里出现一段 [import-direction] 的说明
#      阻断   → 编辑被拒绝，并给出违规详情
```

### 方法 B：直接手动喂一个钩子输入（不经 WorkBuddy）

验证分发器本身有没有问题：

```bash
cd <你的项目>
echo '{"hook_event_name":"PostToolUse","tool_name":"Write","tool_input":{"file_path":"<绝对路径>"}}' \
  | python .codingskills/hooks/run.py import-direction --blocking
echo "退出码=$?   # 有违规应为 2"
```

这条能过，说明脚本链路是通的；那如果方法 A 没反应，问题就在「配置位置」而不是「脚本」。

### 方法 C：看配置有没有被读到

装完后检查该文件是否真的生成、内容是否指向正确的绝对路径：

```bash
cat <你的项目>/.codebuddy/settings.json
```

如果位置不对，用 `--dry-run` 先看一遍再改 `SETTINGS_REL` 重装。

---

## 五、已知的坑

- **不要手改 `~/.workbuddy/settings.json`**：它由 WorkBuddy 管理，含 `presetVersion` 等版本字段，手改可能被覆盖或触发迁移。
- **钩子命令一律用绝对路径**：`install.py` 会把裸 `python` 换成探测到的解释器绝对路径。PATH 不完整时裸 `python` 会静默失败。
- **`matcher` 在 IDE 与 CLI 下拿到的工具名不同**：配置文件里写 `Write|Edit` 两边都能匹配，但钩子脚本收到的 `tool_name` 可能是 IDE 风格（`write_to_file`）。本套件不依赖 `tool_name`，只用 `tool_input.file_path`，所以不受影响。
- **多个钩子并行执行，不保证顺序**：本套件每个工具互相独立，不存在顺序依赖。
