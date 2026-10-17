#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_readme.py —— 项目运行时 README 的渲染器

从 cards/ 元数据派生检查清单，固定骨架（棘轮语义、规则归属）由套件维护。
build.py 在 tree() 里调用；install.py 把产物落为 <项目>/.cardsmith/README.md。

独立成模块的原因：README 渲染是一类独立职责，且 build.py 受自家 500 行门禁约束。
有意不 import build（避免循环导入），_with_prefix 为本地实现。
"""

from __future__ import annotations

from pathlib import Path


def _with_prefix(path: str, prefix: str) -> str:
    """给 tools/ 或 rules/ 开头的相对路径加运行时前缀。前缀为空时原样返回。"""
    if not prefix:
        return path
    return f"{prefix.rstrip('/')}/{path}"


def _manual_cmd(
    script: str, args: "list[str]", prefix: str, inputs: str = "staged-files"
) -> str:
    """从卡片 tools 元数据拼出可复制的执行命令。rules/tools 开头的参数加前缀。

    --all 只对读文件集的工具（staged-files / all-files）有意义；
    inputs: none 的工具（如 sync-state，只对撞远端 SHA）没有文件集语义，
    拼上 --all 会报 unrecognized arguments 退出码 2 —— 文档不能许诺跑不起来的命令。
    """
    parts = ["python", _with_prefix(script, prefix)]
    for a in args:
        s = str(a)
        parts.append(
            _with_prefix(s, prefix) if s.startswith(("tools/", "rules/")) else s
        )
    if inputs != "none":
        parts.append("--all")
    return " ".join(parts)


def render_runtime_readme(cards: "list[dict]", prefix: str) -> str:
    """项目运行时的 README：给打开 .cardsmith/ 的人类看的一页速查。

    固定骨架（棘轮语义、规则归属）写死在这里，由套件维护；
    检查清单从卡片元数据派生 —— 卡片改了 README 跟着变，check.py 逐字节对账兜底。
    install.py 装到项目时落为 .cardsmith/README.md。
    """
    lines = [
        "# cardsmith 运行时说明",
        "",
        "<!-- 本文件由 scripts/build.py 从 cards/ 生成，请勿直接修改。改内容请改 build.py 或对应卡片。 -->",
        "",
        "本目录是 cardsmith 套件装进本项目的运行时，为 git 提交提供自动化检查门禁。",
        "工具清单与绑定关系由套件仓库的 `cards/` 声明；重装只会更新工具、补充新规则文件，",
        "**`rules/` 里的项目定制（层级 pattern、阈值、skip_paths、composition_roots）不会被覆盖**。",
        "",
        "## 手动检查（在项目根执行）",
        "",
        "默认只检查 git 暂存文件；`--all` 全量扫描（与文件集无关的工具，如 sync-state，直接执行）：",
        "",
        "| 检查 | 手动执行命令 | 退出码 |",
        "|---|---|---|",
    ]
    baseline_hints: "list[str]" = []
    for card in cards:
        for t in card["tools"]:
            args = [str(a) for a in (t.get("args") or [])]
            lines.append(
                "| {desc} | `{cmd}` | {exit} |".format(
                    desc=str(t.get("desc", t["id"])).replace("|", "\\|"),
                    cmd=_manual_cmd(
                        str(t["script"]),
                        args,
                        prefix,
                        str(t.get("inputs", "staged-files")),
                    ),
                    exit=str(t.get("exit", "—")).replace("|", "\\|"),
                )
            )
            rules_files = [
                args[i + 1]
                for i, a in enumerate(args)
                if a == "--rules" and i + 1 < len(args)
            ]
            if t.get("baseline"):
                # 只有声明了 baseline: true 的工具才登记基线（format-lint 没实现棘轮，不能列）
                for rf in rules_files:
                    stem = Path(rf).stem
                    baseline_hints.append(
                        _with_prefix(f"baselines/{stem}.baseline.json", prefix)
                    )

    lines += [
        "",
        "## 棘轮基线（存量债务）",
        "",
        "存量违规登记在基线文件里，**命中者只提示不阻断，新增违规照样拦截**：",
        "",
    ]
    lines += [f"- `{b}`" for b in baseline_hints]
    lines += [
        "",
        "修掉存量后收紧（登记当前实际违规，退出码 0）：",
        "",
        "```",
        "python "
        + _with_prefix("tools/<检查脚本>", prefix)
        + " --rules "
        + _with_prefix("rules/<规则>.json", prefix)
        + " --all --update-baseline",
        "```",
        "",
        "基线是待处理债务，不是豁免；显式指定 `--baseline` 但文件不存在 → 退出码 2。",
        "",
        "## 钩子",
        "",
        "- **git 钩子**：`.githooks/{pre-commit,commit-msg,pre-push}` 分别读 `hooks/<event>-plan.json`。"
        "工具缺失时跳过而非阻断；**退出码 2 = 配置错误，绝不能当通过处理**。",
        "- **WorkBuddy 编辑时提示**：`hooks/hooks.json`（需 WorkBuddy 在场，失效不影响 git 门禁）。",
        "",
        "## 目录结构",
        "",
        "| 目录/文件 | 作用 | 归属 |",
        "|---|---|---|",
        "| `tools/` | 检查脚本 | 套件（重装覆盖） |",
        "| `rules/` | 检查规则 | **项目**（重装保留定制，只补新文件） |",
        "| `hooks/` | 分发器 run.py + 生成的绑定配置 | 套件（重装覆盖） |",
        "| `baselines/` | 棘轮基线（债务清单） | **项目**（随债务修掉而收紧） |",
        "| `README.md` | 本文件 | build.py 生成，勿手改 |",
        "",
        "## 真源与快照",
        "",
        "- `cards/` 是套件守卫卡片的**只读快照**，随本项目提交，方便在项目内查阅每张卡声明的工具、",
        "  钩子与规则绑定。",
        "- **真源在套件上游仓**（独立 git 仓库，不在本项目内）。改守卫行为的顺序：改上游",
        "  `cards/`、`tools/` → 上游跑 `scripts/build.py --check` → 重装本项目（`python",
        "  <上游>/scripts/install.py --project <本项目> --with-git-hook`）。**不要直接改本项目",
        "  `.cardsmith/tools/`** —— 重装即被上游覆盖，改了不生效；运行时读的是 `tools/` 与",
        "  `rules/`，不是 `cards/`。",
        "- 本项目定制（`source_roots`、`cf_doc_bases`、`allow_refs`、`allow_ref_patterns`、棘轮基线）",
        "  全在 `rules/` 与 `baselines/`，跟本项目走，重装不覆盖。",
        "",
        "## 换机器 / 新克隆（每台机器一次）",
        "",
        "git 的 `core.hooksPath` 与换行设置是**本机配置**，不随仓库走，新机器要配一次：",
        "",
        "```bash",
        "git config core.hooksPath .githooks    # 让仓库内的 .githooks/ 生效（否则门禁根本不跑）",
        "```",
        "",
        "`.gitattributes` 已把文本换行统一为 LF；本地若仍见 CRLF 噪音 diff，跑一次",
        "`git add --renormalize .` 归一。",
        "",
    ]
    return "\n".join(lines)
