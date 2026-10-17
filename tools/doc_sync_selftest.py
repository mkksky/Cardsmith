# -*- coding: utf-8 -*-
"""tools/doc_sync_selftest.py —— check_doc_sync 的自检用例

独立成文件的原因：主文件撞 size-naming 的 500 行门禁（pre-commit 实测）。
套件已有先例：scripts/yamlmini_selftest.py 同样独立。
跑法不变：python tools/check_doc_sync.py --selftest
"""

from pathlib import Path

from check_doc_sync import collect_doc_files, check_path_refs, check_api_doc, is_skipped


def run_selftest() -> int:
    import tempfile

    failures = 0

    def case(name: str, expect: int, build) -> None:
        nonlocal failures
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

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    total = 20
    print(f"自检全绿：{total}/{total}")
    return 0
