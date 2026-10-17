# 编码 skill 候选清单

按研发流程四阶段组织。**写之前先读 `docs/CONVENTIONS.md` 与 `docs/CARD-SPEC.md`。**

## 状态图例

| 标记 | 含义 |
|---|---|
| ✅ | 已有卡片，且工具、钩子齐备 |
| 🔧 | 缺工具 —— **先写工具再建卡片**，否则卡片只是空承诺 |
| 📝 | 待建。内容应来自真实踩过的坑，不是官方文档的复述 |

这套件的硬约束：**卡片声明了 tool，脚本就必须存在**（`check.py` 会验证）。所以「🔧」状态的任务，第一步是写 `tools/` 下的脚本，不是写卡片。

---

## 一、需求 / 上手

| 状态 | 名称 | 说明 | 缺什么 |
|---|---|---|---|
| 📝 | `legacy-codebase-onboarding` | 接手老项目，产出系统地图：模块边界、程序入口、数据流、外部依赖、隐性约定 | 可自动化的部分：入口点扫描、模块依赖图生成、被引用次数统计 |
| 🔧 | `api-contract-guard` | 前后端接口契约：字段命名、分页与错误码约定、契约变更的兼容性 | 需要写 `tools/check_api_contract.py`，针对具体框架（Spring Boot / FastAPI） |
| 📝 | `tech-proposal-review` | 技术方案评审清单：选型理由、影响面、回滚路径、不可逆点 | 本质是判断，可能无法自动化 —— 建卡片时要接受它只享受弱保护 |

## 二、编码

| 状态 | 名称 | 说明 | 缺什么 |
|---|---|---|---|
| ✅ | `layered-architecture-guard` | 分层依赖方向：只允许外层依赖内层 | — |
| ✅ | `file-size-naming-guard` | 单文件行数上限 + 文件名命名规范 | — |
| ✅ | `format-lint-guard` | ruff / eslint 等格式化与 lint 工具链接入 | — |
| 📝 | `java-spring-layering` | Spring Boot 特有的分层细节：DTO 转换位置、事务边界、Controller 不许写业务 | 与 `layered-architecture-guard` 有重叠，想清楚边界再建 |
| 📝 | `vue-component-structure` | Vue 组件拆分粒度、props / emit 契约、状态该放哪 | 需要 AST 或 vue 编译器解析 `.vue`，工具成本较高 |
| 🔧 | `db-migration-guard` | 数据库变更安全：可回滚设计、大表变更注意 | ✅ 已建（2026-09-24）。首版落地为**受保护路径阻断**（`tools/check_protected_paths.py`）：迁移目录任何暂存变更（含删除）pre-commit 拦截。迁移脚本可回滚性检查留待后续 |
| ✅ | `doc-sync-guard` | 文档同步：文档引用的路径时效性 + API 文档方法名与源码 AST 对账 | ✅ 已建（2026-09-24，某 Python 项目 实测）。首轮真数据抓出 10 处真漂移零误报。参数级签名比对留待真实案例驱动 |
| ✅ | `secrets-guard` | 密钥防泄：私钥/云厂商 token/敏感文件暂存 → 阻断；密钥形态赋值 → 仅提醒不拦 | ✅ 已建（2026-09-24）。两级严重度设计：hard 零误报才阻断，soft 永不拦截——给「个人项目不想被打断」的场景留了余地。报告打码、基线存哈希不存原文 |

## 三、调试

| 状态 | 名称 | 说明 | 缺什么 |
|---|---|---|---|
| ✅ | `root-cause-protocol` | 根因分析协议：禁止降日志级别之类的规避、必须给证据链、区分症状与根因 | ✅ 已建（2026-09-24）。工具抓裸 except / except-pass（缩进感知内置检测器）/ 空 catch；日志级别漂移检测留待后续（需基线对比，误报率高） |
| 📝 | `maven-build-troubleshoot` | Maven 构建失败、依赖冲突、版本仲裁、插件与 JDK 错配 | 可写工具包装 `mvn dependency:tree` 做冲突检测 |
| 📝 | `runtime-log-triage` | 从症状反查代码路径：日志分级定位、异常链路追踪 | 依赖具体日志格式，工具需按项目定制 |
| 🔧 | `frontend-build-troubleshoot` | npm / vite / electron-builder 构建与打包故障 | 需要写工具解析构建输出并归类失败原因 |

## 四、交付

| 状态 | 名称 | 说明 | 缺什么 |
|---|---|---|---|
| ✅ | `commit-message-guard` | commit message 规范：类型前缀、长度、正文格式 | ✅ 已建（2026-09-24）。`tools/check_commit_msg.py` 接 `commit-msg` 阶段（套件已扩展支持该 git 事件） |
| ✅ | `multi-machine-sync-guard` | 多机 Git 同步：合并后必须跑导入扫描、判断落后不能信 `git status` | ✅ 已建（2026-09-24）。`tools/check_sync_state.py` 用 `git ls-remote` 现查远端 SHA 与本地 HEAD 对撞，接 `pre-push` 阶段；网络不可达放行并警告 |
| ✅ | `release-checklist` | 发布前检查：版本号、变更日志、构建产物、回滚预案 | ✅ 已建（2026-09-24，聚合版）。`tools/release_checklist.py` 从 registry 动态取全部工具跑 `--all` 出体检表（exclude：commit-msg/sync-state）；脚本缺失也计 FAIL 防静默失效。版本号/变更日志/回滚预案等人工项不在此范围 |
| 📝 | `env-migration-setup` | 开发环境迁移：新机器从零到可跑 | 本质是文档，工具价值有限 |

---

## 建议顺序

1. ~~先把现有三张卡片接到一个真实项目上跑通~~ ✅ 已完成（2026-09-24，真实项目跑通一轮）
2. ~~`root-cause-protocol`~~ ✅ 已建
3. ~~`commit-message-guard`~~ ✅ 已建
4. 其余按实际踩坑顺序来。下一批候选：`frontend-build-troubleshoot`、
   `api-contract-guard`、迁移脚本可回滚性检查（db-migration-guard 增强）

## 写作提示

- **P2 的别急着写**。内容应该来自真实踩过的坑，否则会退化成官方文档的复述 —— 这类内容不值得占一个技能的位置
- **一个技能只解决一类问题**。发现某个卡片里出现两条互不相关的流程，拆成两张
- **先问：这个流程下次还需要我从头想一遍吗？** 不需要的，不值得建
