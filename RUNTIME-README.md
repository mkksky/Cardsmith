# cardsmith 运行时说明

<!-- 本文件由 scripts/build.py 从 cards/ 生成，请勿直接修改。改内容请改 build.py 或对应卡片。 -->

本目录是 cardsmith 套件装进本项目的运行时，为 git 提交提供自动化检查门禁。
工具清单与绑定关系由套件仓库的 `cards/` 声明；重装只会更新工具、补充新规则文件，
**`rules/` 里的项目定制（层级 pattern、阈值、skip_paths、composition_roots）不会被覆盖**。

## 手动检查（在项目根执行）

默认只检查 git 暂存文件；`--all` 全量扫描（与文件集无关的工具，如 sync-state，直接执行）：

| 检查 | 手动执行命令 | 退出码 |
|---|---|---|
| 校验 commit message 的类型前缀、scope 与主题行长度 | `python tools/check_commit_msg.py --rules rules/commit-message-guard.json` | 0=通过 1=违规 2=配置错误 |
| 阻断受保护路径（迁移脚本等）下的任何暂存变更 | `python tools/check_protected_paths.py --rules rules/db-migration-guard.json --all` | 0=通过 1=受保护路径有变更 2=规则或配置错误 |
| 查文档引用的路径是否存在、API 文档声明的方法在源码里是否存在 | `python tools/check_doc_sync.py --rules rules/doc-sync-guard.json --all` | 0=通过 1=有违规 2=规则或配置错误 |
| 检查文件行数与文件名命名规范 | `python tools/check_size_naming.py --rules rules/file-size-and-naming-guard.json --all` | 0=通过 1=有违规 2=用法或配置错误 |
| 调用本机已装的 ruff / ruff-format / eslint，统一到套件退出码 | `python tools/check_format_lint.py --rules rules/format-lint-guard.json --all` | 0=通过或跳过 1=有违规 2=规则文件错误 |
| 用 AST 解析各层文件的导入，判断是否违反分层依赖方向 | `python tools/check_import_direction.py --rules rules/layered-architecture-guard.json --all` | 0=通过 1=有违规 2=用法或配置错误 |
| ls-remote 现查远端 SHA 与本地 HEAD 对撞，落后/分叉即阻断 | `python tools/check_sync_state.py` | 0=状态允许继续 1=落后或分叉需先同步 2=配置错误 |
| 聚合 registry 全部工具的 --all 结果出体检表，git 事件专用工具按规则排除 | `python tools/release_checklist.py --rules rules/release-checklist.json` | 0=全部通过（含 WARN） 1=有 FAIL 2=配置错误 |
| 抓裸 except / except-pass / 空 catch 等吞错误的写法 | `python tools/check_debug_smells.py --rules rules/root-cause-guard.json --all` | 0=通过 1=有新增坏味 2=规则或配置错误 |
| 扫描暂存文件里的私钥、云厂商 token、密钥形态赋值；高危阻断，可疑仅提醒 | `python tools/check_secrets.py --rules rules/secrets-guard.json --all` | 0=通过或仅软提醒 1=高危密钥 2=配置错误 |
| 解析导入说明符，校验目标模块在仓库内真实存在 | `python tools/check_stale_imports.py --rules rules/stale-import-guard.json --all` | 0=通过 1=新增违规 2=规则/用法错误 |

## 棘轮基线（存量债务）

存量违规登记在基线文件里，**命中者只提示不阻断，新增违规照样拦截**：

- `baselines/doc-sync-guard.baseline.json`
- `baselines/file-size-and-naming-guard.baseline.json`
- `baselines/layered-architecture-guard.baseline.json`
- `baselines/root-cause-guard.baseline.json`
- `baselines/secrets-guard.baseline.json`

修掉存量后收紧（登记当前实际违规，退出码 0）：

```
python tools/<检查脚本> --rules rules/<规则>.json --all --update-baseline
```

基线是待处理债务，不是豁免；显式指定 `--baseline` 但文件不存在 → 退出码 2。

## 钩子

- **git 钩子**：`.githooks/{pre-commit,commit-msg,pre-push}` 分别读 `hooks/<event>-plan.json`。工具缺失时跳过而非阻断；**退出码 2 = 配置错误，绝不能当通过处理**。
- **WorkBuddy 编辑时提示**：`hooks/hooks.json`（需 WorkBuddy 在场，失效不影响 git 门禁）。

## 目录结构

| 目录/文件 | 作用 | 归属 |
|---|---|---|
| `tools/` | 检查脚本 | 套件（重装覆盖） |
| `rules/` | 检查规则 | **项目**（重装保留定制，只补新文件） |
| `hooks/` | 分发器 run.py + 生成的绑定配置 | 套件（重装覆盖） |
| `baselines/` | 棘轮基线（债务清单） | **项目**（随债务修掉而收紧） |
| `README.md` | 本文件 | build.py 生成，勿手改 |

## 真源与快照

- `cards/` 是套件守卫卡片的**只读快照**，随本项目提交，方便在项目内查阅每张卡声明的工具、
  钩子与规则绑定。
- **真源在套件上游仓**（独立 git 仓库，不在本项目内）。改守卫行为的顺序：改上游
  `cards/`、`tools/` → 上游跑 `scripts/build.py --check` → 重装本项目（`python
  <上游>/scripts/install.py --project <本项目> --with-git-hook`）。**不要直接改本项目
  `.cardsmith/tools/`** —— 重装即被上游覆盖，改了不生效；运行时读的是 `tools/` 与
  `rules/`，不是 `cards/`。
- 本项目定制（`source_roots`、`cf_doc_bases`、`allow_refs`、`allow_ref_patterns`、棘轮基线）
  全在 `rules/` 与 `baselines/`，跟本项目走，重装不覆盖。

## 换机器 / 新克隆（每台机器一次）

git 的 `core.hooksPath` 与换行设置是**本机配置**，不随仓库走，新机器要配一次：

```bash
git config core.hooksPath .githooks    # 让仓库内的 .githooks/ 生效（否则门禁根本不跑）
```

`.gitattributes` 已把文本换行统一为 LF；本地若仍见 CRLF 噪音 diff，跑一次
`git add --renormalize .` 归一。
