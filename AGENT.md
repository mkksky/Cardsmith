# AGENT.md —— 新接手本仓库 Agent 必读

## 一句话定位

**codingskills 是一套给 AI IDE（WorkBuddy / Trae / Claude / Codex / Qoder）用的
开发守卫套件**，由 17 张手写卡片驱动，通过工具链生成技能、钩子配置、git 钩子，
在编辑后、提交前、推送前三个时机拦截违规操作。

> 本文件写给**刚接手本仓库**的 Agent，不是写给终用户。
> 用户看 `README.md`，你看这个。

## 快速开始（首次克隆后）

```bash
# ① 自检，确认三方对账 + README/VERSION 对账全绿
python scripts/check.py
#   期望看到：21 项检查全绿，0 错 / 0 警

# ② 装技能到本机（让 WorkBuddy 能加载）
python scripts/install.py

# ③ 装到你自己的项目（自动检测多 IDE 目录 + git 钩子链式接力）
python scripts/install.py --project /path/to/your-project --with-git-hook --dry-run
#   --dry-run 改任何东西之前先跑，看看它会检测到哪些 IDE、写哪些文件

# ④ 然后才真装
python scripts/install.py --project /path/to/your-project --with-git-hook
```

## 改任何东西的正确顺序（不能乱）

```
改 cards/<name>.md  ★ 唯一真源（不要直接改 skills/ hooks/）
        ↓
python scripts/build.py          # 卡片 → 生成物（SKILL.md / hooks.json / registry.json / plans）
        ↓
python scripts/check.py          # 三方对账 + README/VERSION 对账
        ↓                         # （会检查卡片 ↔ 生成物 ↔ README 一致）
python scripts/install.py --project <项目>   # 重装运行时到目标项目
```

> **安全红线**：直接改 `skills/` 下的 SKILL.md、`hooks/hooks.json` 等生成物，
> `check.py` 会抓到并报错（"生成物与卡片不一致"）。
> **安全红线**：改版本号必须同时改 `VERSION` 文件和 `CHANGELOG.md`，
> 否则 `check.py` 的 README/VERSION 对账会失败。

## 仓库地图

```
codingskills/
├── cards/<name>.md       ★ 唯一真源（17 张，手写）
│     └── 卡片声明：工具 id、规则源、阶段、基线守卫
├── tools/*.py            ★ 检查脚本（被 run.py 调用）
│     ├── check_<xxx>.py           ← 主检查逻辑
│     ├── <xxx>_selftest.py        ← 自检脚本（独立跑）
│     └── import_collectors.py     ← 通用工具（供多张卡片引用）
├── rules/<name>.json     ★ 复杂规则的机器可读定义（零歧义）
│     └── 卡片的 source 字段引用这里
├── templates/card.md    ★ 新卡片模板（scripts/new_card.py 用）
├── skills/<name>/SKILL.md     ← 生成物（build.py 写，勿手改）
├── hooks/
│     ├── hooks.json           ← 生成物（build.py 写，勿手改）—— 中立钩子配置
│     ├── registry.json        ← 生成物 —— 工具注册表
│     ├── <event>-plan.json    ← 生成物 —— git 钩子清单
│     └── run.py               ← 分发器（被所有 IDE 钩子 / git 钩子调用）
├── scripts/
│     ├── build.py             卡片 → 生成物（SKILL.md / hooks.json / plans）
│     ├── check.py             三方对账 + README/VERSION 对账（21 项）
│     ├── install.py           装技能 + 装运行时 + 装 IDE hooks
│     ├── ide_hosts.py         ⭐ 多 IDE 适配器（扩新 IDE 改这里）
│     ├── git_hooks.py         git 钩子链式接力（不覆盖宿主钩子）
│     ├── new_card.py          建新卡片
│     ├── run_precommit.py     git pre-commit 钩子入口
│     ├── project_baselines.py 项目级基线管理
│     ├── baseline_ratchet_selftest.py 基线棘轮自检
│     └── *_selftest.py        各种自检脚本
├── docs/
│     ├── CARD-SPEC.md         卡片规范（写新卡片必读）
│     ├── CONVENTIONS.md       仓库编码/提交/目录约定
│     ├── HOOKS-HOST-GAP.md    WorkBuddy 已知的宿主问题根因
│     ├── HOOKS-VERIFY.md      IDE 钩子配置验证方法
│     ├── CASES.md             真实踩坑案例
│     └── BACKLOG.md           未来待做
├── README.md             ← 对用户的总览（check.py 会对账版本号 / 守卫名集合）
├── AGENT.md              ← 你现在读的（对 Agent 的工作指南）
├── CHANGELOG.md          ← 版本变更记录
├── VERSION               ← 单一真源版本号（README/CHANGELOG 要同步）
└── RUNTIME-README.md     ← 安装到项目后的运行说明
```

## 常见操作 checklist

### ① 建一张新卡片

```bash
python scripts/new_card.py <guard-name> --stage editing  # 或 debugging / committing
# → 在 cards/<guard-name>.md 生成模板，fill in content

# 写好卡片后：
python scripts/build.py      # 生成 SKILL.md / hooks.json 增量
python scripts/check.py      # 对账（新卡片要进 README 的守卫列表）
```

### ② 扩一个新 IDE 钩子适配器

改 `scripts/ide_hosts.py` 的 `ADAPTERS` 列表，加一条 `IdeAdapter(...)`：

```python
IdeAdapter(
    id="xxx",                              # --ide 参数用
    display="Xxx IDE",                     # dry-run 日志显示
    dir_name=".xxx",                       # 项目根下的 IDE 目录
    config_rel="hooks.json",              # 目录内的配置文件相对路径
    placement="top",                       # "top"（独立 hooks.json）或 "embed"（嵌 settings.json）
    top_extra={"version": 1},              # 可选：顶层额外 key
    strip_top_description=True,            # 可选：是否剥离 description 字段
),
```

然后：
1. `python scripts/build.py`（生成物不变，因为中立 hooks.json 是 schema 无关的）
2. 跑一次端到端验证：建一个临时测试项目 → `install.py --project <临时> --ide xxx` → 看生成的配置文件 schema 对不对
3. 更新 README.md 的多 IDE 配置表
4. 更新 `rules/doc-sync-guard.json` 的 `allow_refs`（加新 IDE 的路径）

### ③ 改版本号

三个地方必须同步，且顺序严格：
1. 改 `VERSION` 文件
2. 改 `CHANGELOG.md`（加新条目）
3. `python scripts/check.py`（README 里写的版本号和守卫数量也要同步，check.py 会告诉你差了什么）

### ④ 装到自己的项目并验证

```bash
python scripts/install.py --project /path/to/your-project --dry-run
# ↑ 先看检测到了哪些 IDE 目录（.codebuddy/ .trae/ .claude/ .codex/ .qoder/）

python scripts/install.py --project /path/to/your-project --with-git-hook
# ↑ 真正安装

# 验证 IDE 钩子（看配置文件内容）：
cat /path/to/your-project/.trae/hooks.json
cat /path/to/your-project/.codex/hooks.json

# 验证 git 钩子：
cat /path/to/your-project/.git/hooks/pre-commit
python scripts/run_precommit.py          # 手动触发一次
```

### ⑤ 跑所有自检

```bash
# 主对账
python scripts/check.py

# 独立自检（每个工具自己的 selftest）
python tools/check_secrets_selftest.py
python tools/check_dead_code_selftest.py
python tools/doc_sync_selftest.py
# ……（还有不少，在 tools/ 和 scripts/ 下带 _selftest.py 的）
```

## 多 IDE 适配说明

`scripts/ide_hosts.py` 是适配器注册表。核心数据结构：

- **自动检测模式**（默认）：只往项目根下**实际存在**的 IDE 目录写配置。
  用户想"装给任何 IDE 都能用"，就把 `.trae/` 等目录放在项目根下即可。
- **显式指定模式**（`--ide <name> ...`）：不管目录存不存在，都写配置
  （install.py 会自动 `mkdir -p`）。适合"只装 Trae / 不装 CodeBuddy"的场景。
- **跳过模式**（`--ide` 空值）：完全跳过 IDE hooks 写入，只装运行时 + git 钩子。

宿主兼容性：

| IDE | 配置文件 | placement | 宿主读 project 作用域 |
|---|---|---|---|
| CodeBuddy / WorkBuddy | `.codebuddy/settings.json` | embed | ⚠️ WorkBuddy 桌面端屏蔽（见 docs/HOOKS-HOST-GAP.md） |
| Trae | `.trae/hooks.json` | top | ✅ |
| Claude Code | `.claude/settings.json` | embed | ✅ |
| Codex (OpenAI) | `.codex/hooks.json` | top | ✅ |
| Qoder（阿里） | `.qoder/settings.json` | embed | ✅ |

> **Trae + Claude 共存陷阱**：Trae 官方说会兼容读取 `.claude/settings.json`。
> 项目同时有 `.trae/` 和 `.claude/` 时，Trae 会读两份配置合并执行，
> 同一条钩子可能被触发两次。只用 Trae 时建议 `--ide trae` 显式指定。

## 对账机制（check.py）

`check.py` 跑 21 项检查，核心是：

1. **卡片 ↔ 生成物**：每张卡片声明的工具 id 必须有对应的 tools/*.py
2. **生成物 ↔ 磁盘文件**：skills/<name>/SKILL.md 里写的钩子配置必须和 hooks/hooks.json 一致
3. **README ↔ VERSION**：README 里写的版本号、守卫数量、守卫名字集合必须和实际一致

**改 README 里的守卫列表 / 版本号之前，先改 VERSION / CHANGELOG / 卡片**——
check.py 会以 VERSION 和 cards/ 为真源，反向核对 README 有没有跟上。

## 已知的坑

- **WorkBuddy 桌面端 `--setting-sources=user`**：启动宿主 CLI 时传了这个参数，
  导致 project 作用域的 settings.json 整体被屏蔽。所以 `.codebuddy/settings.json`
  写对了，WorkBuddy 里也看不到钩子。**解决**：装 Trae / Claude / Codex / Qoder，
  或手动写 `~/.workbuddy/settings.json` 用户级配置。
- **不要手改用户级 settings.json 让它长期生效**：WorkBuddy 会管理这个文件
  （含 `presetVersion` 等版本字段），手改可能被覆盖或触发迁移。
- **钩子命令一律用绝对路径**：install.py 会把裸 `python` 换成探测到的解释器绝对路径。
  PATH 不完整时裸 `python` 会静默失败。
- **Trae + Claude 共存 = 钩子跑两次**：见上文。
- **多个钩子并行执行，不保证顺序**：本套件每个工具互相独立，不存在顺序依赖。

## 文档索引

| 文件 | 谁该看 | 什么时候看 |
|---|---|---|
| `README.md` | 终用户 | 装套件 |
| `AGENT.md`（本文件） | 接手 Agent | 第一天 |
| `docs/CARD-SPEC.md` | 写新卡片的 Agent | 建卡片前 |
| `docs/CONVENTIONS.md` | 所有人 | 改代码/提 PR 前 |
| `docs/HOOKS-VERIFY.md` | 排钩子问题 | 钩子不触发时 |
| `docs/HOOKS-HOST-GAP.md` | 理解宿主问题 | WorkBuddy 里钩子不触发时 |
| `docs/CASES.md` | 所有人 | 想知道真实踩了什么坑 |
| `docs/BACKLOG.md` | 排期的 Agent | 下一个迭代做什么 |
