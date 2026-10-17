# 编码卡片规范

卡片是这套件的**唯一真源**。`skills/` 下的 SKILL.md、`hooks/hooks.json` 全部由卡片生成 —— **手改生成物等于制造漂移，`check.py` 会当场报错。**

```
cards/<name>.md          ← 手改这里
      │ build.py
      ├──→ skills/<name>/SKILL.md
      └──→ hooks/hooks.json
```

卡片本体是一张 Markdown：**frontmatter 声明机器的部分，正文写人的部分。**

---

## 一、frontmatter 字段

### 顶层

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 与文件名一致，`^[a-z0-9]+(-[a-z0-9]+)*$` |
| `stage` | 是 | `onboarding` / `coding` / `debugging` / `delivery` 四选一 |
| `description` | 是 | 触发描述，原样写进 SKILL.md 的 frontmatter。写法见 `CONVENTIONS.md` |
| `version` | 是 | 整数。改规则或绑定关系时必须 +1，否则看不出卡片何时变过 |
| `tools` | 否 | 绑定的检查工具列表 |
| `hooks` | 否 | 绑定的钩子列表 |
| `rules` | 否 | 规则声明列表 |

### tools 项 —— 「这个技能靠什么执行」

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | 是 | 工具标识，卡片内唯一。hooks 用它引用 |
| `script` | 是 | 相对仓库根的路径，**必须实际存在**，否则 check 报错 |
| `args` | 否 | 固定参数列表 |
| `inputs` | 否 | `staged-files` / `all-files` / `none`，默认 `staged-files` |
| `exit` | 否 | 退出码约定，形如 `0=通过 1=违规 2=用法错误` |
| `desc` | 否 | 一句话说明用途 |

### hooks 项 —— 「什么时候跑」

| 字段 | 必需 | 说明 |
|---|---|---|
| `event` | 是 | WorkBuddy 事件，见下表；另支持 git 钩子事件 `pre-commit` / `commit-msg` / `pre-push` |
| `matcher` | 否 | 正则。仅 PreToolUse / PostToolUse 用，如 `"Write|Edit"` |
| `tools` | 是 | 引用的 tool id 列表 |
| `blocking` | 否 | 默认 `false`。`true` 时工具返回违规会让钩子以退出码 2 阻断 |
| `timeout` | 否 | 秒，默认 15 |

事件可选值：

```
PreToolUse  PostToolUse  SessionStart  UserPromptSubmit
Stop  SubagentStop  PreCompact
pre-commit  commit-msg  pre-push（均为 git 钩子）
```

git 事件的计划文件约定：`hooks/<event>-plan.json`（如 `hooks/commit-msg-plan.json`），
由 build.py 生成、`.githooks/<event>` 消费。`commit-msg` 阶段 git 会把 message
文件路径作为 `$1` 传入，工具用 `--msg-file` 接收；这两个事件下的工具一般声明
`inputs: none`。

### rules 项 —— 「管什么」

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | 是 | 规则标识 |
| `kind` | 是 | `file-lines` / `import-direction` / `naming` / `api-contract` / `format-lint` |
| `severity` | 是 | `error`（阻断）/ `warn`（仅提示） |
| `source` | 否 | 规则细节的 JSON 路径。**复杂规则必须外置**，别塞进 frontmatter |
| 其余 | 否 | 按 kind 取，如 `file-lines` 用 `limit` |

**为什么复杂规则要外置成 JSON**：卡片 frontmatter 是人读的，要能一眼扫完。分层依赖矩阵这种东西动辄几十行，塞进来卡片就没法看了。而且 JSON 解析零歧义 —— 规则是机器执行的，不该冒解析风险。

---

## 二、正文写什么

正文就是生成后 SKILL.md 的正文。按 `CONVENTIONS.md` 的六节结构写。

**不用手写「绑定了哪些工具/钩子」** —— `build.py` 会从 frontmatter 自动派生这一节追加到末尾。手写会造成两处描述打架。

---

## 三、支持的 YAML 语法（超出会报错）

卡片由 `scripts/yamlmini.py` 解析，是**严格子集**。允许：

```yaml
key: value              标量（字符串/整数/小数/true/false/null）
key: "含 # 的字符串"      引号内 # 不是注释
key:                    嵌套
  sub: 1
list:
  - a                   标量序列
  - id: x               序列项是映射
    script: y
text: >                 折叠块（多行规格说明用它）
  第一行
  第二行
```

禁止（**遇到直接报错，不会静默跳过**）：tab 缩进、`[a, b]`、`{a: b}`、`&anchor`、`*alias`、`---` 多文档。

两个容易踩的点：

- **值里带 `#` 必须加引号**。YAML 规则是「`#` 前有空白即为注释」，所以 `desc: 用 # 号开头` 会被截断成 `用`。写 `desc: "用 # 号开头"`。
- **值里带 `:` 不一定要引号**，但 `http://x` 这类在序列项里可能被误判 —— 解析器已按「冒号后必须是空白」处理，能正确识别。

---

## 四、命名与版本

- 卡片文件名 = `name`，改一个必须改另一个（check 会查）
- 改 `rules` / `tools` / `hooks` 任一项 → `version` +1
- 只改正文措辞 → 不必动 version

---

## 五、提交前自检

```bash
python scripts/build.py           # 卡片 → 生成物
python scripts/check.py           # 三方对账，全绿才能提交
```

`check.py` 查的是机器能查的：字段合法性、tool 脚本是否存在、生成物是否与卡片一致、hooks.json 里有没有卡片没声明的条目。

查不出、要人看的：`description` 的触发词选得准不准、规则阈值定得合不合理。
