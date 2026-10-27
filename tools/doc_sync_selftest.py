# -*- coding: utf-8 -*-
"""tools/doc_sync_selftest.py —— check_doc_sync 的自检用例

独立成文件的原因：主文件撞 size-naming 的 500 行门禁（pre-commit 实测）。
套件已有先例：scripts/yamlmini_selftest.py 同样独立。
跑法不变：python tools/check_doc_sync.py --selftest
"""

from pathlib import Path

from check_doc_sync import run_doc_checks
from doc_sync_checks import check_api_doc, check_path_refs, collect_doc_files
from doc_sync_new_file_selftest import run_selftest as run_new_file_selftest
from doc_sync_paths import is_skipped, parse_name_status
from doc_sync_reverse import api_docs_triggered, docs_referencing, ref_matches_staged


class _Args:
    """编排函数只用到 quiet，测试里给个最简替身。"""

    quiet = True


def run_selftest() -> int:
    import tempfile

    failures = 0
    ran = 0

    def case(name: str, expect: int, build) -> None:
        nonlocal failures, ran
        ran += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rules = build(root)
            issues = []
            for doc in collect_doc_files(root, rules):
                if not is_skipped(doc, rules["skip_paths"]):
                    issues += check_path_refs(doc, root, rules)
            for entry in rules.get("api_docs", []):
                issues += check_api_doc(entry, root, rules)
            ok = len(issues) == expect
            print(f"  [{'v' if ok else 'x'}] {name}")
            if not ok:
                print(f"      期望 {expect} 处，实际 {len(issues)}：{issues}")
                failures += 1

    def bool_case(name: str, expect: bool, actual: bool) -> None:
        nonlocal failures, ran
        ran += 1
        ok = expect == actual
        print(f"  [{'v' if ok else 'x'}] {name}")
        if not ok:
            print(f"      期望 {expect}，实际 {actual}")
            failures += 1

    def base_rules(**over):
        rules = {
            "doc_roots": ["docs"],
            "doc_extensions": [".md"],
            "source_roots": [],
            "path_suffixes": [".py", ".md", ".json"],
            "skip_paths": [],
            "allow_refs": [],
            "skip_symbols": ["print", "open"],
            "api_docs": [],
        }
        rules.update(over)
        return rules

    def w(root: Path, rel: str, content: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    case(
        "有效路径引用 → 通过",
        0,
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/run.py` 与 [指南](./guide.md)\n"),
            w(r, "scripts/run.py", "print(1)\n"),
            w(r, "docs/guide.md", "ok\n"),
            base_rules(),
        )[-1],
    )

    case(
        "过期路径引用 → 违规",
        1,
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/deleted.py`\n"),
            base_rules(),
        )[-1],
    )

    case(
        "URL 与锚点链接不查",
        0,
        lambda r: (
            w(
                r,
                "docs/a.md",
                "[x](https://example.com/a.md) [y](#section) [z](mail@example.com)\n",
            ),
            base_rules(),
        )[-1],
    )

    case(
        "非路径 code span（无斜杠无后缀）不查",
        0,
        lambda r: (
            w(
                r,
                "docs/a.md",
                "用 `get_klines(symbol, timeframe, limit=100)` 获取数据\n",
            ),
            base_rules(),
        )[-1],
    )

    case(
        "裸文件名默认不查（散文泛指）",
        0,
        lambda r: (
            w(r, "docs/a.md", "`check.py` 会报错，`SKILL.md` 是生成物\n"),
            base_rules(),
        )[-1],
    )

    case(
        "check_bare_names 开启后裸文件名真查",
        1,
        lambda r: (
            w(r, "docs/a.md", "`check.py` 会报错\n"),
            base_rules(check_bare_names=True),
        )[-1],
    )

    case(
        "source_roots 兜底解析",
        0,
        lambda r: (
            w(r, "docs/a.md", "见 `application/service.py`\n"),
            w(r, "pkg/application/service.py", "x = 1\n"),
            base_rules(source_roots=["pkg"]),
        )[-1],
    )

    case(
        "allow_refs 白名单放行",
        0,
        lambda r: (
            w(r, "docs/a.md", "将来会有 `tools/future.py`\n"),
            base_rules(allow_refs=["tools/future.py"]),
        )[-1],
    )

    case(
        "allow_ref_patterns 形态白名单放行（单位/数字比/证据目录）",
        0,
        lambda r: (
            w(
                r,
                "docs/a.md",
                "价格 `元/㎡`、`万元/亩→mu_ten_thousand`、`¥1500元/㎡/月`，"
                "权重 `28/24/19/15/9/5`，证据 `.local-evidence/x.js`\n",
            ),
            base_rules(
                allow_ref_patterns=[
                    r"^(?:¥?[\d.]*)?(?:万元|万|元)/(?:㎡|亩|月|天|年)(?:[·/](?:月|天|年))?(?:→\w+)?$",
                    r"^\d+(?:/\d+)+$",
                    r"^\.local-evidence(?:/.*)?$",
                ]
            ),
        )[-1],
    )

    case(
        "allow_ref_patterns 不放行无关的不存在路径",
        1,
        lambda r: (
            w(r, "docs/a.md", "见 `services/gone.js`，单位写作 `元/㎡`\n"),
            base_rules(allow_ref_patterns=[r"^(?:万元|元)/.*$"]),
        )[-1],
    )

    case(
        "skip_paths 命中的文档不查",
        0,
        lambda r: (
            w(r, "docs/generated/rep.md", "见 `no/such/file.py`\n"),
            base_rules(skip_paths=["docs/generated/**"]),
        )[-1],
    )

    case(
        "api 文档方法存在 → 通过",
        0,
        lambda r: (
            w(
                r,
                "docs/api.md",
                "### `get_klines(symbol, timeframe)`\n还有 `subscribe(symbol)`\n",
            ),
            w(
                r,
                "src/kline.py",
                "class KlineService:\n    def get_klines(self, a, b):\n        pass\n    def subscribe(self, a):\n        pass\n",
            ),
            base_rules(api_docs=[{"doc": "docs/api.md", "search_roots": ["src"]}]),
        )[-1],
    )

    case(
        "api 文档方法在代码里消失 → 违规",
        1,
        lambda r: (
            w(r, "docs/api.md", "### `get_history(symbol, timeframe)`\n"),
            w(
                r,
                "src/kline.py",
                "class KlineService:\n    def get_klines(self, a, b):\n        pass\n",
            ),
            base_rules(api_docs=[{"doc": "docs/api.md", "search_roots": ["src"]}]),
        )[-1],
    )

    case(
        "skip_symbols 过滤内置名",
        0,
        lambda r: (
            w(r, "docs/api.md", "用 `print(value)` 输出\n"),
            w(r, "src/x.py", "x = 1\n"),
            base_rules(api_docs=[{"doc": "docs/api.md", "search_roots": ["src"]}]),
        )[-1],
    )

    case(
        "声明的 api 文档整个丢失 → 违规",
        1,
        lambda r: (
            w(r, "src/x.py", "x = 1\n"),
            base_rules(api_docs=[{"doc": "docs/gone.md", "search_roots": ["src"]}]),
        )[-1],
    )

    case(
        "行号/符号/查询串后缀剥掉后按纯路径查",
        0,
        lambda r: (
            w(
                r,
                "docs/a.md",
                "见 `services/x.js:64`、`services/x.js:145-172`、"
                "`services/x.js:getFoo`、`pages/p/index?type=abc`\n",
            ),
            w(r, "services/x.js", "x = 1\n"),
            w(r, "pages/p/index.js", "x = 1\n"),
            base_rules(),
        )[-1],
    )

    case(
        "剥后缀仍不存在 → 违规（按剥完的路径报告）",
        1,
        lambda r: (
            w(r, "docs/a.md", "见 `services/gone.js:12`\n"),
            base_rules(),
        )[-1],
    )

    case(
        "pages/ 无扩展名路由补 .js 探测",
        0,
        lambda r: (
            w(r, "docs/a.md", "跳转 `/pages/client/list` 与 `pages/p/detail`\n"),
            w(r, "pages/client/list.js", "x = 1\n"),
            w(r, "pages/p/detail.js", "x = 1\n"),
            base_rules(),
        )[-1],
    )

    case(
        "cf_doc_bases 云函数文档邻域解析",
        0,
        lambda r: (
            w(r, "docs/05-cloudfunctions/foo/README.md", "见 `handlers/crud.js`\n"),
            w(r, "cloudfunctions/foo/handlers/crud.js", "x = 1\n"),
            base_rules(
                cf_doc_bases=[
                    {"doc_prefix": "docs/05-cloudfunctions/", "base_prefix": "cloudfunctions/"}
                ]
            ),
        )[-1],
    )

    case(
        "cf_doc_bases 在 monorepo 布局下按 source_root 拼接",
        0,
        lambda r: (
            w(r, "pkg/docs/05-cloudfunctions/foo/README.md", "见 `handlers/crud.js`\n"),
            w(r, "pkg/cloudfunctions/foo/handlers/crud.js", "x = 1\n"),
            base_rules(
                source_roots=["pkg"],
                cf_doc_bases=[
                    {"doc_prefix": "docs/05-cloudfunctions/", "base_prefix": "cloudfunctions/"}
                ],
            ),
        )[-1],
    )

    # ── 反向并入（2026-10-21）：代码改了 → 抓回引用它的文档 ──

    bool_case("反查匹配：路径全等", True, ref_matches_staged("tools/x.py", "tools/x.py"))
    bool_case(
        "反查匹配：文档邻域相对路径（docs/a.md 写 guide.md）",
        True,
        ref_matches_staged("guide.md", "docs/guide.md"),
    )
    bool_case(
        "反查匹配：source_roots 前缀省略",
        True,
        ref_matches_staged("application/x.py", "pkg/application/x.py"),
    )
    bool_case("反查匹配：目录引用命中其下文件", True, ref_matches_staged("tools/", "tools/x.py"))
    bool_case(
        "反查匹配：目录引用不误伤同前缀目录",
        False,
        ref_matches_staged("tools/", "tooling/x.py"),
    )
    bool_case("反查匹配：无关路径不命中", False, ref_matches_staged("tools/x.py", "docs/x.md"))

    def reverse_case(name: str, staged: list, expect_docs: list, build) -> None:
        """反查集成：文档引用了谁，谁就被并入。"""
        nonlocal failures, ran
        ran += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rules = build(root)
            hits = docs_referencing(staged, collect_doc_files(root, rules), root, rules)
            got = sorted(hits)
            ok = got == sorted(expect_docs)
            print(f"  [{'v' if ok else 'x'}] {name}")
            if not ok:
                print(f"      期望并入 {sorted(expect_docs)}，实际 {got}")
                failures += 1

    reverse_case(
        "反查集成：改代码命中引用它的文档",
        ["scripts/run.py"],
        ["docs/a.md"],
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/run.py`\n"),
            w(r, "scripts/run.py", "print(1)\n"),
            base_rules(),
        )[-1],
    )

    reverse_case(
        "反查集成：改无关文件不牵连文档",
        ["scripts/other.py"],
        [],
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/run.py`\n"),
            w(r, "scripts/run.py", "print(1)\n"),
            base_rules(),
        )[-1],
    )

    reverse_case(
        "反查集成：引用带行号后缀也命中",
        ["scripts/run.py"],
        ["docs/a.md"],
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/run.py:64`\n"),
            w(r, "scripts/run.py", "print(1)\n"),
            base_rules(),
        )[-1],
    )

    bool_case(
        "api 反查：暂存文件落在 search_roots 下触发对账",
        True,
        bool(
            api_docs_triggered(
                ["src/kline.py"],
                [{"doc": "docs/api.md", "search_roots": ["src"]}],
            )
        ),
    )
    bool_case(
        "api 反查：search_roots 外不触发",
        False,
        bool(
            api_docs_triggered(
                ["lib/kline.py"],
                [{"doc": "docs/api.md", "search_roots": ["src"]}],
            )
        ),
    )

    # ── 暂存清单解析 ──

    bool_case(
        "暂存清单：删除的路径也进清单（删文件正是漂移高发场景）",
        True,
        "a.py" in parse_name_status("D\0a.py\0"),
    )
    bool_case(
        "暂存清单：重命名的旧路径也进清单",
        True,
        "old.py" in parse_name_status("R100\0old.py\0new.py\0"),
    )
    bool_case(
        "暂存清单：新增/修改路径正常解析",
        True,
        parse_name_status("A\0a.py\0M\0b.py\0") == ["a.py", "b.py"],
    )

    # ── 编排：反向并入真的会拦下漂移 ──

    def orchestration_case(name: str, paths: list, expect_files: list, build) -> None:
        """暂存模式编排：改代码 → 引用它的文档被并入 → 过期引用被报出来。"""
        nonlocal failures, ran
        ran += 1
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rules = build(root)
            issues, checked = run_doc_checks(paths, root, rules, _Args(), True)
            got = sorted({i["file"] for i in issues})
            ok = got == sorted(expect_files)
            print(f"  [{'v' if ok else 'x'}] {name}")
            if not ok:
                print(f"      期望报出 {sorted(expect_files)}，实际 {got}（checked={sorted(checked)}）")
                failures += 1

    orchestration_case(
        "编排：暂存代码被删，引用它的白名单文档一并报漂移",
        ["scripts/gone.py"],
        ["docs/a.md"],
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/gone.py`\n"),
            base_rules(),
        )[-1],
    )

    orchestration_case(
        "编排：暂存代码没被文档引用，不牵连任何文档",
        ["scripts/other.py"],
        [],
        lambda r: (
            w(r, "docs/a.md", "见 `scripts/gone.py`\n"),
            base_rules(),
        )[-1],
    )

    orchestration_case(
        "编排：白名单外的文档不被并入（白名单纪律）",
        ["scripts/gone.py"],
        [],
        lambda r: (
            w(r, "notes/a.md", "见 `scripts/gone.py`\n"),
            base_rules(doc_roots=["docs"]),
        )[-1],
    )

    new_ran, new_failures = run_new_file_selftest()
    ran += new_ran
    failures += new_failures

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    total = ran
    print(f"自检全绿：{total}/{total}")
    return 0
