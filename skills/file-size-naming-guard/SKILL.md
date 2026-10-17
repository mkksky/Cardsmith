---
name: file-size-naming-guard
description: "守住两条最容易随时间塌掉的编码统一性：单文件行数上限，以及文件名命名规范 （Python 模块 snake_case、Java 类 PascalCase、Vue 组件 PascalCase）。 当用户抱怨「这个文件太大了」「怎么拆文件」「命名乱」「有的文件 camelCase 有的 snake_case」「统一一下命名」，或要给项目加文件体积约束、排查某个文件 该不该拆时使用。触发词：文件太大、超长文件、拆分文件、单文件行数、 命名规范、snake_case、PascalCase、命名统一、file too long、naming convention。"
agent_created: true
---

# 文件体积与命名守卫

这两件事放在一个技能里，因为它们同源：**都是「项目跑久了之后一致性先烂掉」的地方**，而且都不需要理解业务语义就能检查，适合交给程序而不是靠人记得。

## 何时使用

- 某个文件已经没人愿意打开，改一处要滚半天
- 团队/自己前后写出的文件命名风格不一致
- 想给项目加体积约束，但不知道阈值定多少
- 准备拆文件，需要先判断「该按什么拆」

## 核心方法

### 行数是信号，不是标准

行数上限不是一个神奇数字。它的作用是**在文件变成没人敢碰之前发出提醒**。

所以超限时的正确反应不是「把长函数拆成三个短函数凑数」，而是回头问一句：

> 这个文件是不是承担了两件事？

拆分的依据永远是**职责**，不是行数。一个 600 行的文件如果只做一件事且内聚，拆开反而更差。工具只能提醒你去看，不能替你判断。

### 命名规范的价值在一致性，不在具体选哪个

`snake_case` 还是 `camelCase` 没有优劣，**混着用才有问题** —— 混用会让你在 import 时反复猜错名字，也会让新代码不知道该跟哪种。

但工具的规则要能改：不同语言的社区惯例不同（Python 模块 snake_case、Java 类 PascalCase、Vue 组件 PascalCase），规则集中在 `rules/*.json`，改一处就行。

### 阈值按类型分档

`rules/file-size-and-naming-guard.json` 里不同扩展名可以给不同上限：

- 文档（`.md`）、配置（`.json` / `.yaml` / `.yml`）：不查，天然就长
- `.js` / `.ts` / `.vue`：**600**，与 ESLint 的 `max-lines: 600` 对齐 —— 两套标准错位最坑人（600 才 warn、400 就硬拦）
- `.java`：800，样板代码多
- 其余类型（`.py`、`.wxss`、`.wxml` 等）：走 `default` **500**

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **只改文件名不改 import** | 重命名后全项目 import 报错 | 重命名后必须跑一次全模块导入扫描，别只看 `git status` 干净 |
| **大小写改名在 Windows 上失败** | 想把 `userService.py` 改成 `UserService.py`，`git status` 显示什么都没变 | Windows 文件系统大小写不敏感。两步走：先改成 `usertmp.py` 提交，再改成 `UserService.py` |
| **把长文件硬拆成碎片** | 拆完文件数翻倍，但每个文件都缺上下文，读代码要在五个文件间跳 | 按职责拆，不按行数拆。拆不出来说明它本来就该是一件事 |
| **阈值定得太紧** | 提交时到处阻断，很快开始 `--no-verify` | 先跑全量摸底看分布，把阈值定在现有代码的 P90 附近，再靠棘轮收紧 |
| **生成代码也被检查** | 自动生成的 client / proto 文件动辄几千行，天天报错 | `skip_paths` 排除 `**/*.generated.*`、`**/vendor/**` 等 |
| **重命名破坏 git 历史** | 改名后 `git log --follow` 跟不到旧历史 | 单独的提交只做重命名，不要夹带内容修改，git 才能识别为 rename |
| **收紧基线时漏了 `--all`** | 跑 `--update-baseline` 没带 `--all`，只登记了暂存文件那几条，其余存量债务被从基线里缩掉 → 下次提交一次性全变「新增违规」阻断 | `--update-baseline` 写入的是**本次扫描到的全部违规**，扫描范围一窄基线就跟着塌。收紧时必须配 `--all` 全量扫描 |

## 实施步骤

1. **先摸底**：`--all` 跑一次，看现有超限文件的分布，不要直接照抄 500
2. 按分布定阈值。超限文件少就修，多就走棘轮（`--update-baseline` 登记基线到 `baselines/<rules 名>.baseline.json`，只阻断新增）
3. 对齐 `rules/*.json` 里的命名规则到你项目的真实惯例（照抄默认值可能全都不匹配）
4. 接入钩子：编辑后提示（不阻断）、提交前阻断

## 验证

```bash
# 摸底
python tools/check_size_naming.py --rules rules/file-size-and-naming-guard.json --all

# 端到端：确认超限真的会被拦
python -c "open('tmp_big.py','w').write('x=1\n'*600)"
python tools/check_size_naming.py --rules rules/file-size-and-naming-guard.json --files tmp_big.py
echo "退出码=$?"   # 应为 1
rm tmp_big.py
```

工具自带用例，改完脚本先跑一遍确认自己没坏：

```bash
python tools/check_size_naming.py --selftest    # 应 9/9 全绿
```

## 注意

- **规则文件的 `skip_paths` 必须落实**，否则生成代码和第三方代码会淹没真实问题
- 命名规则用 `re.fullmatch` 语义，写正则时不要自己加 `^$`
- 阈值调整要和卡片的 `version` 一起改 —— 卡片变了却没升版本，看历史时无法判断行为何时变的

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/file-size-naming-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `size-naming` | `tools/check_size_naming.py` | 检查文件行数与文件名命名规范 | staged-files | 0=通过 1=有违规 2=用法或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `size-naming` | 否 | 10s |
| `pre-commit` | — | `size-naming` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/file-size-naming-guard.md 生成，请勿直接修改。 -->
