---
name: root-cause-protocol
stage: debugging
version: 2
description: >-
  根因分析协议：禁止「让症状闭嘴而不是修问题」的调试写法。工具会抓裸 except、
  异常块里只有 pass、空 catch 块这类把错误静默吞掉的代码，并强制给出处理证据。
  当用户说「修好了但不知道为什么」「偶发 bug 查不到」「异常被吞了」「log 里什么
  都没有」「程序闪退没报错」，或 review 时怀疑有人降日志级别、吞异常规避问题时
  使用。触发词：根因、异常被吞、except pass、空 catch、吞错误、偶发问题、
  查不到原因、swallowed exception、silent failure。
tools:
  - id: debug-smells
    script: tools/check_debug_smells.py
    args: ["--rules", "rules/root-cause-guard.json"]
    inputs: staged-files
    baseline: true
    exit: "0=通过 1=有新增坏味 2=规则或配置错误"
    desc: 抓裸 except / except-pass / 空 catch 等吞错误的写法
hooks:
  - event: PostToolUse
    matcher: "Write|Edit"
    tools: [debug-smells]
    blocking: false
    timeout: 10
  - event: pre-commit
    tools: [debug-smells]
    blocking: true
rules:
  - id: no-silent-failure
    kind: debug-smell
    severity: error
    source: rules/root-cause-guard.json
---

# 根因分析协议

## 何时使用

- 排查「偶发」「查不出」「日志里什么都没有」的问题 —— 十有八九是错误被静默吞了
- review 拉取的代码里出现 `except: pass`、空 `catch`、`# 暂时忽略`
- 自己想写 `except Exception: pass` 快速跳过报错的时候 —— 这张卡就是拦这个的

## 核心方法

### 吞掉的错误不是消失了，是转移了

`except: pass` 让**这次**的症状不见了，但根因还在，而且失去了唯一的观测点：
下次同类故障发生时，日志里什么都没有，问题从「能定位」退化成「玄学」。
偶发 bug 查不出来，大多数时候不是日志不够多，是关键异常在这一行被吃了。

### 正确的异常处理至少留一样东西

catch 里写什么才有资格放行：

1. **处理动作**：重试 / 降级 / 默认值 —— 且要能说清为什么这个异常可以这么处理
2. **证据**：`log.warning(..., exc_info=True)` / `console.error(e)`，带上上下文
3. **上抛**：处理不了就别接，让它炸在有能力处理的地方

三者一个都没有，就是不合法的 catch。收窄异常类型（`except ValueError`）不能
替代以上任何一条 —— 类型再窄，接了之后什么都不做还是吞。

### 工具只拦确定的，不确定的靠人

`check_debug_smells.py` 只拦「块里确定什么都没做」的写法（except-pass 用缩进
感知的内置检测器，正则做不准这个判断）。`except` 里写了日志但写错了级别这种，
工具查不出来 —— review 时人要看的是「有没有证据」，不是「有没有 except」。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **降日志级别规避报错** | `logger.setLevel(ERROR)` 把 warning 全关了，现场证据没了 | 日志级别是配置不是修复手段；排查期临时调整后必须还原 |
| **吞异常后再猜** | 偶发问题复现时日志干净得像什么都没发生 | 先全局搜 `except` / `catch`，把空块补上证据再排查 |
| **裸 except 连 Ctrl+C 都吞** | 程序杀不掉、退出时无响应 | 至少 `except Exception`，让 `KeyboardInterrupt` / `SystemExit` 穿过去 |
| **catch 里只打 "error"** | 日志里全是 `error`，不知道哪个请求、哪个参数、什么栈 | 带上下文和堆栈：`exc_info=True` / 完整 error 对象 |
| **上线前临时 try 包住整个函数** | 一个小异常把整条业务链静默跳过 | try 的范围缩到会真的抛那行；包大了等于没有观测 |

## 实施步骤

1. 排查阶段：先跑 `--all` 全量摸底，看现有代码有多少吞错误点（走基线不算新增）
2. 排查中发现的空 catch：补证据（日志 + 堆栈）或收窄类型后给出处理动作
3. 提交前：pre-commit 阻断层会拦新增的吞错误写法，存量走 `--update-baseline` 登记
4. 修一个收一个：修掉的存量从基线里收紧（重跑 `--update-baseline`）

## 验证

```bash
# 全量摸底
python tools/check_debug_smells.py --rules rules/root-cause-guard.json --all

# 端到端：确认吞错误真的会被拦
printf 'try:\n    f()\nexcept Exception:\n    pass\n' > tmp_swallow.py
python tools/check_debug_smells.py --rules rules/root-cause-guard.json --files tmp_swallow.py
echo "退出码=$?"   # 应为 1
rm tmp_swallow.py

# 自身用例
python tools/check_debug_smells.py --selftest    # 应 12/12 全绿
```

## 注意

- 规则分两层：`builtin`（except-pass，代码内置的缩进感知检测）和 `patterns`
  （行级正则，按项目增删）。改正则后先跑 `--selftest`
- 工具**不做**日志级别漂移检测（需要基线对比，误报率高）—— 这条靠 review，
  卡片管的是「下次写代码时想起这个约束」
- 摸底发现存量很多时先建基线，别一上来就阻断 —— 逼出 `--no-verify` 习惯
  比吞异常本身危害更大
