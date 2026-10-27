---
name: config-guard
description: "检查 Spring Boot application*.yml 的机器可判定危险配置组合：CORS 裸通配阻断， 明文密码默认值 / JWT 默认密钥 / Flyway 与 ddl-auto 并存 / 关闭迁移校验等进提示档。 当项目使用 Spring Boot，且担心配置文件写了不该写的默认值（密码、密钥）、 或 Flyway 与 Hibernate ddl-auto 两套迁移同时生效、或迁移校验被关闭时使用。 触发词：application.yml 检查、配置安全、密码明文、CORS 配置、Flyway 校验、 JWT 默认密钥、ddl-auto 冲突、配置组合检查。"
agent_created: true
---

# 配置安全组合守卫

单个配置项看起来可能没问题，**组合起来就危险**。本守卫只负责机器能确定的组合，
不猜业务语义。

## 核心方法

### 两档分离，刻意不同

| 档位 | 命中条件 | 处理 |
|------|---------|------|
| **阻断档** | CORS 裸 `*` / `http://*` 通配 | exit 1，提交被拦 |
| **提示档** | 明文密码默认值、JWT 默认密钥、`ddl-auto` 与 Flyway 并存、关闭迁移校验 | exit 0 + 输出 `[!]`，不计 FAIL |

提示档是为了"存量不逼死"：项目里肯定有 dev 配置的明文默认值（本地方便），
如果直接阻断，大家立刻把它放进 `--no-verify`。

### 为什么 CORS 通配要进阻断

裸 `*` + `allowCredentials=true` 等于把登录态交给任意站点。这是安全界公认的红线，
没有"开发环境方便"的托词。`http://localhost:*` 这种限端口的通配放行。

### 为什么 ddl-auto 与 Flyway 并存要提示

Flyway 说"表该长这样"，Hibernate 说"让我改一下"，生产时 `ddl-auto=none`
就会暴露差异。开发库"能跑"不代表没问题。

## 规则真源

- `rules/config-guard.json` — 检查项定义与来源模式
- 工具：`tools/check_config_guard.py`

新增检查项时：在 `check_config_guard.py` 的 `KNOWN_CHECKS` 集合里加 id，
在规则 JSON 的 `checks` 数组里加定义，正则匹配 yml 中的对应行。

## 自检

```bash
python tools/check_config_guard.py --rules rules/config-guard.json --selftest
```

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/config-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `config-guard` | `tools/check_config_guard.py` | 正则匹配 yml 中的危险组合，CORS 裸通配阻断，其余走提示档 | staged-files | 0=通过（含提示） 1=阻断级配置 2=规则或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `pre-commit` | — | `config-guard` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/config-guard.md 生成，请勿直接修改。 -->
