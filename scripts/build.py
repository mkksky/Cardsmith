#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py —— 卡片 → 生成物

唯一真源是 cards/<name>.md。本脚本把它展开成：

    skills/<name>/SKILL.md        技能本体
    hooks/hooks.json              WorkBuddy 钩子配置（插件级约定位置）
    hooks/registry.json           工具注册表，hooks/run.py 按它找脚本
    hooks/pre-commit-plan.json    git 钩子要跑哪些工具
    RUNTIME-README.md             项目运行时说明（install.py 落为 .cardsmith/README.md）

所有生成物都是**确定性**的：同一张卡片永远产出同样的字节。
check.py 靠这一点做逐字节比对，从而发现「卡片改了没重跑」和「生成物被手改」。

用法：
    python scripts/build.py                  # 生成全部
    python scripts/build.py --only <name>    # 只生成某张卡片
    python scripts/build.py --check          # 只比对不写盘（check.py 内部用）
    python scripts/build.py --prefix .cardsmith/    # 安装到项目时的路径前缀

退出码：0 = 成功/无差异，1 = 卡片有问题，2 = --check 模式下发现差异。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runtime_readme  # noqa: E402
import yamlmini  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CARDS_DIR = REPO_ROOT / "cards"
SKILLS_DIR = REPO_ROOT / "skills"
HOOKS_DIR = REPO_ROOT / "hooks"

REQUIRED_FIELDS = ("name", "stage", "description", "version")
VALID_STAGES = {"onboarding", "coding", "debugging", "delivery"}
WORKBUDDY_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PreCompact",
}
GIT_EVENTS = {"pre-commit", "commit-msg", "pre-push"}
# 各 git 事件的计划文件名约定：hooks/<event>-plan.json（pre-commit 也是这个规律，
# 所以不需要特判表 —— run_precommit.py 按同一约定反查）。
VALID_INPUTS = {"staged-files", "all-files", "none"}

GENERATED_HEADER = "<!-- 本文件由 scripts/build.py 从 cards/{card} 生成，请勿直接修改。 -->"
# 装到项目时运行时的目录名。与 scripts/install.py 的 RUNTIME_DIRNAME 保持一致。
RUNTIME_DIRNAME = ".cardsmith"


class CardError(Exception):
    """卡片本身有问题，无法生成。"""


# ────────────────────────── 卡片加载 ──────────────────────────


def yaml_quote(s: str) -> str:
    """压成单行并加双引号，确保 description 里的冒号、井号不会破坏 frontmatter。"""
    flat = " ".join(s.split())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_card(path: Path) -> dict:
    try:
        fm, body, _ = yamlmini.extract_frontmatter(path.read_text(encoding="utf-8"))
    except yamlmini.YamlError as exc:
        raise CardError(f"{path.name} frontmatter 解析失败：\n    {exc}") from exc

    if fm is None:
        raise CardError(f"{path.name} 缺少 frontmatter（文件必须以 --- 开头）")
    if not isinstance(fm, dict):
        raise CardError(f"{path.name} frontmatter 必须是一组键值对")

    for field in REQUIRED_FIELDS:
        if field not in fm or fm[field] in (None, ""):
            raise CardError(f"{path.name} 缺少必需字段：{field}")

    name = str(fm["name"])
    if name != path.stem:
        raise CardError(f"{path.name} 的 name 字段是 {name!r}，与文件名不一致")

    stage = str(fm["stage"])
    if stage not in VALID_STAGES:
        raise CardError(
            f"{path.name} 的 stage 非法：{stage!r}\n"
            f"    可选：{', '.join(sorted(VALID_STAGES))}"
        )

    if not isinstance(fm["version"], int):
        raise CardError(f"{path.name} 的 version 必须是整数")

    card = dict(fm)
    card["_file"] = path.name
    card["body"] = body.strip()
    card["tools"] = _normalize_list(fm.get("tools"), "tools", path)
    card["hooks"] = _normalize_list(fm.get("hooks"), "hooks", path)
    card["rules"] = _normalize_list(fm.get("rules"), "rules", path)
    return card


def _normalize_list(value, field: str, path: Path) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CardError(f"{path.name} 的 {field} 必须是列表")
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise CardError(f"{path.name} 的 {field}[{i}] 必须是映射（- key: value 形式）")
    return value


def load_all_cards(only: "list[str] | None" = None) -> "list[dict]":
    if not CARDS_DIR.is_dir():
        raise CardError(f"找不到卡片目录：{CARDS_DIR}")
    paths = sorted(p for p in CARDS_DIR.glob("*.md"))
    if only:
        wanted = set(only)
        paths = [p for p in paths if p.stem in wanted]
        missing = wanted - {p.stem for p in paths}
        if missing:
            raise CardError(f"cards/ 下不存在：{', '.join(sorted(missing))}")
    cards = [load_card(p) for p in paths]

    seen: dict = {}
    for c in cards:
        if c["name"] in seen:
            raise CardError(f"卡片名重复：{c['name']}")
        seen[c["name"]] = c
    return cards


# ────────────────────────── 校验（生成前） ──────────────────────────


def validate_for_build(card: dict) -> None:
    """生成前的最小校验。全量严格校验在 check.py。"""
    src = card["_file"]
    tool_ids = set()

    for i, tool in enumerate(card["tools"]):
        for field in ("id", "script"):
            if not tool.get(field):
                raise CardError(f"{src} tools[{i}] 缺少 {field}")
        tid = str(tool["id"])
        if tid in tool_ids:
            raise CardError(f"{src} tools[{i}].id 重复：{tid}")
        tool_ids.add(tid)
        if tool.get("inputs") and str(tool["inputs"]) not in VALID_INPUTS:
            raise CardError(
                f"{src} tools[{i}].inputs 非法：{tool['inputs']}\n"
                f"    可选：{', '.join(sorted(VALID_INPUTS))}"
            )

    for i, hook in enumerate(card["hooks"]):
        event = str(hook.get("event") or "")
        if event not in WORKBUDDY_EVENTS and event not in GIT_EVENTS:
            raise CardError(
                f"{src} hooks[{i}].event 非法：{event!r}\n"
                f"    可选：{', '.join(sorted(WORKBUDDY_EVENTS | GIT_EVENTS))}"
            )
        refs = hook.get("tools")
        if not refs:
            raise CardError(f"{src} hooks[{i}] 缺少 tools（必须引用至少一个 tool id）")
        for ref in refs:
            if ref not in tool_ids:
                raise CardError(f"{src} hooks[{i}].tools 引用了不存在的 tool id：{ref!r}")

    for i, rule in enumerate(card["rules"]):
        for field in ("id", "kind", "severity"):
            if not rule.get(field):
                raise CardError(f"{src} rules[{i}] 缺少 {field}")
        sev = str(rule["severity"])
        if sev not in ("error", "warn"):
            raise CardError(f"{src} rules[{i}].severity 非法：{sev!r}（可选 error / warn）")


# ────────────────────────── 渲染 ──────────────────────────


def _tool_script_path(script: str) -> str:
    return script


def with_prefix(path: str, prefix: str) -> str:
    """给 tools/ 或 rules/ 开头的相对路径加上前缀。前缀为空时原样返回。"""
    if not prefix:
        return path
    return f"{prefix.rstrip('/')}/{path}"


def render_skill_md(card: dict) -> str:
    parts = [
        "---",
        f"name: {card['name']}",
        f"description: {yaml_quote(str(card['description']))}",
        "agent_created: true",
        "---",
        "",
        card["body"],
        "",
    ]

    if card["tools"]:
        parts += [
            "## 绑定的工具",
            "",
            "> 本节由卡片自动派生，勿手改。改 tools 请改 `cards/"
            + card["_file"]
            + "`。",
            "",
            "| 工具 | 脚本 | 用途 | 输入 | 退出码 |",
            "|---|---|---|---|---|",
        ]
        for t in card["tools"]:
            parts.append(
                "| `{id}` | `{script}` | {desc} | {inputs} | {exit} |".format(
                    id=t["id"],
                    script=t["script"],
                    desc=t.get("desc", "—"),
                    inputs=t.get("inputs", "staged-files"),
                    exit=str(t.get("exit", "—")).replace("|", "\\|"),
                )
            )
        parts.append("")

    if card["hooks"]:
        parts += [
            "## 绑定的钩子",
            "",
            "> 本节由卡片自动派生，勿手改。",
            "",
            "| 事件 | 匹配 | 工具 | 阻断 | 超时 |",
            "|---|---|---|---|---|",
        ]
        for h in card["hooks"]:
            parts.append(
                "| `{event}` | {matcher} | {tools} | {blocking} | {timeout}s |".format(
                    event=h["event"],
                    # 表格单元格里的 | 必须转义，哪怕是反引号包着的 ——
                    # GFM 仍会把它当单元格分隔符，导致整行错位
                    matcher=f"`{str(h['matcher']).replace('|', chr(92) + '|')}`" if h.get("matcher") else "—",
                    tools=", ".join(f"`{r}`" for r in h["tools"]),
                    blocking="是" if h.get("blocking") else "否",
                    timeout=h.get("timeout", 15),
                )
            )
        parts.append("")

    if card["tools"]:
        # 卡片里的 tools/、rules/ 是**相对运行时根**的约定路径（见 render_registry）。
        # 技能装到 ~/.workbuddy/skills/ 后单独看这份 SKILL.md，读者无从得知
        # 「运行时根」是什么，照抄命令会在项目根报 No such file —— 所以把这条
        # 隐式约定显式派生出来，而不是让它只存在于生成器的注释里。
        parts += [
            "## 运行时约定",
            "",
            "> 本节由卡片自动派生，勿手改。",
            "",
            "本文与上表中的 `tools/`、`rules/` 路径一律**相对运行时根**，不是相对项目根：",
            "",
            "- 在套件仓库内维护：运行时根 = 仓库根",
            f"- 在已安装的项目里使用：运行时根 = `<项目>/{RUNTIME_DIRNAME}/`"
            f" —— 先 `cd {RUNTIME_DIRNAME}` 再照抄命令，"
            f"或把路径写成 `{RUNTIME_DIRNAME}/tools/...`",
            "",
        ]

    parts.append(GENERATED_HEADER.format(card=card["_file"]))
    parts.append("")
    return "\n".join(parts)


def render_registry(cards: "list[dict]", prefix: str) -> dict:
    """工具注册表。hooks/run.py 按本表定位脚本。

    路径一律**相对运行时根**（即 run.py 所在目录的上一级），不加 prefix：
        · 套件自身：运行时根 = 仓库根，脚本就在 tools/x.py
        · 装到项目：运行时根 = <项目>/.cardsmith，脚本在 .cardsmith/tools/x.py
    两处都写成 tools/x.py 即可 —— prefix 只管 hooks.json 里 run.py 自己的路径。
    踩过的坑：早期版本给 script 也加了 prefix，结果 run.py 再拼一次变成
    .cardsmith/.cardsmith/tools/...，脚本永远找不到。
    """
    registry: dict = {"description": "由 scripts/build.py 生成，勿手改。hooks/run.py 按本表定位脚本。", "tools": {}}
    for card in cards:
        for tool in card["tools"]:
            tid = str(tool["id"])
            if tid in registry["tools"]:
                raise CardError(f"tool id 在多个卡片中重复：{tid}（hook 无法区分该跑哪个）")
            registry["tools"][tid] = {
                "card": card["name"],
                "script": str(tool["script"]),
                "args": [str(a) for a in (tool.get("args") or [])],
                "inputs": tool.get("inputs", "staged-files"),
            }
    return registry


def _is_repo_path(s: str) -> bool:
    """卡片里的仓库相对路径约定：以 tools/ 或 rules/ 开头。

    注意：这个约定用于校验卡片书写规范，**不用于给路径加前缀** ——
    生成物里的路径一律相对运行时根，见 render_registry 的说明。
    """
    return s.startswith("tools/") or s.startswith("rules/")


def render_hooks_json(cards: "list[dict]", prefix: str) -> dict:
    """生成 WorkBuddy 钩子配置。git 的 pre-commit 不在这里，它走 .githooks。"""
    runner = "${CODEBUDDY_PROJECT_DIR}/" + with_prefix("hooks/run.py", prefix)
    events: dict = {}

    for card in cards:
        for hook in card["hooks"]:
            event = str(hook["event"])
            if event in GIT_EVENTS:
                continue
            entry_block = events.setdefault(event, [])
            matcher = hook.get("matcher")
            group = None
            for existing in entry_block:
                if existing.get("matcher") == matcher:
                    group = existing
                    break
            if group is None:
                group = {"hooks": []}
                if matcher:
                    group["matcher"] = matcher
                entry_block.append(group)

            for tid in hook["tools"]:
                tool = next(t for c in cards for t in c["tools"] if str(t["id"]) == tid)
                desc = tool.get("desc") or f"{card['name']} 的检查"
                # blocking 是「钩子 × 工具」这一对儿的属性，不是工具自身的属性：
                # 同一个工具可以在编辑后仅提示、在提交前阻断。用命令行参数传下去。
                command = f'python "{runner}" {tid}'
                if hook.get("blocking"):
                    command += " --blocking"
                    desc += "（违规即阻断）"
                group["hooks"].append(
                    {
                        "type": "command",
                        "command": command,
                        "description": desc,
                        "timeout": int(hook.get("timeout", 15)),
                    }
                )

    return {
        "description": "由 scripts/build.py 从 cards/ 生成，勿手改。改绑定关系请改卡片后重跑 build。",
        "hooks": events,
    }


def render_git_plan(cards: "list[dict]", prefix: str, event: str) -> dict:
    """某个 git 事件（pre-commit / commit-msg / pre-push）要跑哪些工具。
    .githooks/<event> 读对应的 hooks/<event>-plan.json。

    与 registry 同理：script/args 都相对运行时根，不加 prefix。
    """
    entries: list = []
    seen: set = set()
    for card in cards:
        for hook in card["hooks"]:
            if str(hook["event"]) != event:
                continue
            for tid in hook["tools"]:
                if tid in seen:
                    continue
                seen.add(tid)
                tool = next(t for c in cards for t in c["tools"] if str(t["id"]) == tid)
                entries.append(
                    {
                        "id": tid,
                        "script": str(tool["script"]),
                        "args": [str(a) for a in (tool.get("args") or [])],
                        "inputs": tool.get("inputs", "staged-files"),
                        "blocking": bool(hook.get("blocking")),
                        "card": card["name"],
                    }
                )
    return {
        "description": f"由 scripts/build.py 生成，勿手改。.githooks/{event} 按 blocking 决定是否中断。",
        "tools": entries,
    }


def render_precommit_plan(cards: "list[dict]", prefix: str) -> dict:
    """向后兼容别名：pre-commit 计划的生成已并入 render_git_plan。"""
    return render_git_plan(cards, prefix, "pre-commit")


def tree(cards: "list[dict]", prefix: str) -> "dict[str, str]":
    """算出所有生成物的内容。key 是相对仓库根的路径，value 是文本内容。

    纯函数 —— check.py 直接拿它跟磁盘比对，不落盘。
    """
    files: dict = {}
    for card in cards:
        files[f"skills/{card['name']}/SKILL.md"] = render_skill_md(card)
    files["hooks/registry.json"] = json.dumps(render_registry(cards, prefix), ensure_ascii=False, indent=2) + "\n"
    files["hooks/hooks.json"] = json.dumps(render_hooks_json(cards, prefix), ensure_ascii=False, indent=2) + "\n"
    for event in sorted(GIT_EVENTS):
        files[f"hooks/{event}-plan.json"] = (
            json.dumps(render_git_plan(cards, prefix, event), ensure_ascii=False, indent=2) + "\n"
        )
    files["RUNTIME-README.md"] = runtime_readme.render_runtime_readme(cards, prefix)
    return files


def write_all(files: "dict[str, str]") -> "list[str]":
    written = []
    for rel, content in files.items():
        path = REPO_ROOT / rel
        old = path.read_text(encoding="utf-8") if path.is_file() else None
        if old == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        written.append(rel)
    return written


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="卡片 → 生成物")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="只处理指定卡片")
    ap.add_argument("--check", action="store_true", help="只比对不写盘（有差异返回 2）")
    ap.add_argument("--prefix", default="", help="路径前缀，安装到项目时用 .cardsmith/")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        cards = load_all_cards(args.only)
        if not cards:
            print("[!] cards/ 下没有卡片，无事可做。")
            return 0
        for card in cards:
            validate_for_build(card)
        files = tree(cards, args.prefix)
    except CardError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1

    if args.check:
        drift = []
        for rel, content in files.items():
            path = REPO_ROOT / rel
            if not path.is_file():
                drift.append((rel, "缺失"))
            elif path.read_text(encoding="utf-8") != content:
                drift.append((rel, "内容不一致"))
        if drift:
            print("[x] 生成物与卡片不一致：")
            for rel, why in drift:
                print(f"    {rel} —— {why}")
            return 2
        if not args.quiet:
            print(f"[v] 生成物与卡片一致（{len(cards)} 张卡片，{len(files)} 个产物）")
        return 0

    written = write_all(files)
    if written:
        print(f"[+] 已更新 {len(written)} 个产物：")
        for rel in written:
            print(f"    {rel}")
    elif not args.quiet:
        print(f"[v] 产物已是最新（{len(cards)} 张卡片）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
