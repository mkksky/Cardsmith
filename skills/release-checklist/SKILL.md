---
name: release-checklist
description: "交付前体检：一个命令把套件全部守卫的 --all 结果汇总成一张表（架构分层 / 行数命名 / lint / 文档同步 / 密钥 / 迁移保护 / 调试坏味道），任一 FAIL 即退出码 1。 清单从 registry.json 动态取——新增守卫卡片后自动纳入，不用改本工具。 当用户说「交付前检查」「发布前体检」「跑一遍全部检查」「汇总检查结果」， 或交付/发版前想要一张全守卫体检表时使用。触发词：交付体检、release checklist、全量检查、体检表、发布前检查。"
agent_created: true
---

# 交付体检（release-checklist）

## 何时使用

- 功能做完准备交付 / 发版，想要一张「所有守卫一起过一遍」的体检表
- 新项目刚装上套件，想一次性摸底存量状态（哪项守卫会红、红多少）
- 单项守卫分散跑太碎，想要一个命令看全貌

## 核心方法

### 清单是动态的，不是抄的

体检表从 `hooks/registry.json` 取工具清单——这是 `build.py` 从全部卡片生成的
唯一注册表。**新增一张守卫卡片，体检表自动纳入**，不存在"加守卫忘了加体检"
的漂移口。反过来，git 事件专用工具通过 `rules/release-checklist.json` 的
`exclude` 排除：

- `commit-msg`：需要 git 传 message 文件参数，脱离 commit-msg 事件没法跑
- `sync-state`：会联网查远端，且 pre-push 已经管它，交付体检里重复跑没增量

### 状态判定

| 标记 | 含义 | 对整体退出码的影响 |
|---|---|---|
| `PASS` | 工具 exit=0 且无 `[!]` 行 | 无 |
| `WARN` | 工具 exit=0 但有 `[!]` 行（套件的非阻断提醒约定） | 无，但打印提醒内容 |
| `FAIL` | 工具 exit≠0 / 超时 / 脚本缺失 | **整体 exit=1** |
| `SKIP` | 被规则 exclude，或是聚合器自身（不递归） | 无 |

### 脚本缺失也算 FAIL

注册表里登记了但脚本文件不存在的工具判 FAIL 而不是跳过——「看着装了实际
没检查」是这套件明确要防的静默失效，体检表上必须红出来。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **把体检表挂到 pre-commit** | 每次提交都全量扫描，秒级变分钟级 | 别绑钩子；它是交付节点的一次性动作，`hooks: []` 是有意设计 |
| **新装项目首轮全红** | 存量问题全被 --all 扫出来 | 这正是摸底用途；先 `--update-baseline` 登记存量，之后体检只看新增 |
| **以为 WARN 会挡交付** | 看到黄色就以为过不了 | WARN 只是让人看一眼的提醒，不拦；只有 FAIL 拦 |
| **排除项想改** | 想在体检里也跑 sync-state | 改 `rules/release-checklist.json` 的 exclude；注意它会联网，CI 里慎开 |

## 实施步骤

1. 交付前在项目根目录跑：

   ```bash
   python tools/release_checklist.py --rules rules/release-checklist.json
   echo "退出码=$?"   # 0=可交付 1=有 FAIL 2=配置错误
   ```

2. 有 FAIL：按各项工具的输出逐个修（FAIL 行下面附了各工具最后 5 行输出）
3. 存量债务多时：先逐工具 `--update-baseline`，再回来跑体检确认全绿
4. 体检通过再走交付动作（打 tag / push / 发版）

## 验证

```bash
python tools/release_checklist.py --selftest    # 应 7/7 全绿
```

## 注意

- 本工具只聚合，不重复实现任何检查逻辑——判定全权交给被聚合的各守卫工具
- `timeout_seconds` 默认 120s/工具；大仓库可在规则 JSON 里调
- 聚合器自己不读基线配置，基线行为完全由各被聚合工具自决（棘轮语义不破坏）

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/release-checklist.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `release-checklist` | `tools/release_checklist.py` | 聚合 registry 全部工具的 --all 结果出体检表，git 事件专用工具按规则排除 | none | 0=全部通过（含 WARN） 1=有 FAIL 2=配置错误 |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/release-checklist.md 生成，请勿直接修改。 -->
