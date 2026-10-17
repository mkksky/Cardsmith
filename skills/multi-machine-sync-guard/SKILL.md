---
name: multi-machine-sync-guard
description: "多机 Git 同步守卫：push 前用 git ls-remote 现查远端真实 SHA，与本地 HEAD 硬碰 硬对撞，不信任 git status、不信任本地追踪缓存。发现本地落后或与远端分叉就阻断， 防止在过期历史上开工或误用 --force 覆盖别人的提交。当用户说「多台机器同步」 「push 被拒」「另一台电脑推过了」「分叉了怎么办」「要不要 force push」， 或接手一台新机器要先确认仓库状态时使用。触发词：多机同步、ls-remote、 对撞校验、远端 SHA、push 被拒、分叉、落后远端、force push、远程仓库状态。"
agent_created: true
---

# 多机同步守卫

## 何时使用

- 多台机器通过同一远端仓库（Gitee 等）接力开发，push 前想确认没踩到别人的提交
- 一台机器上工作完，换另一台机器继续 —— 开工前先对撞一次
- push 被拒后想搞清楚到底分叉没分叉、谁领先谁
- 接手一台新机器 / 新克隆，想确认「我看到的远端状态是真的」

## 核心方法

### 为什么不信 git status，也不信 fetch 缓存

`git status` clean 只说明**工作区**没改动，不说明本地历史包含远端最新提交 ——
这是两件事。而 `git fetch` 后的 remote-tracking ref（`origin/main`）是**上次
fetch 时**的快照：另一台机器刚推的提交，fetch 之前根本看不见。

唯一可信的做法是 `git ls-remote` —— 直接问远端要此刻的 SHA，与本地 `HEAD`
硬碰硬比对。本工具全部判定建立在这条证据上，输出里会打印两个 SHA 让人对。

### 分叉时的动作是 rebase，不是 force

本地落后 → `git pull --rebase`。分叉 → 同样 `git pull --rebase`，把本地提交
摞到远端最新之上。`--force`（以及更隐蔽的 `--force-with-lease` 之外的任何
强推变体）会覆盖远端历史，另一台机器的提交凭空消失 —— 在多机接力场景里
这是数据丢失，不是工程手段。

### 网络不可达时放行

pre-push 阶段 ls-remote 连不上远端时工具放行并警告 —— 因为接下来的 push
本身也会因网络失败，重复拦截没有信息增量。网络恢复后手动跑一次对撞补上。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **信了过期追踪分支** | `git status` 说 ahead 3，实际远端已前进 5 个提交 | 一切以 ls-remote 现查为准；fetch 只是缓存刷新，不是对撞 |
| **远端不叫 origin** | 工具说「无法连接远端 origin」然后放行，守卫形同虚设 | 远端名是仓库属性，不该被工具假设。本工具按 `branch.<分支>.remote` → 仓库唯一远端 → `origin` 顺序自动识别；也可显式 `--remote <名>` |
| **误用 --force 覆盖队友** | push 被拒后随手 `--force`，另一台机器的提交消失 | 先 `git pull --rebase`；rebase 后正常 push 即可，不需要 force |
| **ls-remote 相似分支名混淆** | 查 `main` 结果匹配到了 `main-v2` | 工具内按 ref 精确匹配（自检有用例守住）；手工操作时也写全 `refs/heads/<分支>` |
| **新分支误判为异常** | 第一次 push 新分支，工具说「远端没有这个分支」 | 这是提示不是错误；确认 remote 和分支名没打错再推 |
| **push 被拒后反复重试** | 网络问题导致 ls-remote 失败，以为是分叉 | 看工具输出：网络失败会明确说「跳过检查」，与「分叉阻断」措辞不同 |
| **detached HEAD 状态开工** | 工具报配置错误退出码 2 | 先 `git switch <分支>` 回到正常分支再干活 |

## 实施步骤

1. 开工前（尤其换机器后）：手动跑一次 `python tools/check_sync_state.py`
   看证据表 —— 两个 SHA 一致才开工
2. 装 git 钩子后，push 前自动对撞：`install.py --project <项目> --with-git-hook`
   会装上 `.githooks/pre-push`
3. 被阻断时：`git pull --rebase` → 确认无冲突 → 重新 push
4. 多机接力流程固化：**收工必 push，开工必对撞** —— 工具管 push 侧，开工侧
   靠这条纪律

## 验证

```bash
# 手工对撞（会联网）。不传 --remote 时自动识别远端名
python tools/check_sync_state.py
echo "退出码=$?"   # 0=可继续 1=落后/分叉/无法判定 2=配置错误

# 端到端判定逻辑（不联网）
python tools/check_sync_state.py --selftest    # 应 18/18 全绿
```

## 注意

- 本工具没有 rules JSON —— 判定逻辑是 git 语义，不是项目约定
- remote 名**自动识别**，不写死 `origin`：`branch.<当前分支>.remote` → 仓库里唯一的
  远端 → 兜底 `origin`。远端叫 `gitee` / `upstream` / 中文名都能直接用；显式传
  `--remote` 则完全按传的来
- ahead/behind 数字来自本地追踪分支。它缺失、或与远端此刻真实 SHA 不一致（即过期）
  时，算出的落后量会被低估，把落后的本地读成「领先可推送」。所以这种情况下工具
  不猜：直接判「无法判定」并阻断，让人先 `git fetch` 拿到真实状态
- 它管的是「push 之前不许站在过期历史上」；开工前的手动对撞靠流程纪律，
  工具替不了

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/multi-machine-sync-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `sync-state` | `tools/check_sync_state.py` | ls-remote 现查远端 SHA 与本地 HEAD 对撞，落后/分叉即阻断 | none | 0=状态允许继续 1=落后或分叉需先同步 2=配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `pre-push` | — | `sync-state` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/multi-machine-sync-guard.md 生成，请勿直接修改。 -->
