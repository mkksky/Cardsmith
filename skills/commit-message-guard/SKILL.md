---
name: commit-message-guard
description: "commit message 规范检查：类型前缀（feat/fix/chore 等）、可选 scope、主题行长度 上限，Merge 和 Revert 提交自动跳过。挂在 git commit-msg 阶段，message 写完、 提交对象生成前就拦。当用户抱怨「log 里搜不到当时怎么改的」「提交信息是 update / fix bug / 修改」「commit 被拦说格式不对」，或要给项目加 message 门禁时 使用。触发词：提交信息、commit message、message 规范、提交被拦、 主题行太长、conventional commits、commit-msg。"
agent_created: true
---

# 提交信息守卫

## 何时使用

- commit message 被钩子拦截，提示格式不对
- 想给项目加 message 门禁，但不想要一整套重工具（commitlint 等）
- `git log --oneline` 里出现「update」「修改」「fix bug」这种事后无法检索的提交

## 核心方法

### 为什么拦在 commit-msg 而不是 pre-commit

message 是在编辑器里写完才存在的 —— pre-commit 时 `COMMIT_EDITMSG` 还是上一次
的残留。commit-msg 阶段 git 把 message 文件路径作为 `$1` 传进来，此时拦截，
用户改一下 message 重跑 `git commit`（不带参数，内容会保留）就行，成本最低。

### 格式只是手段，检索才是目的

`<type>(<scope>): <描述>` 不是审美偏好：

- `git log --grep '^fix'` 直接拉出全部缺陷修复 → 生成变更日志
- scope 告诉你那次提交影响哪个模块 → 回滚定位不用逐个打开 diff
- 主题行限长是因为 `log --oneline` 和 GitHub 截断它 —— 被截断的类型前缀等于白写

### 跳过系统生成的提交

`Merge ...`、`Revert ...`、`Initial commit` 是 git 自动写的，格式不受人控制，
拦它们只会训练出 `--no-verify` 习惯。规则里 `skip_patterns` 放行这些前缀。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **改 --amend 反复被拦** | 修改 message 后提交还是失败 | 拦截用的是新 message，按提示改对就行；别用 --no-verify |
| **中文提交信息被误判** | 明明写了类型前缀还是报错 | 检查冒号：必须是半角 `: ` 且后带空格，全角冒号不匹配 |
| **类型表写太窄** | `wip` / `tmp` 之类的真实工作流被拦 | 类型列表是约定，不是法律 —— 按项目实际用法调整 `types` |
| **scope 随手写** | `feat(xxx):` 满天飞，scope 失去检索价值 | scope 用模块名（auth / migration / ui），团队对齐一张模块清单 |
| **交互式编辑器里换了行** | 第一行被截成了半句 | 主题行永远一行写完，细节空一行放正文（git 默认注释里有提醒） |

## 实施步骤

1. 对齐 `rules/commit-message-guard.json`：类型表按项目实际用法（本套件默认
   Conventional Commits 常用 11 类），长度上限默认 72
2. 存量 message 不需要迁移 —— 门禁只管新增提交，历史是历史
3. 装 git 钩子：`install.py --project <项目> --with-git-hook` 会把
   `.githooks/commit-msg` 一起装上并设置 `core.hooksPath`
4. 若把写 message 委托给了 AI，把格式约定放进生成提示词 —— 门禁是最后防线，
   不是生成规范

## 验证

```bash
# 手工验证一条 message（不真正提交）
git log -1 --pretty=%B | python tools/check_commit_msg.py --rules rules/commit-message-guard.json --stdin

# 端到端：造一个违规 message 文件，确认退出码为 1
printf 'update stuff\n' > /tmp/msg.txt
python tools/check_commit_msg.py --rules rules/commit-message-guard.json --msg-file /tmp/msg.txt
echo "退出码=$?"   # 应为 1

# 自身用例
python tools/check_commit_msg.py --selftest    # 应 11/11 全绿
```

## 注意

- 本工具运行在 commit-msg 阶段，**不接收文件列表**（`inputs: none`）——
  它的输入是 message 文件本身
- 主题行上限 72 而不是 50：中文一个字符占一个 char，50 对中文描述太紧；
  72 是 git 默认折行宽度，两边都能接受
- 规则改动 = 卡片 version +1，否则看不出行为何时变的

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/commit-message-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `commit-msg` | `tools/check_commit_msg.py` | 校验 commit message 的类型前缀、scope 与主题行长度 | none | 0=通过 1=违规 2=配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `commit-msg` | — | `commit-msg` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.cardsmith/` —— 先 `cd .cardsmith` 再照抄命令，或把路径写成 `.cardsmith/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/commit-message-guard.md 生成，请勿直接修改。 -->
