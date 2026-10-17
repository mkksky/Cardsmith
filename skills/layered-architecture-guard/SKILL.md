---
name: layered-architecture-guard
description: "按分层架构约束代码的依赖方向：只允许外层依赖内层，阻止内层反向引用外层 （如 domain 引用 infrastructure、entity 引用 service），并输出完整的违规导入链路。 当项目有分层目录结构（domain/application/infrastructure/presentation 或 model/service/repository/controller）、需要防止架构腐化，或用户抱怨 「检查一下有没有跨层调用」「依赖方向对不对」「分层乱了」「架构约束」 「service 里直接写了 SQL」「domain 层引了框架」时使用。 触发词：分层检查、跨层导入、依赖方向、架构约束、架构腐化、layer violation、 circular layer dependency、domain 依赖 infrastructure、内层依赖外层。"
agent_created: true
---

# 分层架构依赖方向守卫

架构腐化不是突然发生的，是每次「就这一次，直接调一下」积累出来的。这个技能的职责是**把这条线画死**，而不是事后 review 时靠人眼发现。

## 何时使用

- 项目已按层组织目录，但没有任何机制阻止跨层调用
- 出现过「domain 层 import 了 ORM」「service 里直接写 SQL」「entity 引了 controller 的 DTO」这类问题
- 接手老项目，要摸清依赖方向是否已经被破坏
- 加新模块前，想确认它该放在哪一层

## 核心方法

### 依赖方向单向

```
presentation ──→ infrastructure ──→ application ──→ domain
   (外层)                                              (内层)
```

**只允许箭头方向**：外层可以 import 内层，内层不能 import 外层。内层是最抽象、最稳定的，它一旦依赖外层，抽象就被具体实现绑死了。

### 三层判断，缺一不可

1. **这个文件属于哪一层** —— 按文件路径匹配 `layers[].patterns`
2. **它导入了谁** —— 解析 import 语句，把导入的模块映射回某一层
3. **方向对不对** —— 用 rank 比较：导入方 rank < 被导入方 rank = 违规

只做第 1、2 步不做第 3 步，就退化成「列出所有 import」，没有约束力。

### 必须用 AST，不能 grep

静态文本搜索抓不到延迟导入：

```python
def process():
    from infrastructure.db import session   # 藏在函数体里
```

这是藏循环依赖的常见手法。必须遍历 AST，拿到每个 `Import` / `ImportFrom` 节点的 `lineno`，判断它落在哪一层里 —— 函数体内的导入 `lineno` 会落在某个 `FunctionDef` 的行范围内。

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| **延迟导入绕过检查** | grep 全文找不到跨层 import，但运行时确实在跨层调用 | 用 AST 遍历所有 Import 节点，包括函数体、类方法内的 |
| **TYPE_CHECKING 块被误报** | `if TYPE_CHECKING: from x import Y` 是为了类型注解，运行时并不导入，却报违规 | 识别 `if TYPE_CHECKING:` 块，整块放行（`exempt.type_checking_blocks`） |
| **按文件路径判断层级** | 同一个模块被两个 pattern 匹配，或模块名与目录名不一致时判错 | 优先按**模块路径**（`a.b.c`）判断层级，文件路径只作兜底 |
| **相对导入漏判** | `from ..infrastructure import x` 拼不出绝对模块名，被跳过 | 用 `ImportFrom.level` 结合当前模块路径还原绝对路径 |
| **把测试也扫了** | `tests/` 里为了构造场景故意跨层，误报一片 | `skip_paths` 排除测试、迁移脚本、生成代码 |
| **组合根被误报** | 组合根（DI 装配层）的本职就是把 infrastructure 具体实现装配进抽象接口，报违规是误伤 | 规则加 `composition_roots`（glob 数组），命中的文件整体放行。典型：`service_factory`、`application_context`。判定标准：该文件删了这些 import 系统就没法启动，且只做装配不做业务 |
| **Java 的 import 是包名** | 直接把 Python 的模块名规则套到 Java 上，层级全判错 | Java 按 `package` 声明与 `import` 的全限定名判断，取包路径匹配 patterns |

## 实施步骤

1. **先摸底，不要先设卡**。跑一次全量扫描，看清当前违规分布在哪些层、有多少
2. 违规量小（个位数）→ 直接修
3. 违规量大（几十上百）→ 走**棘轮**：`--update-baseline` 把现存违规登记进基线，只阻断新增。基线文件按约定自动发现：`baselines/<rules 名>.baseline.json`（与规则文件同级的上一级目录）。清单是待处理债务，不是豁免，销账方式是真修代码后重跑 `--update-baseline`
4. 把规则文件 `rules/layered-architecture-guard.json` 的层级与 pattern 对齐到项目的真实目录结构（照抄默认值多半是错的）
5. 接入钩子：提交前阻断（`pre-commit` + `blocking: true`），编辑后仅提示（`PostToolUse` + `blocking: false`，避免边写边被打断）

## 验证

```bash
# 全量扫描，看基线
python tools/check_import_direction.py --rules rules/layered-architecture-guard.json --all

# 端到端：故意在 domain 层加一行 import infrastructure，确认退出码为 1
echo "from infrastructure.db import session" >> src/domain/user.py
python tools/check_import_direction.py --rules rules/layered-architecture-guard.json --all
echo "退出码=$?"   # 应为 1
```

退出码 `0` = 无违规，`1` = 有违规，`2` = 规则文件缺失或格式错误。**`2` 不能当通过处理** —— 配置坏了却没报错，比有违规更危险。

## 注意

- **规则文件要和目录结构对齐后再上线**。直接跑默认配置，大概率因为 pattern 不匹配而「全部通过」—— 这是假阳性最危险的形式：门禁看着在跑，其实一行都没检查到
- **零命中要报警**。如果某个层的 pattern 匹配不到任何文件，说明配置写错了，工具应该提示而不是静默通过
- 分层规则随项目演进，改层级定义时 `rules/*.json` 和卡片的 `version` 要同步更新

## 绑定的工具

> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/layered-architecture-guard.md`。

| 工具 | 脚本 | 用途 | 输入 | 退出码 |
|---|---|---|---|---|
| `import-direction` | `tools/check_import_direction.py` | 用 AST 解析各层文件的导入，判断是否违反分层依赖方向 | staged-files | 0=通过 1=有违规 2=用法或配置错误 |

## 绑定的钩子

> 本节由卡片自动派生，勿手改。

| 事件 | 匹配 | 工具 | 阻断 | 超时 |
|---|---|---|---|---|
| `PostToolUse` | `Write\|Edit` | `import-direction` | 否 | 15s |
| `pre-commit` | — | `import-direction` | 是 | 15s |

## 运行时约定

> 本节由卡片自动派生，勿手改。

本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：

- 在套件仓库内维护：运行时根 = 仓库根
- 在已安装的项目里使用：运行时根 = `<项目>/.codingskills/` —— 先 `cd .codingskills` 再照抄命令，或把路径写成 `.codingskills/tools/...`

<!-- 本文件由 scripts/build.py 从 cards/layered-architecture-guard.md 生成，请勿直接修改。 -->
