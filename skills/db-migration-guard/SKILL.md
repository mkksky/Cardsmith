---
name: db-migration-guard
description: "受保护路径守卫：把「这个目录落地后就不许再动」的规矩从人记变成门禁，典型对象是 数据库迁移脚本 —— 暂存区出现受保护路径下的任何变更（含删除）都会在提交前被阻断。 当用户问「迁移文件能不能改」「历史迁移要不要动」「migrations 目录报错被拦了」， 或项目里存在不许直接修改的目录（迁移脚本、生成的 schema、评审冻结的代码）需要 加保护时使用。触发词：迁移脚本、migrations、不许改、受保护路径、 改历史迁移、migration 被拦、冻结目录。"
agent_created: true
---

# 数据库迁移守卫（受保护路径）

## 何时使用

- 项目里有「落地即只读」的目录：数据库迁移脚本、生成的 schema、评审冻结的代码
- 需要改一个已提交的迁移脚本，想确认影响面
- 多机协作时，另一台机器上的人不知道「这个目录别碰」的口头约定

## 核心方法

### 为什么历史迁移不能改

迁移工具有自己的版本账本。改一个**已经执行过**的迁移脚本，会导致：

1. 新环境按改后的脚本建库，老环境还是老结构 —— 两边从此不一致，且没有任何报错
2. 校验和（checksum）对不上，迁移工具直接拒跑，卡住所有人的部署

正确做法永远是**追加新的迁移脚本**往前走，让历史保持原样。

### 把「人记的规矩」降级成「程序查的规矩」

「这个目录别动」如果只存在于口头或聊天记录里，多机协作必然破功 ——
另一台机器上的会话根本没有这段上下文。受保护路径检查把它变成 pre-commit
门禁：暂存区出现 `protected_paths` 下的任何路径（**含删除**）就阻断，
想改就必须显式处理，不存在「顺手改掉」。

### 删除比修改更危险

迁移脚本被删掉后，新环境根本建不出正确结构的库，而且这种损坏在部署当天
不一定暴露。所以本工具收集暂存变更时**不做 diff-filter 过滤** ——
D（删除）状态也在检查范围内。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **改了已执行的迁移** | 迁移工具报 checksum mismatch，所有人都部署不了 | 新增一个迁移脚本做修正（或 rollback 再修），历史文件恢复原样 |
| **删迁移脚本「清理」** | 新环境建库失败，或建出的库缺表缺列 | 迁移脚本不是垃圾文件；确实过期的用迁移工具的 drop/ squash 流程处理 |
| **保护路径写太宽** | `database/**` 把 seed、备份全拦了，天天误报 | 规则按真实约定写：只保护真正只读的路径，规则文件里可加 `skip_paths` 豁免 |
| **把新迁移也拦了** | 想提交一个新的迁移脚本，被门禁拦下 | 受保护 ≠ 禁止新增文件。如果项目约定连新增都要评审，把规则改成按变更类型区分（本工具默认新增也拦，按需放宽） |
| **Windows 路径分隔符** | 钩子报不出违规，路径带 `\` 没命中 | 工具内部已做 `\` → `/` 归一；自检里有对应用例守住 |

## 实施步骤

1. 对齐 `rules/db-migration-guard.json` 的 `protected_paths` 到项目的真实目录
   （不同项目可能是 `migrations/`、`db/migrate/`、`prisma/migrations/`）
2. 摸底：`--all` 跑一次，看当前工作区有没有已在受保护路径下的未提交变更
3. 接入钩子：编辑时提示（不阻断）、提交前阻断
4. 真的需要改受保护文件时：走显式评审，评审通过后用 `git commit --no-verify`
   单独提交（**只**这一条提交可以），提交说明里写清影响面与回滚路径

## 验证

```bash
# 端到端：造一个受保护路径下的文件变更，确认退出码为 1
mkdir -p database/migrations
echo "SELECT 1;" > database/migrations/20260924_test.sql
git add database/migrations/20260924_test.sql
python tools/check_protected_paths.py --rules rules/db-migration-guard.json
echo "退出码=$?"   # 应为 1
git reset database/migrations/20260924_test.sql && rm database/migrations/20260924_test.sql

# 自身用例
python tools/check_protected_paths.py --selftest    # 应 9/9 全绿
```

## 注意

- 本工具只看**路径**，不解析 SQL 内容 —— 迁移脚本是否可回滚是另一个问题，
  别指望这个工具替你审 SQL
- `skip_paths` 的豁免优先级高于 `protected_paths`（生成的快照文件等场景）
- 规则文件按项目定制后不要来回改 —— 保护清单是项目约定，漂移了门禁就没意义

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/db-migration-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `protected-paths` | `tools/check_protected_paths.py` | 阻断受保护路径（迁移脚本等）下的任何暂存变更 | staged-files | 0=通过 1=受保护路径有变更 2=规则或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `protected-paths` | 否 | 10s |
| `pre-commit` | — | `protected-paths` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/db-migration-guard.md 生成，请勿直接修改。 -->
