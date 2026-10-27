---
name: doc-sync-guard
stage: coding
version: 8
description: >-
  文档同步守卫：抓「代码改了文档没跟上」的漂移。path-ref 查文档引用的文件/目录在磁盘上
  是否真实存在（source_roots 兜底源码不在仓库根的项目，cf_doc_bases 让云函数文档按
  cloudfunctions/<fn>/ 邻域解析）；api-ref 把 API 文档声明的方法/类名与源码 AST 对账。
  提交前会反向并入：暂存的代码（含被删/被改名的路径）反查引用它的白名单文档，
  命中的文档一并校对；可选 new_file_docs 要求新增文件登记到指定文档。
  只查机器能裁断的事实，不判内容对错。当用户
  抱怨「文档过期了」「改了代码忘更新文档」「文档和代码不一致」，或要给项目加文档
  时效性检查、建 API 文档对账时使用。触发词：文档同步、文档漂移、文档过期、README
  失效、API 文档、签名对账、doc sync、stale docs、doc drift。
tools:
  - id: doc-sync
    script: tools/check_doc_sync.py
    args: ["--rules", "rules/doc-sync-guard.json"]
    inputs: staged-files
    baseline: true
    exit: "0=通过 1=有违规 2=规则或配置错误"
    desc: 查文档引用路径、API 符号，以及可选的新增文件文档覆盖
hooks:
  - event: PostToolUse
    matcher: "Write|Edit"
    tools: [doc-sync]
    blocking: false
    timeout: 15
  - event: pre-commit
    tools: [doc-sync]
    blocking: true
rules:
  - id: doc-path-refs
    kind: api-contract
    severity: error
    source: rules/doc-sync-guard.json
  - id: doc-api-refs
    kind: api-contract
    severity: error
    source: rules/doc-sync-guard.json
  - id: doc-new-file-coverage
    kind: api-contract
    severity: error
    source: rules/doc-sync-guard.json
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

引用按 `<文档所在目录> → <项目根> → <source_roots>` 依次解析，命中即通过。第三级是给 monorepo 形态的项目准备的：源码在 `backend/` 子目录下，文档里写 `application/...`（省略前缀）不算漂移，配一条 `source_roots` 就好。**不要为了过检查去改文档加前缀**——文档怎么写是人的事，工具适配文档，不是反过来。

### doc_roots 是白名单，不是扫描器

只查声明了的文档/目录。历史报告、复盘记录、生成文档（`build_reports/` 之类）引用过期路径是天经地义的，扫进来全是噪音。新文档要纳入检查，往 `doc_roots` 加一行。

### 反向并入：改了代码，就把引用它的文档抓回来

只查「被暂存的文档」有个致命盲区：提交里只有代码时，文档检查一声不吭——哪怕文档里那条引用刚刚失效。v1 之前就是这个状态，`scripts/build.py` 改了，`README.md` 里指向它的链接烂掉了，提交照样过。

现在暂存模式会做一次反查：**暂存的代码路径 → 白名单文档里谁引用了它 → 命中的文档一并校对**。匹配是三层：路径全等、目录引用命中其下文件、尾段匹配（兜住文档省略 `source_roots` 前缀或按文档邻域写相对路径的写法）。

暂存清单里**被删除的路径**和**被改名的旧路径**同样算「改动」——文件挪了或没了，文档还指着它，这就是漂移本身。

匹配故意宽松：多并入一份文档只是多一次检查（毫秒级），漏掉才是真漂移。宁可多查不可漏查。

### 新增文件文档覆盖（可选）

反向并入只能发现「文档引用了某个被改路径」。如果新增文件从未被任何文档提到，它不会凭空产生一条命中，因此不能全局硬判所有新文件都该写进文档：测试、临时工具、一次性脚本未必需要。项目可显式声明高价值源码到文档的覆盖关系：

```json
"new_file_docs": [
  {
    "source": ["src/**/*.py"],
    "docs": ["docs/architecture.md"]
  }
]
```

命中 `source` 的 `A`（新增）以及 `R` / `C`（改名 / 复制）目标路径，必须至少被 `docs` 中一份文档通过 path-ref 引用；目录级引用（如 `src/modules/`）覆盖其下文件。`new_file_docs` 默认空数组，不配置时行为完全不变。指定文档不存在、源码未被登记都会作为 `new-file-doc-missing` 违规报出，可走既有基线棘轮。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **文档目录没配白名单** | `--all` 扫出几百条历史报告的过期引用，没法用 | `doc_roots` 白名单制，只加「改代码必须同步」的那几份 |
| **API 文档是散文体** | 签名散落在正文里不像标准格式 | 工具按 code span 抽 `name(...)` 形态，散文体也能抽；抽不到的本来也不该硬查 |
| **裸文件名被当路径** | 散文里「check.py 会报错」被判为根路径缺失，误报刷屏 | 裸文件名（无斜杠）默认不查，`check_bare_names: true` 才开启——首轮真数据验证过的取舍 |
| **首次接入全量阻断** | 提交被存量违规卡死，开始想 `--no-verify` | 先 `--all` 摸底 → `--update-baseline` 登记存量 → 新增才阻断 |
| **代码 span 里的句读** | `见 scripts/run.py。` 这类带句号的引用被误判 | 工具已剥首尾标点；自检覆盖了这个 case |
| **假方法引用** | 文档里举例性的 `get_xxx(...)` 并非真接口 | `skip_symbols` 过滤内置名，项目私有的假名加进 `allow_refs` 或改文档写法 |
| **改了代码，文档没跟着改，提交却过了** | 提交里只有代码，被引用的文档没被暂存，文档检查一声不吭 | 反向并入已修（暂存代码反查引用它的白名单文档并入校对）；仍漏就把该文档加进 `doc_roots` |
| **反查把不相干的文档拖进来** | 每次改代码都并入一堆文档，白名单形同虚设 | 反查只从 `doc_roots` 白名单里找；`doc_roots` 只放「改代码必须同步」的那几份 |
| **新增文件从未被文档引用** | 反查没有目标，检查通过 | 对需要架构索引的目录配置 `new_file_docs`；不要对测试和临时脚本全局开启 |

## 实施步骤

1. `rules/doc-sync-guard.json` 里配 `doc_roots`（白名单）和 `source_roots`（源码前缀，没有就留空）
2. 有 API 指南的，往 `api_docs` 加 `{doc, search_roots}` 映射
3. 需要强制新增源码登记时，往 `new_file_docs` 加 `{source, docs}`；默认留空
4. `--all` 摸底，看真实漂移有多少、误报在哪
5. 误报进 `allow_refs`，真漂移要么修要么 `--update-baseline` 登记债务
6. 接钩子：编辑后提示（不阻断）、提交前阻断

## 验证

```bash
# 摸底
python tools/check_doc_sync.py --rules rules/doc-sync-guard.json --all

# 端到端：确认过期路径真的会被抓
mkdir -p docs && echo "见 \`no/such/file.py\`" > docs/_stale_test.md
python tools/check_doc_sync.py --rules rules/doc-sync-guard.json --files docs/_stale_test.md
echo "退出码=$?"   # 应为 1
rm docs/_stale_test.md

# 端到端：确认反向并入会把引用被删代码的文档抓回来
git rm -q scripts/some_file.py
python tools/check_doc_sync.py --rules rules/doc-sync-guard.json
echo "退出码=$?"   # 引用了它的文档会被并入校对，报漂移 → 1
```

工具自带 47 个用例，改完脚本先跑一遍确认自己没坏：

```bash
python tools/check_doc_sync.py --selftest    # 应 47/47 全绿
```

## 注意

- v1 的 api-ref 只比对**方法名存在性**，不比对参数签名——参数比对误报高，等真实案例驱动再加
- 引用必须落在 code span（反引号）或 markdown 链接里才被抽取，纯正文文字里的路径不查——别指望它读懂散文
- `new_file_docs` 只看暂存状态中的新增路径；普通 `--files` 显式点名不推断“这是新文件”
- 规则文件改动要连着卡片 `version` 一起加一
