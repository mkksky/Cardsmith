---
name: perf-path-guard
description: "加载链路性能守卫：抓「本可以并发/合并/省略的工作被写成了排队」。工具会拦 列表 key 用下标、循环里 setData、forEach 套 async、用睡眠等异步这些判定确定的坏味， 并把「循环里 await」单列为不阻断的提示项。当用户说「页面加载慢」「首屏卡」「转圈久」 「列表图片一张一张出」「接口明明很快但还是慢」「帮我做性能优化」「加个性能门禁」 「有没有串行请求」「重复请求太多」时使用。触发词：加载慢、首屏、性能优化、 串行请求、重复请求、无用等待、N+1、瀑布请求、渲染卡顿、list key、lazy load。"
agent_created: true
---

# 加载链路性能守卫

## 何时使用

- 用户说「页面加载慢」「首屏转圈久」「列表图片一张一张出」
- 准备做性能优化，需要先拿到**机器确认的问题清单**，而不是靠感觉
- 接手一个项目，想快速摸清它在加载链路上欠了多少债
- 自己正要写 `for (const x of list) { await api(x) }` —— 这张卡就是拦这个的

## 核心方法

### 慢的四种来源，只有两种能被机器确认

| 类别 | 典型写法 | 能否机器判定 |
|---|---|---|
| 关键路径被拉长 | `await a; await b;`（本可并发） | **不能** —— 「能否并发」取决于业务语义 |
| 单段变慢 | 循环内 await 累加 N 次往返 | **能**（结构可判），但有正当例外 |
| 重复付费 | 同一份数据反复请求、缓存不写 | **不能** —— 需要跨文件数据流 |
| 启动与渲染 | 列表 key 用下标、循环内 setData | **能**，且零例外 |

所以这张卡只管后两类里**判定确定**的部分。前两类写进「守不住的」一节，
靠 review 和 APM 兜底 —— 硬做成门禁只会产出噪声。

### 为什么分成阻断档和提示档

这是这张卡最重要的设计，也是实测调出来的：

`await-in-loop`（循环里 await）在真实项目上跑 `--all`，抽样核验后发现
**大部分命中是正当的**：重试循环（`for (let attempt = 0; attempt <= retry; attempt++)`
必须串行，下一次尝试本来就要等上一次失败）、逐张图片压缩（并发会顶爆小程序内存）、
批处理分页拉取（下一页依赖上一页游标）。

判定所需的信息（「这次循环的下一轮要不要依赖上一轮」）在语法层面根本不存在。
把它做成阻断 = 逼人养成 `--no-verify` 的习惯，比不做门禁更糟。

于是拆两档：

- **阻断档**（`perf-smells`）：`wx:key` 用下标、循环内 `setData`、
  `forEach` 套 `async`、`await` 套 `setTimeout` 等 —— 这些**没有正当例外**，
  命中即错
- **提示档**（`perf-serial-advisory`）：循环内 `await`、请求路径上的 `execSync` ——
  **有正当例外**，只提醒不阻断，判定交给人

两档各自有独立基线，互不干扰。

规则文件还可用 `exemptions` 登记**经核验的合理命中**：每项必须有
`file`、`pattern`、`reason`，可选 `match` 正则只放行同一文件中特定循环。
豁免与基线语义不同 —— 基线是待处理债务，豁免是「已确认不是问题」。

### 每条规则在防什么

| 规则 | 后果 | 改法 |
|---|---|---|
| `wx-key-index` | diff 认为节点位置没变，刷新时全量原地复用、内容重填，滚动位置和图片加载状态丢失 | 换成稳定唯一字段（`id` / `_id` / 业务主键）。**字符串数组用 `*this` 是正确写法，不在拦截范围** |
| `wx-for-no-key` | 同款问题，退化成按下标 diff | 补上 `wx:key` |
| `setdata-in-loop` | 每轮一次逻辑层→渲染层通信，N 轮就 N 次通信加 N 次 diff | 内存里攒成一个 patch 对象，循环外 setData 一次 |
| `async-in-foreach` | `forEach` 不等待回调，遍历瞬间结束、回调还在飞，后续读到半成品 | 要并发用 `map` + `Promise.all`，要串行用 `for...of` |
| `await-sleep` | 慢的根源没解决，只是把等待写进代码，每次都白等固定时长 | 改成事件 / 回调 / Promise 链 |
| `await-in-loop`（提示） | N 次往返依次排队 | 先收集 Promise 再 `Promise.all`，注意别超过并发上限 |
| `sync-exec`（提示） | 阻塞整个事件循环，同进程所有并发请求一起等 | 换异步版本；CLI 脚本里可以不管 |

## 守不住的（别指望工具）

这四条是真问题，但判定所需的信息不在代码表面：

- **「这两个 await 能不能并发」** —— 取决于业务语义。`loadUser()` 之后
  `loadOrders(userId)` 就不能并发，但工具看不出参数依赖
- **「这个接口是不是被重复调了」** —— 跨文件数据流，得看调用图
- **「图片该不该裁剪」** —— 要看渲染尺寸，工具只能看到 URL
- **「缓存该不该加」** —— 取决于数据变更频率和容忍的延迟

这些写进 review checklist，不要往门禁里塞。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **拿 `await-in-loop` 当阻断用** | 重试循环、批处理全被拦，很快出现 `--no-verify` | 这是提示档，别改 blocking |
| **`*this` 被当成坏 key** | 字符串数组列表被误报 | `*this` 对字符串/数字数组是唯一正确写法，规则已排除 |
| **云函数公用副本重复报** | 同一份 `common` 逻辑复制到 N 个云函数，一个问题报 N 遍 | 看第一份就行，或在项目规则里跳过复制目录 |
| **运维脚本被当热路径** | 批量迁移、构建脚本里的串行 await 疯狂命中 | 提示档已跳过脚本和迁移目录；项目可再收窄 |
| **只看命中数不看位置** | 修了一堆低频脚本，首屏没变 | 先按「是否在用户请求路径上」排优先级 |
| **把合法串行塞进基线** | 债务清单永远清不掉，也看不出哪些是误报 | 经核验的重试、分页、批处理写入规则 `exemptions`，必须写理由 |
| **豁免写得太宽** | 整个文件一次放行，掩盖后来新增的同模式坏味 | 同一文件多段循环时用 `match` 限定循环头片段 |
| **基线建太小** | 只扫暂存文件，存量债到下次提交全变「新增」 | `--update-baseline` 必须配 `--all` |

## 实施步骤

1. **摸底**：先跑 `--all` 两档，拿到完整清单，别一上来就阻断
2. **分流**：把命中项按「是否在用户请求路径上」排序 —— 分包里的内部工具页优先级最低
3. **建基线**：`--all --update-baseline` 登记存量，让门禁从「零新增」开始
4. **改一处收一处**：修掉的存量重跑 `--update-baseline` 收紧，棘轮只降不升
5. **拿不到的用 review 补**：上面「守不住的」四条写进团队 checklist

## 验证

```bash
# 自检（应 24/24 全绿）
python tools/check_perf_smells.py --selftest
# 用例单独成文件（工具本体 497 行，内联会超 500 上限），也可直接跑
python tools/check_perf_smells_selftest.py

# 阻断档全量摸底
python tools/check_perf_smells.py --rules rules/perf-guard.json --all

# 提示档全量摸底
python tools/check_perf_smells.py --rules rules/perf-guard-advisory.json --all

# 端到端：确认坏 key 真的会被拦
printf '<view wx:for="{{list}}" wx:key="index">{{item}}</view>\n' > tmp_key.wxml
python tools/check_perf_smells.py --rules rules/perf-guard.json --files tmp_key.wxml
echo "退出码=$?"   # 应为 1
rm tmp_key.wxml
```

## 注意

- 规则拆两个文件是刻意的，不要合并 —— 合并后就没法只阻断确定的、放过有例外的
- 改 `builtin` 需同步改工具里的 `BUILTIN_SPECS`，两边对不上会报「未知内置检测器」
- 项目侧规则文件装完后归项目所有，`skip_paths` 可以按自己目录结构加，升级套件不会覆盖
- 这个工具**不做**数据流分析，别指望它发现「同一个接口被两个页面各调一次」

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/perf-path-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `perf-smells` | `tools/check_perf_smells.py` | 阻断档：列表 key 用下标、循环内 setData、forEach+async、睡眠等异步 | staged-files | 0=通过 1=有新增坏味 2=规则或配置错误 |
| `perf-serial-advisory` | `tools/check_perf_smells.py` | 提示档：循环内 await、请求路径上的同步执行（有正当例外，只提醒） | staged-files | 0=通过 1=有提示项（不阻断）2=规则或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `perf-smells` | 否 | 10s |
| `pre-commit` | — | `perf-smells` | 是 | 15s |
| `pre-commit` | — | `perf-serial-advisory` | 否 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/perf-path-guard.md 生成，请勿直接修改。 -->
