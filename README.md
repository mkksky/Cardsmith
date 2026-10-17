# codingskills

一套**卡片驱动**的编码规范强制套件。给 AI 编码助手装的规矩，不能只写在文档里 ——
必须由程序在编辑后、提交前真的查。

[English](#english) | 许可证：[MIT](LICENSE) | 当前版本：0.7.1

## 它解决什么问题

单写一个 `SKILL.md` 的问题不是写不出来，是**它和它声称要做的检查会漂移**：
文档说「禁止跨层导入」，但没有任何程序真的在查；或者曾经有，后来脚本改名了、
卡片没跟上。

所以这里每个技能都必须回答三个问题，且答案由程序校验：

```
这个技能靠什么执行？   → tools/    里的脚本，卡片声明它，check.py 验证文件真的存在
什么时候跑？           → hooks     事件与阻断策略，build.py 生成配置
怎么确认没漂移？       → check.py  卡片 ↔ 生成物 ↔ 磁盘 三方对账
```

## 快速上手

```bash
# 1. 克隆
git clone https://github.com/mkkluo/codingskills.git && cd codingskills

# 2. 自检：确认套件自身没有漂移（14 项三方对账）
python scripts/check.py

# 3. 装到你的项目（会自动接 git 钩子，宿主钩子链式接力不覆盖）
python scripts/install.py --project /path/to/your-project --with-git-hook

# 4. 装完先看它会做什么（可选，--dry-run 不改任何东西）
python scripts/install.py --project /path/to/your-project --dry-run
```

装完后运行时目录落在你项目里（默认名 `codingskills`，前缀可配），
`git config core.hooksPath` 指向 `githooks`。
在项目里改一个源文件、或直接 `git commit` 一次，就能看到守卫在跑。

**默认阈值不适配你的项目？** 装完之后 `rules/` 归你所有（安装只补新增、不覆盖已有），
直接改 JSON，再用 `--update-baseline` 收紧：

```bash
python .codingskills/tools/check_size_naming.py \
  --rules .codingskills/rules/file-size-and-naming-guard.json \
  --all --update-baseline        # --all 不能省：它写入本次扫描到的全部违规
```

## 数据流

```
cards/<name>.md          ★ 手写唯一真源
      │  scripts/build.py
      ├──→ skills/<name>/SKILL.md        技能本体（勿手改）
      ├──→ hooks/hooks.json              WorkBuddy 钩子配置（勿手改）
      ├──→ hooks/registry.json           工具注册表（勿手改）
      └──→ hooks/<event>-plan.json       git 钩子清单：pre-commit / commit-msg / pre-push（勿手改）

      scripts/check.py  ← 三方对账，任一处漂移就报错
      tools/*.py        ← 卡片声明的检查脚本，被钩子调用
      rules/*.json      ← 复杂规则的机器可读定义（零歧义）
```

**改任何东西的顺序**：改 `cards/` → `build.py` → `check.py`。直接改 `skills/` 或
`hooks/` 会被 `check.py` 抓到并报错。

## 11 个守卫

| 守卫 | 检查什么 | 编辑后 | 提交前 |
|---|---|---|---|
| `layered-architecture-guard` | 分层依赖方向（AST，含函数体延迟导入） | 提示 | **阻断** |
| `file-size-naming-guard` | 单文件行数上限（js/ts/vue 600，其余 500，java 800）+ 文件名规范 | 提示 | **阻断** |
| `format-lint-guard` | 调本机 ruff / ruff-format / eslint，统一退出码 | 提示 | **阻断** |
| `root-cause-protocol` | 裸 except / except-pass / 空 catch 等吞错误写法 | 提示 | **阻断** |
| `db-migration-guard` | 受保护路径（迁移脚本等）下的任何暂存变更 | 提示 | **阻断** |
| `doc-sync-guard` | 文档引用的路径是否存在、API 文档方法名与源码 AST 对账 | 提示 | **阻断** |
| `secrets-guard` | 私钥块、云厂商 token、密钥形态赋值（两级严重度） | 提示 | **阻断** |
| `stale-import-guard` | 模块被搬迁/改名后引用没跟上（Python AST + JS/TS/Vue 正则） | 提示 | **阻断** |
| `multi-machine-sync-guard` | push 前 `ls-remote` 现查远端 SHA，对撞本地追踪分支 | — | **阻断** |
| `commit-message-guard` | commit message 类型前缀与格式 | — | **阻断** |
| `release-checklist` | 交付前把上面全部守卫的 `--all` 结果汇成一张表 | — | 手动 |

`format-lint-guard` 在工具未安装时**安静跳过**而不是报错 —— 没装 ruff 就卡住提交，
只会逼出 `--no-verify` 习惯，那样连真正重要的分层检查也一起失效。

## 三层防线

| 层 | 时机 | 依赖 WorkBuddy 钩子 |
|---|---|---|
| ① 编辑后提示 | `PostToolUse` | 是 |
| ② 提交前阻断 | git `pre-commit` | 否 |
| ③ 卡片对账 | 提交前 | 否 |

②③ 不依赖任何宿主，纯 git 钩子。若项目已有钩子（如 husky 的 pre-commit），
探测到后链式接力，不覆盖。

## 目录

| 路径 | 作用 |
|---|---|
| `cards/` | 唯一真源，每个技能一张卡片 |
| `skills/` | 生成物，技能本体（装到 `~/.workbuddy/skills/`） |
| `hooks/` | 生成物 + 分发器 `run.py` |
| `tools/` | 检查脚本，卡片用 `id` 引用 |
| `rules/` | 规则细节 JSON，卡片用 `source` 引用 |
| `scripts/` | 套件自身工具链（不随技能分发） |
| `docs/` | 规范、验证方法、候选清单、真实踩坑案例 |
| `templates/` | 新卡片模板 |
| `CHANGELOG.md` | 变更记录（版本号唯一用途是运行时对账） |
| `RUNTIME-README.md` | 安装到项目后的运行说明 |

## 命令

```bash
python scripts/new_card.py <name> [--stage debugging]   # 建卡片
python scripts/build.py                                 # 卡片 → 生成物
python scripts/check.py                                 # 三方对账
python scripts/install.py                               # 装技能到 ~/.workbuddy/skills/
python scripts/install.py --project <项目> --dry-run      # 先看会做什么
python scripts/install.py --project <项目> --with-git-hook
```

## 自检

```bash
python scripts/check.py                                   # 卡片 ↔ 生成物 ↔ 磁盘
python scripts/import_collectors_selftest.py              # JS 注释剥离与导入收集（14 例）
python scripts/yamlmini.py                                # 自带 YAML 解析器
for t in tools/check_*.py; do python "$t" --selftest; done # 每个检查工具各自的用例
python tools/release_checklist.py --rules rules/release-checklist.json   # 全守卫聚合体检
```

## 文档

| 文件 | 内容 |
|---|---|
| `docs/CARD-SPEC.md` | 卡片字段规范、支持的 YAML 语法、踩坑点 |
| `docs/CONVENTIONS.md` | description 触发词写法、正文结构与判断标准 |
| `docs/BACKLOG.md` | 四阶段候选清单，标了哪些已有卡片、哪些缺工具 |
| `docs/CASES.md` | 真实踩坑案例（症状→根因→解法→教训） |
| `docs/HOOKS-VERIFY.md` | 钩子生效验证方法与实测记录 |
| `docs/HOOKS-HOST-GAP.md` | 宿主项目级配置失效的分析与可行修法 |

## 依赖

**纯 Python 标准库，零第三方依赖。** Python 3.8+，无平台特定调用。

`scripts/yamlmini.py` 是自带的 YAML 子集解析器（严格模式，遇到不支持的语法
直接报错而不是静默忽略）。改解析器后先跑 `python scripts/yamlmini.py` 确认自带用例全绿。

## 设计取舍

**工具缺失时跳过而不是报错。** 没装 ruff 就卡住提交，只会逼出 `--no-verify` 习惯。

**新增正则类守卫，先在真实项目上跑一遍 `--all` 看误报率。** 过三成就不能直接当
blocking 用 —— `stale-import-guard` 首发误报率 64%（把「修复记录」注释里的历史
`require()` 当成真导入），blocking 守卫误报过三成会被人当噪声绕过去，
比没有守卫更糟：它给出「已检查过」的错觉。

**版本号的唯一用途是运行时对账。** `hooks/run.py` 拿项目里
`.codingskills/runtime-state.json` 的 `suite_version` 与套件根 `VERSION` 比对，
不一致就在 stderr 提示重装。改了 `tools/` `rules/` `hooks/` 的行为就必须 bump
`VERSION` 并补 CHANGELOG —— 只改代码不升版本，对账永远报「一致」，
项目里跑的还是旧工具。

---

## English

**codingskills** is a card-driven coding-standards enforcement suite. Rules for AI
coding assistants must not live only in docs — they have to actually run at edit
time and before commits.

Every skill answers three questions, verified by `scripts/check.py`:

- What executes it? → a script in `tools/`, declared by the card, existence verified
- When does it run? → hooks events and blocking policy, generated by `build.py`
- How do we know it hasn't drifted? → three-way reconciliation: card ↔ generated ↔ disk

Quick start:

```bash
python scripts/check.py                                          # suite self-check
python scripts/install.py --project /path/to/your-project --with-git-hook
```

Pure Python standard library, zero third-party dependencies. Python 3.8+.
See [LICENSE](LICENSE) (MIT).
