# 变更记录

版本号的唯一用途是**运行时对账**：`hooks/run.py` 拿项目里
`.codingskills/runtime-state.json` 的 `suite_version`，与套件仓库根 `VERSION` 比对，
不一致就在 stderr 提示重装（机制见 `docs/HOOKS-VERIFY.md`）。

> ⚠️ **改了 `tools/` `rules/` `hooks/` 的行为，就必须 bump 根目录的 `VERSION`。**
> 只改代码不升版本，对账永远报"一致"，项目里跑的还是旧工具 ——
> 0.4.0 从落地到 0.5.0 之间，VERSION 一次都没动过，对账全程形同虚设。

---

## 0.10.9

**`run.py` 加用户级部署守卫，钩子在 WorkBuddy 桌面端真正生效（方案 A 落地）**

- 背景：WorkBuddy 桌面端以 `--setting-sources=user` 启动宿主 CLI，项目级
  `.codebuddy/settings.json` 的钩子被整体屏蔽（根因见 `docs/HOOKS-HOST-GAP.md`）
- `hooks/run.py` 新增 `_belongs_to_project()` 守卫：钩子输入的文件路径不在
  本项目根下 → 退出码 0 静默放行。用户级配置在别的项目里触发时，不会拿
  本项目规则去查别家文件；项目级部署与套件仓库自身行为不变（文件天然在根下）
- 守卫读取 payload 后即刻短路（stdin 只读一次，原 payload 读取点已上移，消除
  二次读取读空导致 `--files` 丢失的隐患）
- 用户级部署方式：把项目级 `.codebuddy/settings.json` 的 `hooks` 块合并进
  `~/.workbuddy/settings.json`（改前备份，该文件为程序管理文件）
- 实测：selftest 6/6；外项目路径 → 静默放行；本项目违规文件（staged）→
  `debug-smells` 退出码 2 并回填违规详情
- 已知限制：用户级 settings 由宿主在会话/宿主启动时读取，改完需新会话生效；
  其余项目的每次 Write/Edit 会多出约 10 次 python 进程冷启动（守卫下均秒退）

---

## 0.10.8

**新增 `config-guard` / `artifact-guard`，分层守卫补禁止依赖与禁止注解**

- 新增 `config-guard`：正则匹配 Spring Boot `application*.yml` 的机器可判定危险组合。
  裸 `*` 通配 CORS 阻断；明文密码默认值、JWT 默认密钥、Flyway 与 `ddl-auto` 并存、
  关闭迁移校验走提示档，避免存量配置第一天就把提交逼进 `--no-verify`
- 新增 `artifact-guard`：只拦本次新增入库的构建产物（`.venv/`、`dist/`、`target/`、
  `coverage/`、`*.log`、`tmp/`）；已跟踪的存量产物与前端产物目录落后源码超过阈值
  只提示，存量走债务、新增才阻断
- `layered-architecture-guard` 规则升 v2：新增 `forbidden_dependencies` /
  `forbidden_annotations`（Controller 直连 Repository、Controller 上的
  `@Transactional` 均走提示档），补 Spring 三层默认层序与静态资源 skip_paths
- 新增工具自检：`check_config_guard.py` 9 项、`check_artifact_guard.py` 4 项
- 接入 pre-commit 阻断与编辑后提示；`VERSION 0.10.7 → 0.10.8`

## 0.10.7

**新增 `module-contract-guard`：检查消费方请求的 CommonJS 导出是否真实存在**

- 起因：某小程序统一工具入口定义了 `exportObject`，却漏写
  `module.exports = exportObject`。lint、单测和既有死代码守卫全绿，线上详情页
  直到运行时才因解构得到 `undefined` 而崩溃
- 新增独立守卫 `module-contracts`：扫描 `const { A } = require('./mod')`，
  解析目标模块的 `module.exports = {...}`、`exports.A =`、对象变量导出、
  再导出与静态 spread；目标缺少请求的源导出名即阻断
- 始终从全仓消费者构建契约关系，staged 模式只过滤报告范围：改生产模块会复查
  未暂存的旧消费者，改消费者也会检查未暂存的目标模块
- 动态导出保守跳过：`Object.assign(exports, ...)`、工厂返回值、计算属性或导出
  对象后续动态补属性都标记为不可完整判定，宁漏报不误报
- 新增卡内自检 18 项，覆盖漏写 `module.exports`、别名解构、对象变量、再导出、
  静态/动态 spread、目录 index、规则别名、注释/字符串/正则字面量误匹配与暂存过滤
- 卡带基线：键为 `消费者:目标:导出名#missing-export`，老项目可登记存量，
  后续只允许收紧；接入 pre-commit 阻断与编辑后提示
- 工具行为新增 **VERSION 0.10.6 → 0.10.7**

## 0.10.6

**新增 `ahk-v2-guard`：拦截照 v1 记忆写 AutoHotkey v2**

- 新增 `ahk-v2` 检查工具，覆盖缺 `#Requires AutoHotkey v2.0`、v2 已移除/改名的
  内置变量、单引号串未闭合、单引号包双引号后的 auto-concat 缺空格、v1 裸赋值
  与命令式语法
- 不尝试完整语法解析：只保留字面层面可裁断、且能明确修复的规则；合法 v2 写法
  保守放行，避免门禁误报诱发 `--no-verify`
- 重点覆盖 `Run('"' path '" --src1')` 这类合法写法与漏引号写法的差异；
  同时排除注释、字符串内分号、v2 多变量赋值、数组与函数调用逗号等合法场景
- 新增 21 项自检，包含真实 `Missing "'"` 故障复现、v1 命令式语法与典型合法
  v2 片段
- 顺带修正 `doc-sync` 默认白名单：允许文档引用运行时 `.workbuddy/` 与
  `.git/COMMIT_EDITMSG`，避免干净 worktree 的自身提交门禁误红；
  `doc-sync-guard` 卡片 7 → 8
- 接入编辑后提示与 pre-commit 阻断；`VERSION 0.10.5 → 0.10.6`

## 0.10.5

**`format-lint` 分批执行：Windows 全量扫描不再被命令行长度上限打回「未安装」**

- 起因：`--all` 全量扫描时把全部文件一次性塞进单条 argv，在 Windows 上超过
  CreateProcess 的命令行长度上限（约 32767），直接抛 `[WinError 206] 文件名或扩展名太长`。
  工具根本没跑起来，但旧代码把 `OSError` 并进 `skipped`，输出成「本机未安装，已跳过」——
  ruff 明明装着，检查却被静默跳过，交付体检的 format-lint 项变黄
- 新增 `chunk_paths()` 按 `ARG_BUDGET = 16000`（上限留足余量）把文件列表切批，
  逐批调用；任何一批报告违规即记违规并停止该工具
- 执行失败与未安装**分开统计**：`missing`（`shutil.which` 找不到）仍走 SKIP 退出码 0；
  `exec_failed`（超时 / OSError）与违规同等对待，退出码 1。不再让「跑不起来」伪装成「没装」
- `--selftest` 增加分批用例：5000 个长路径文件 → 分批后一个不丢、每批估算长度均不超预算
- 卡片 `version` 1 → 2（`exit` 语义补「执行失败」，常见坑新增 WinError 206 一行）；
  工具行为变化 **VERSION 0.10.4 → 0.10.5**

## 0.10.4

**`doc-sync-guard` 新增可选 `new_file_docs`：新增源码必须有文档落点**

- 起因：代码拆分新增模块后，项目文档仍只描述原入口文件，旧文档却没有任何失效路径，
  提交门禁因此全绿。反向并入只能发现「文档引用了被修改/删除的路径」；一个从未被任何
  文档提到的新文件没有反查目标，这是机械事实检查的真实盲区
- 规则新增 `new_file_docs: [{source, docs}]`：匹配 `source` glob 的 `A`（新增）以及
  `R` / `C`（改名 / 复制）目标路径，必须至少被 `docs` 中一份文档通过 path-ref 引用；
  目录引用覆盖其下文件。指定文档不存在或新增文件未登记会报
  `new-file-doc-missing`，可走既有基线棘轮
- **默认 `new_file_docs: []`，不配置时行为完全不变**。不做「所有新文件必须进文档」的
  全局规则：测试、临时脚本和一次性工具会制造大量误报，项目应只对需要架构索引的高价值
  源码目录显式开启
- `git diff --name-status` 状态现在保留在内部记录中，同时兼容旧的路径列表调用；
  `--files` 不推断新增状态，避免把手工点名误当成 git 的 `A`
- 新增独立自检 `doc_sync_new_file_selftest.py`，覆盖已登记 / 未登记 / 目录引用 /
  source 未匹配 / 文档缺失 / 非法配置 / A+R 状态提取，以及 staged 编排的
  新增触发 / 既有文件不误报；主自检从 37 项扩到 47 项
- 顺带修正 `glob_to_regex()` 的中间 `**/` 语义：`src/**/*.py` 此前要求至少一层
  子目录，会漏掉 `src/x.py`；现在任意位置的 `**/` 都可匹配零层目录，并补了边界用例
- 卡片 `version` 6 → 7；规范与 README 同步，工具行为变化 **VERSION 0.10.3 → 0.10.4**

## 0.10.3

**受保护路径守卫区分「新增」与「改动」：新增 `allow_new`，默认行为不变**

- 起因：某项目把 `protected_paths` 对齐到真实 Flyway 目录后实测发现，**新建**的
  迁移脚本也被拦了（`exit=1`），而拦截文案写的恰恰是「要变更请走新的迁移脚本」——
  自相矛盾。原因：`staged_files()` 用 `git diff --cached --name-only`，
  为了不漏掉删除（D）而没有加 `--diff-filter`，顺带把新增（A）也收了进来
- 后果不是「更严格」，而是把门禁废掉：迁移目录的正常工作流就是不断追加脚本，
  每次都被拦 → 所有人 `--no-verify` → 这道闸变成「已经检查过」的错觉，
  比没有守卫更糟
- 改用 `git diff --cached --name-status --no-renames`（改名拆成 D + A，D 那半仍拦），
  规则新增 `allow_new` 字段：
  - `false`（默认）= 新增也拦，**与之前行为完全一致，现有项目零影响**
  - `true` = 放行 `A`，`M` / `D` / `R` 照拦
- `--files` 显式点名时不看状态、一律按违规处理 —— 点名检查就是要看它算不算违规
- 违规输出加状态字母（`[M] path` / `[D] path`），一眼看出是改还是删
- 自检从 9 项扩到 14 项，其中 5 项专守 `allow_new` 边界，含「状态缺失 → 保守拦下」
- 默认放宽比默认收紧危险，所以做成 opt-in：要不要放宽是项目自己的约定，
  工具不替人决定。卡片里补了「新增和改动是两件事」一节，说明这条分界

**分层守卫原生支持 `project_packages`：第三方库名带层名不再被误判**

- 同一轮实测暴露的第二处：JPA 项目里 `import jakarta.persistence.Entity` 被判成
  「domain(rank 0) → infrastructure(rank 1)」，一次 253 条，全是假的。
  原因：模块名 `jakarta/persistence/Entity` 命中了 `**/persistence/**`
- 有的项目在本地改过这个工具来规避。但安装器对 `tools/` 是**整体覆盖**的
  （`rules/` 才是「已有保留」）—— 重装一次定制就没了，规则里的 `project_packages`
  成为没人读的死字段，而工具又不会报错（多余字段被静默忽略），
  表现就是「重装完突然冒出几百条假警报」，且看不出跟配置有关
- 现在上游原生支持：`project_packages: ["com.congge"]` 只对自身前缀的 import 判层。
  **留空 = 不过滤，行为与之前完全一致**，没配的项目零影响
- 与 `allow_new` 同一取向：默认行为不变，放宽靠显式配置。自检加了一条守这个边界

## 0.10.2

**安装时给实现了棘轮的工具预置空基线，关掉「首建豁免」**

- 起因是一个项目实测发现的：零债务时 `.codingskills/baselines/` 根本不存在。
  两个后果 —— 一是文档没法引用这个路径（`doc-sync` 会判「路径不存在」），只能靠
  文字绕开；二是 `--update-baseline` 走 `first_build` 分支、**绕过棘轮闸门**，
  谁顺手跑一次，就能把当时扫到的违规全部静默登记成债务
- 空基线（`entries: []`）存在时，同一条命令会被闸门拒绝「本次会新增 N 条债务」，
  必须 `--accept-debt --debt-reason` 才算认账。卡片里本来就写着「首次接入也要
  显式认账」，是 `first_build` 分支让实现和文档对不上 —— 这次把实现拉回来
- 安装逻辑：`install_runtime()` 按卡片声明的 `tools[].baseline == true` 取规则名，
  给 `baselines/<规则名>.baseline.json` 补空文件；**已有的文件一律不碰**
  （基线是项目的债务清单，重装覆盖等于替项目认账或销账）
- 写入复用 `tools/baseline_store.py` 的 `apply_ratchet()`，不在 `install.py` 里
  手抄一份 JSON 模板 —— 抄一份就漂一份，正是本套件反复在修的病
- 坏基线（0 字节、非法 JSON）会被点名而不是静默放过：这类文件会让检查工具以
  退出码 2 失败，比没有基线更糟
- 顺带补上 `stale-import-guard` 卡片缺失的 `baseline: true` 声明：工具早就支持
  `--update-baseline`，卡片没声明，导致运行时 README 的基线清单只列 8 个、
  实际跑出 9 个（卡片 `version` 2 → 3）
- 验证：函数级四场景全过（dry 不写盘 / 真实写入 9 个且 `entries` 为空带 `initial` /
  重装不覆盖已有 / 0 字节被点名）；`build.py --check` 与 `check.py` 通过
- 行为变化：**新装项目首次 `--update-baseline` 必须带 `--accept-debt`**。
  已经在用基线的项目不受影响（文件已存在，只提示保留）
- 改的是安装行为而不是 `tools/` `rules/` `hooks/` 的检查逻辑，但项目里跑的还是
  旧安装器 → 仍 **bump VERSION 到 0.10.2**

## 0.10.1

**`size-naming` 不再对二进制资产数行数**

- 起因是真实误报：提交一份 804 KB 的 `.pptx` 使用说明，被判「文件 1531 行，
  超过上限 500 行（超出 1031 行）」，并按卡片要求提示「超长文件不是靠拆函数
  解决，而是先问这个文件是不是承担了两件事」—— 而 pptx 是 zip 容器，那个
  1531 只是压缩字节里 `0x0A` 出现的次数，不是行数
- `count_lines()` 数的一直是原始字节里的 `\n`。行数上限的整个前提是「行数能
  映射到职责」（一个 800 行文件可能是两个类挤在一起）；二进制资产没有函数、
  没有职责、也不可拆，前提不成立
- 判定放在**工具层**而不是规则层：已知扩展名（pptx/docx/xlsx/pdf/zip/图片/
  字体/jar/音视频等）先短路、不读盘；其余走 git 的 NUL 判据嗅探前 8 KiB。
  任何项目装上即生效，不必在每个 `rules/*.json` 里逐个登记 —— 否则换个项目
  就得重新踩一次，这类「配置里少写一条就静默失效」的坑正是本套件要消灭的
- 已知边界：UTF-16 编码的文本含 NUL，会被判成二进制而不查行数。源码里极少，
  且 git 同样视其为二进制，与生态保持一致，不做特例
- 修在 `tools/` 而非 `rules/` → **bump VERSION 到 0.10.1**
- 自检补一例「二进制不数行数、文本文件照旧检查」（9 → 10 项），卡片 `version` 8 → 9
- 自检按套件惯例拆到 `tools/check_size_naming_selftest.py`（同 `check_dead_code`
  的做法），`--selftest` 与独立入口都能跑 —— 内联 + ruff-format 展开后本文件
  564 行，会撞上套件自己要求的 `.py` 500 行上限
- 顺带 `ruff format` 对齐这个文件的既有手工排版：88/96/100/112/120/130/160 各档
  行宽下 ruff 都判「would reformat」，说明不是行宽差异，是换行位置差异 —— 它一直
  过不了自家的 format 门禁。仓库里较新的文件（`check_dead_code.py`、
  `dead_code_collectors.py`）本就是 ruff 格式，这次把这份也对齐
- 验证：真实 `.pptx` + 上游默认规则（不写任何二进制豁免）→ 通过；同目录文本
  文件仍按上限报出，说明不是把「行数检查」整体关掉了

---

**`check.py` 新增第 17 项对账：README 的守卫数量与版本号**

- 起因是自查发现的真实漂移：README 手写、不被 `build.py` 覆盖，0.7.1 之后
  0.8 / 0.9 / 0.10 三个版本新增了 `perf-path-guard` 与 `dead-code-guard`，
  首页却一直停在「11 个守卫」「当前版本：0.7.1」—— 这套专防漂移的工具，
  自己最显眼的那份文档漂了三个版本
- 对账三项机器可裁断的事实：`## N 个守卫` 的 N 与卡片数、守卫表名字集合与
  `cards/` 双向比对（缺卡片 / 幽灵卡片都报）、`当前版本` 与根 `VERSION`
- 不检测措辞与正文 —— 那本来就不该由机器裁断
- 反向分支单独验证过：数量写错、版本写错、多一条幽灵卡片三项同时命中，
  对照组全绿，退出码从 1 回到 0
- **这一条本身不动 `VERSION`**：`scripts/` 是套件自身工具链，不随技能分发，
  bump 只会让已装项目对着一个没变的工具链提示重装（本轮随 `tools/` 的二进制
  修复一并发布为 0.10.1 —— 那处是工具缺陷，见上）

---

## 0.10.0

**新增 `dead-code-guard`：死代码守卫（失效导入 + 零引用导出）**

- 抓两类机器可裁断的事实：
  - **unused-import** —— `require` 绑定名字后全文再没出现（把绑定声明 span
    挖掉后检索），运行时不报错但白加载
  - **dead-export** —— 具名导出在全仓库（含测试）零文本引用，删了不影响任何行为。
    收 `module.exports = {...}`（花括号配对 + 顶层逗号切分）、`exports.name =`、
    `module.exports = Class` 三种形态，方法简写 `gone() {}` / `async gone()` /
    `get foo()` 也识别
- **引用计数只取保守方向**：字符串字面量里出现名字也算引用、测试里的引用也算引用
  —— 宁漏报不误报，误报的门禁会逼出 `--no-verify`
- **引用语料必须全量**：`--files` 只裁剪被检查的文件，语料永远全仓库 ——
  只看暂存文件会把「A 删了引用、B 的导出因此变死」漏成绿灯
- **性能是设计约束不是事后优化**：引用计数用「符号 → 文件集合」倒排索引，
  一次分词 O(N) 构建全库共享；初版逐目标重建正则的 O(N²) 写法在千文件仓库
  要 4 分钟以上，重写后 8 秒完成。门禁慢到没人等就形同虚设
- 防误报三道闸：被 `...require('./x')` 展开继承的文件整文件跳过导出检查；
  转口链（index.js 转出口）v1 明确不拦；动态拼名访问接受漏报
- 解析层拆在 `tools/dead_code_collectors.py`（纯函数无 IO），工具本体
  407 行、解析层 190 行，都在套件 `.py` 500 行门禁内
- 自检按套件惯例拆成 `tools/check_dead_code_selftest.py`（16 用例，`--selftest` 与独立入口都能跑）；首个真实项目摸底 308 处存量（210 零引用导出 +
  98 失效导入）走棘轮基线登记，之后只拦新增
- v1 范围：CJS `require` + `module.exports`/`exports.x`（ESM 语法不在范围，
  需要时按卡片 version 加一升级）

---

## 0.9.1

**性能提示档支持显式豁免，与待修基线彻底分开**

- 规则文件新增可选 `exemptions`。每项必须登记 `file`、`pattern`、`reason`；
  同一文件有多段循环时可用 `match` 正则只放行经核验的循环头，避免整文件宽放行
- 基线继续表示「待处理债务」，豁免表示「已确认合理、无需修改」；
  两者不再混用，`--update-baseline` 也只登记未被豁免的真实命中
- 新增 `tools/perf_exemptions.py` 与两项回归用例：精确片段过滤、缺少理由必须报配置错误
- 适配真实项目首轮体检：重试、分页游标、批处理限流、并发压缩等合理串行可留痕豁免，
  不再逼维护者用基线把债务永久冻结

---

## 0.9.0

**新增 `perf-path-guard`：加载链路性能守卫（首张双档位卡片）**

- **为什么拆两档**：这是本版最重要的决定，而且是被实测逼出来的。
  `await-in-loop`（循环里 await）在一个真实小程序项目上 `--all` 跑出 66 处命中，
  抽样核验后发现**多数是正当的** —— 重试循环（`for (attempt=0; attempt<=retry; attempt++)`
  必须串行，下一次尝试本来就要等上一次失败）、逐张图片压缩（并发会顶爆小程序内存）、
  分页批处理（下一页依赖上一页游标）。
  「这次循环的下一轮要不要依赖上一轮的返回值」这个信息**在语法层面不存在**。
  按 0.7.1 定下的判据（新增正则类守卫误报过三成不能当 blocking），
  它不能进阻断层 —— 否则会把人逼出 `--no-verify`，比没有门禁更糟
- 因此一张卡片注册两个 tool，各挂一份规则、各自一套基线：
  - `perf-smells` → 阻断档。`wx:key` 用下标、`wx:for` 缺 key、循环内 `setData`、
    `forEach` 套 `async`、`await new Promise(r => setTimeout(r, N))`。
    这五类**没有正当例外**，命中即错
  - `perf-serial-advisory` → 提示档。循环内 `await`、请求路径上的 `execSync`。
    有正当例外，只提醒不阻断（`blocking: false`），判定交给人
- **`*this` 差点被误伤**：初版规则把 `wx:key="*this"` 也当坏 key（和 `index` 一起拦）。
  实测该项目 73 处 key 违规里有 **37 处是 `*this`**，而它对**字符串/数字数组是唯一正确写法**
  —— 等于一半全是误报。已从正则里摘掉，并补了「字符串数组用 *this → 通过」回归用例
- 新增 `tools/check_perf_smells.py`（22 用例自检，用例在 `tools/check_perf_smells_selftest.py`）。
  两个内置检测器按块判定，
  不用正则硬凑：`await-in-loop` / `setdata-in-loop` 靠**大括号配对**（JS）与**缩进**
  （Python）圈出循环体，`wx-for-no-key` 按**标签**判定（`wx:for` 与 `wx:key` 必须同标签）
- **wxml 单独做遮罩**：不能复用 `mask_js_strings_and_comments` —— 它连双引号内容一起抹，
  而 wxml 的属性值全在双引号里，`wx:key="index"` 的 `index` 会被抹成空格，
  检查**静默全绿**。这类失效比报错贵得多
- **工具自己撞了自己的体积门禁**：`check_perf_smells.py` 内联 20 个用例后 577 行，
  超过套件对 `.py` 的 500 行上限（`.codingskills` 目录不在体积门禁的 skip 列表里）。
  按套件既有先例（`scripts/baseline_ratchet_selftest.py`）把用例拆成
  `tools/check_perf_smells_selftest.py`，工具本体回到 497 行，两个入口都能跑
- 两份规则默认排除了 `**/.codingskills/**`：门禁运行时的代码不该被业务门禁扫描。
  反过来也要注意 —— 改完 skip 再 `--update-baseline` 会被**范围守卫拒绝**
  （旧键所属文件已不扫，不能被当成「已修复」销掉），新建的基线删掉重建即可
- 卡片正文新增「守不住的」一节，明确列出四条真问题但不做门禁：
  两个 await 能否并发、接口是否被重复调用、图片该不该裁剪、缓存该不该加 ——
  判定信息都不在代码表面。写进 review checklist，不往门禁里塞

---

## 0.8.0

**基线棘轮加方向闸门：`--update-baseline` 从此默认只降不升**

- **问题**：棘轮的死法不是没人跑 `--update-baseline`，是**有人跑得太顺手**。
  提交被拦 → 跑一次 `--update-baseline` → 全绿。diff 里看着像「顺手维护基线」，
  实际把这次的新增违规一起赦免了。几个月后债务翻十倍，
  基线文件里没有任何痕迹说明哪笔是哪次进来的。
  这不是假想：`tim-schipper` 把它列为 ratchet 腐化的第一号方式
  （"看起来像维护"），`issue-orchestrator#6671` 为此单开了一条 P1 工单
- `tools/baseline_store.py` 新增 `apply_ratchet()`，取代裸 `write()`：
  默认只允许收紧；本次会新增键则**拒绝**（`RatchetError`，退出码 1）
  并逐条列出新增的键。只说「有新增」等于没报 —— 报错必须能直接当行动清单用
- **首建基线不受闸门约束**：文件原本不存在时，「全部是新增」是登记存量的正常语义。
  不放开这条，接老项目的第一步永远做不成。首建会写 `initial` 段留痕
- **显式逃生口**：`--accept-debt` + `--debt-reason "..."`。
  理由必填（没理由的认账在 review 里等于没记录），
  理由 + 日期 + 涉及的键写进基线文件的 `accepted` 段，**只追加不覆盖**
  —— 每笔认账都要留下痕迹，用当天日期而非文件 mtime（mtime 会被 git checkout 改掉）
- **六个工具同步生效**：size-naming / import-direction / doc-sync / secrets /
  debug-smells / stale-imports。闸门做在 `baseline_store` 模块层而不是逐工具复制
- 基线文件 `version` 1 → 2。**向后兼容**：`load()` 只读 `entries` 键，与 version 无关，
  v1 基线直接可用；`_load_accepted()` 在无 `accepted` 段时返回空列表。
  已在某小程序项目现存的 4 个 v1 基线上实测通过（doc-sync / file-size /
  root-cause / stale-import），无需迁移脚本
- 新增 `scripts/baseline_ratchet_selftest.py`（23 用例）。单独成文件而不是塞进
  某个工具的 `--selftest`：闸门横跨六层，塞进任一工具，另外五个的回归就没人跑了
- **范围守卫（顺带抓出一个既有缺陷）**：`apply_ratchet()` 接受 `scanned` 参数，
  本次扫描没覆盖到的文件，其存量债务**不允许**被当成「已修复」销掉。
  实测（某小程序项目，`--all --update-baseline` 跑 file-size 守卫）发现：
  `pages/client/detail.wxss` 有 **1902 行、远超 500 行上限**，
  但 `.wxss` 压根不在 `rules/*.json` 的 `file_lines.overrides` 键空间里 →
  `--all` 永远扫不到 `.wxss`（`all_files` 收集到 18650 个文件，wxss 类 **0 个**）
  → 基线里那条 `detail.wxss:file-lines` 在收紧时被**静默删掉**。
  这比「新增违规」更糟：风险从「多一处欠债」变成「超限文件不再被记为欠债」，
  而门禁显示全绿。已用 `git checkout` 恢复该基线，未让损坏落盘
- 范围守卫优先于新增闸门：两者同时成立时报更根本的那个
- **修复**：收紧时（只减不增）现在会报出**具体销掉了哪几条**。
  以前只报总数，「少了很多」和「少了一条但那一条很重要」看起来一模一样
- `write()` 保留但注释标明**只给自检与迁移脚本用**：留着它是因为自检需要无闸门地
  构造前置状态，但检查工具一旦开始调它就等于开了绕过 `apply_ratchet()` 的后门 ——
  棘轮的价值全在于「没有第二条路能新增债务」
- 卡片同步：`file-size-naming-guard` 补「顺手 update-baseline 把新增一起赦免」这条坑
  与「棘轮只降不升」小节（v6→v7）；`layered-architecture-guard` /
  `doc-sync-guard` / `secrets-guard` / `root-cause-protocol` / `stale-import-guard`
  各自的 version 各 +1

### 范围守卫为什么必须做

`--all` 「全量扫描」这个说法本身有前提：**规则文件的键空间覆盖了你关心的类型**。
`all_files()` 的扩展名来源是 `naming` 与 `file_lines.overrides` 两个键空间的并集 ——
任何没在这两处出现的扩展名，一次 `--all` 都扫不到，且表现为「检查全绿」。
配上 `--update-baseline` 的写入语义（写入本次扫描到的全部违规），
就构成了一个静默的数据损坏路径：扫不到 → 不违规 → 基线里删掉 → 债务消失。

这类缺陷的性质和「阈值定得太紧」正好相反：
阈值问题会**报错**（吵），盲区问题**不报错**（安静地失效，更贵）。

---

### 附带修两处（都是本次提交时被自家 pre-commit 拦出来的）

**1. `check_doc_sync.py` 510 行 → 433 行（按职责拆，非按函数拆）**

size-naming 门禁拦下超限 10 行。处置方式是把「引用抽取」整段拆到
`tools/doc_sync_refs.py`：那一段是纯函数 + 正则常量，不碰磁盘、不读规则结构，
职责单一（文本进、候选引用出），与主文件的 I/O / 基线 / 报告输出互不依赖 ——
是天然分界。五个正则常量随之搬走，主文件不再定义它们。
顺带的好处：这里的取舍（裸文件名默认不查、code span 才算引用）
现在能被单独 review，混在 500 行里容易被略过。

**2. `debug-smells` 的 JS 坏味道正则不再命中字符串字面量**

拦下两条 `empty-catch`，核查确认**是误报**：来源是 `check_debug_smells.py` 自己的
自检用例 —— `case("JS 空 catch", {"bad.js": "try { f() } catch (e) {}"}, 1)`
这行把一条完整空 catch 写进了 .py 文件的字符串里，扫全文必然命中。

`import_collectors.py` 新增 `mask_js_strings_and_comments()`：
在 `mask_js_comments()` 之上**额外把字符串内容也替换成空格**（引号保留、
换行保留）。两者分工明确 —— 前者只让解析器跳过字符串（内容原样保留，
因为收集 import 时正则靠引号定位，抹掉就找不到），后者给「用正则找坏味道」
的调用方用。轻量词法扫描单双引号 + 反引号三态，Python 单/双引号同样覆盖，
所以 `check_debug_smells` 不按扩展名分流，统一屏蔽。

**误报不用登记基线赦免** —— 基线是给真违规留的出口，拿它盖扫描器缺陷
等于让守卫永久失效。三个新用例锁住边界：字符串里的样本不算、注释里的不算、
**真实空 catch 仍要拦**（屏蔽不能造成漏报）。

> 附带发现：写这段函数的 docstring 时，我在说明里举了「空 catch 的完整写法」作为例子，
> 结果缩进代码块在词法上就是真实代码，又被自家 debug-smells 拦下（真阳性）。
> 文档在演示坏味道也是在写坏味道 —— 说明里改成描述性表述。

---

## 0.7.1

**修 stale-import-guard 的注释误报：JS/TS 导入收集先剥注释**

- `tools/import_collectors.py` 新增 `mask_js_comments()`，
  `collect_javascript()` 在跑正则前把注释内容替换成空格。
  换行逐字保留 —— 调用方靠 `src.count("\n", 0, m.start())` 算行号，
  抹掉换行会让所有报错行号错位
- **实测数据**（某小程序项目首次接入 0.7.0）：11 处报失效导入里 **7 处是误报**，
  真断链只有 4 处。误报源不是边角料，是最常见的代码形态 ——
  「修复记录」注释：`// require('../../../services/x')`、
  `/* 原实现: require('../../common/y') */`。
  blocking 守卫误报率过六成，第一次提交就会被当成噪声绕过去，
  比没有这个守卫更糟（它会给人「已检查过」的错觉）
- 剥法是轻量词法扫描，只区分字符串 / 行注释 / 块注释：
  ・字符串里的 `//`、`/*` 不误伤 —— 这正是不能直接用正则剥注释的原因，
    `const url = 'https://a.com/x'` 会被吃掉后半行
  ・普通字符串不跨行，遇换行强制退字符串态，一处未闭合引号不会吞掉后半文件
  ・正则字面量不特殊处理：JS 正则里的 `//` 必须写成 `\/`，
    写法上就不存在裸 `//`
- 新增 `scripts/import_collectors_selftest.py`（14 个用例）锁住
  「注释不算导入」与「行号对齐」两条底线。收集顺序也是契约：
  先 `JS_BARE_RES` 组、后 `JS_FROM_RE` 组，组内按出现位置
- 对 `check_import_direction.py` 的 JS 侧同样生效（同一收集器），
  属附带修复：以前注释里的跨层 require 也会被拿去判方向

---

## 0.7.0

**新增 stale-import-guard：失效导入路径检查（Python + JS/TS/Vue）**

- 新增 `tools/check_stale_imports.py` + `cards/stale-import-guard.md` +
  默认规则 `rules/stale-import-guard.json`。抓「模块被搬迁/改名后引用
  没跟上」的失效导入 —— 分层方向检查只看方向、文档同步检查只看文档，
  「目标模块是否存在」此前是盲区
- **Python** 走 AST 收集（含函数体延迟导入）：绝对导入按 packages 白名单
  校验，相对导入按 level 上溯还原后校验；解析优先级对齐 CPython
  FileFinder（常规包 > 模块 > 命名空间目录）—— 同名包/模块共存时判反
  会把合法导入误报成失效，真实案例见卡片「常见坑」
- **JS/TS/Vue** 用正则收集（零依赖）：只查 `./` `../` 相对导入与规则配置
  的构建别名（`javascript.aliases`）；裸说明符（第三方包 / Node 内建）
  天然跳过。`tools/import_collectors.py` 新增 `collect_javascript()`
- 接入 pre-commit（blocking）与 PostToolUse（提示）；基线键 = 文件:模块，
  与 import-direction 同一棘轮约定
- 规则向后兼容：旧项目已有的 `packages` 顶层键照常工作；`javascript`
  段缺省 = 不查 JS

---

## 0.6.0

**放宽 `file-size-and-naming-guard` 的 JS / TS / Vue 行数上限：400 → 600**

- `rules/file-size-and-naming-guard.json`：`*.js` / `*.ts` / `*.vue` 由 400 提到 600。
  400 对「一个页面的全部交互」这类文件偏紧 —— 某小程序项目项目里 `services/client.js`、
  `pages/*/detail.js` 天然在 500 上下，400 会逼着为过门禁而拆出缺上下文的碎片。
  600 与该项目 ESLint 的 `max-lines: 600` 取同一档，消除「600 才 warn、400 就硬拦」
  的错位：错位的阈值比没有阈值更容易让人误判门禁的真实意图
- `default` **保持 500**（`.py` / `.wxss` / `.wxml` 等仍按 500）；`*.java` 800 不变
- 卡片 `version` 5 → 6，阈值描述改为按类型分档的准确表述
- 卡片「常见坑」补一条：`--update-baseline` 必须配 `--all` ——
  它写入的是**本次扫描到的全部违规**，只扫暂存文件会把基线缩塌，
  其余存量债务到下次提交时一次性全变「新增违规」

> ⚠️ **对已安装项目不自动生效**：`install.py` 对 `rules/` 的策略是
> 「已有的保留，只补套件新增的」（阈值与 `skip_paths` 归项目所有）。
> 项目要切到新阈值，需手动同步该 JSON，再跑
> `python .codingskills/tools/check_size_naming.py --rules .codingskills/rules/file-size-and-naming-guard.json --all --update-baseline`
> 收紧棘轮（`--all` 不能省）。

---

## 0.5.1

**回写自某小程序项目项目的两处修复**

- 读暂存文件的工具统一改用 `git -c core.quotePath=false`（`check_doc_sync` /
  `check_size_naming` / `check_debug_smells` / `check_format_lint` /
  `check_import_direction` / `check_protected_paths`）—— 默认会把中文路径转义成
  `"\345\260..."` 字面串，路径解析不到文件，检查**静默通过**
- doc-sync 守卫新增 `allow_ref_patterns` 形态白名单（正则 `fullmatch`），放行
  「长得像路径但不是路径」的项目固定写法：价格单位（`元/㎡`、`万元/亩`）、
  纯数字比（`28/24/19`）、不入库的本地证据目录（`.local-evidence/`）。
  在 `load_rules` 加载期编译，坏正则按配置错误（退出码 2）处理，
  不拖到检查时才崩；自检用例补至 20 条
- secrets-guard 的 `--all` 摸底改为尊重 `.gitignore`
  （`git ls-files --cached --others --exclude-standard`）—— 原先 `rglob` 会把
  `.env` / `.env.local` 这类**永远进不了 git** 的本地文件判成「敏感文件被暂存」
  而 FAIL；而这些文件各机器本地存在与否不同，同一仓库在两台机器上会得出
  相反的体检结论

## 0.5.0

**doc-sync 守卫：引用解析增强**

- 路径候选拼装、后缀剥离抽成独立模块 `tools/doc_sync_paths.py`（本版新增）
- 行号 / 符号锚点 / 查询串后缀剥掉后，按纯路径再查一次
- `pages/` 下无扩展名路由补 `.js` 探测
- `cf_doc_bases` 支持云函数文档的邻域解析，含 monorepo 布局下按 `source_root` 拼接
- 自检用例补齐至 18 条

**sync-state 守卫：远端对账更硬**

- 远端名自动识别，不再要求显式传 `--remote`
- 追踪分支缺失或过期时判 `unknown` 并**阻断** —— 宁可拦下，也不在过期历史上开工

**release-checklist（新增）**

- 一个命令汇总全部守卫的 `--all` 结果；清单从 `hooks/registry.json` 动态取，
  后续新增守卫自动纳入，不用改本工具

**secrets-guard（新增）**

- 私钥块 / 云厂商 token / `.env` 类文件本身被暂存 → 高危阻断
- 敏感变量名 + 密钥形态的赋值 → 可疑，仅提醒不拦截
- 报告打码，基线存哈希不存原文

**build.py**

- SKILL.md 派生「运行时约定」一节，明确 `tools/` `rules/` 是相对**运行时根**
  而非项目根（避免读者照抄命令报 No such file）

## 0.4.0

首个带版本号的版本：引入根 `VERSION` 与运行时版本对账机制。

此前的内容（四类守卫、钩子合并与链式接力、`core.hooksPath` 接通、
卡片 → 生成物的 `build.py` / `check.py` 闭环等）见 `git log`。
