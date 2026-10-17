---
name: doc-sync-guard
description: "文档同步守卫：抓「代码改了文档没跟上」的漂移。两个检查都是机器可裁断的事实—— path-ref 查文档里引用的文件/目录在磁盘上是否真实存在（支持 source_roots 兜底， 兼容源码不在仓库根下的项目；查盘前剥掉行号/符号锚点/查询串后缀；前导 / 按项目根 相对解析；无扩展名 pages/ 路由补 .js 探测；cf_doc_bases 让云函数文档按 cloudfunctions/<fn>/ 邻域解析引用）；api-ref 把 API 文档里声明的方法/类名与源码 AST 对账，代码里删掉的方法文档里还在就拦。当用户抱怨「文档过期了」「README 路径 不对」「照文档调接口参数对不上」「改了代码忘更新文档」「文档和代码不一致」， 或要给项目加文档时效性检查、建 API 文档对账时使用。触发词：文档同步、文档漂移、 文档过期、README 失效、API 文档、签名对账、doc sync、stale docs、doc drift。"
agent_created: true
---

# 文档同步守卫

文档漂移是慢性的：当时没人发现，三个月后文档比没有更毒——照着过期文档调接口，参数对不上还以为是自己错。这张卡只抓**机器能裁断的事实**（路径存在性、符号存在性），内容对不对那种判断题明确不管，硬管会误伤。

## 何时使用

- 改了代码（重命名、删方法、挪目录），怀疑文档还停在旧世界
- 文档里的「位置」「详见」链接点进去是 404
- API 指南声明的方法在代码里已经找不到了
- 给项目部署这套检查时的摸底与基线登记

## 核心方法

### 只查事实，不查判断

「文档说的对不对」分两层：**可枚举的事实**（这个文件存在吗？这个方法名还在吗？）和**内容判断**（描述还算准确吗？）。本工具只做前者，因为事实检查零误报、可阻断；判断题交给人。这是这张卡和 lint 类检查的本质区别：文档检查器一旦试图「理解」文档，误报就会逼出 `--no-verify`。

### 路径解析的三级兜底

引用按 `<文档所在目录> → <项目根> → <source_roots>` 依次解析，命中即通过。第三级是给 monorepo 形态的项目准备的：源码在 `okxtradesys.gitee/` 子目录下，文档里写 `application/...`（省略前缀）不算漂移，配一条 `source_roots` 就好。**不要为了过检查去改文档加前缀**——文档怎么写是人的事，工具适配文档，不是反过来。

### doc_roots 是白名单，不是扫描器

只查声明了的文档/目录。历史报告、复盘记录、生成文档（`build_reports/` 之类）引用过期路径是天经地义的，扫进来全是噪音。新文档要纳入检查，往 `doc_roots` 加一行。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **文档目录没配白名单** | `--all` 扫出几百条历史报告的过期引用，没法用 | `doc_roots` 白名单制，只加「改代码必须同步」的那几份 |
| **API 文档是散文体** | 签名散落在正文里不像标准格式 | 工具按 code span 抽 `name(...)` 形态，散文体也能抽；抽不到的本来也不该硬查 |
| **裸文件名被当路径** | 散文里「check.py 会报错」被判为根路径缺失，误报刷屏 | 裸文件名（无斜杠）默认不查，`check_bare_names: true` 才开启——首轮真数据验证过的取舍 |
| **首次接入全量阻断** | 提交被存量违规卡死，开始想 `--no-verify` | 先 `--all` 摸底 → `--update-baseline` 登记存量 → 新增才阻断 |
| **代码 span 里的句读** | `见 scripts/run.py。` 这类带句号的引用被误判 | 工具已剥首尾标点；自检覆盖了这个 case |
| **假方法引用** | 文档里举例性的 `get_xxx(...)` 并非真接口 | `skip_symbols` 过滤内置名，项目私有的假名加进 `allow_refs` 或改文档写法 |

## 实施步骤

1. `rules/doc-sync-guard.json` 里配 `doc_roots`（白名单）和 `source_roots`（源码前缀，没有就留空）
2. 有 API 指南的，往 `api_docs` 加 `{doc, search_roots}` 映射
3. `--all` 摸底，看真实漂移有多少、误报在哪
4. 误报进 `allow_refs`，真漂移要么修要么 `--update-baseline` 登记债务
5. 接钩子：编辑后提示（不阻断）、提交前阻断

## 验证

```bash
# 摸底
python tools/check_doc_sync.py --rules rules/doc-sync-guard.json --all

# 端到端：确认过期路径真的会被抓
mkdir -p docs && echo "见 \`no/such/file.py\`" > docs/_stale_test.md
python tools/check_doc_sync.py --rules rules/doc-sync-guard.json --files docs/_stale_test.md
echo "退出码=$?"   # 应为 1
rm docs/_stale_test.md
```

工具自带 13 个用例，改完脚本先跑一遍确认自己没坏：

```bash
python tools/check_doc_sync.py --selftest    # 应 13/13 全绿
```

## 注意

- v1 的 api-ref 只比对**方法名存在性**，不比对参数签名——参数比对误报高，等真实案例驱动再加
- 引用必须落在 code span（反引号）或 markdown 链接里才被抽取，纯正文文字里的路径不查——别指望它读懂散文
- 规则文件改动要连着卡片 `version` 一起加一

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/doc-sync-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `doc-sync` | `tools/check_doc_sync.py` | 查文档引用的路径是否存在、API 文档声明的方法在源码里是否存在 | staged-files | 0=通过 1=有违规 2=规则或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `doc-sync` | 否 | 15s |
| `pre-commit` | — | `doc-sync` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.cardsmith/` —— 先 `cd .cardsmith` 再照抄命令，或把路径写成 `.cardsmith/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/doc-sync-guard.md 生成，请勿直接修改。 -->
