# -*- coding: utf-8 -*-
"""hooks/run_selftest.py —— run.py 版本对账逻辑的自检用例

跑法：python hooks/run.py --selftest
全部用例在临时目录里构造，不碰真实运行时。
"""

import json
import tempfile
from pathlib import Path

from run import check_runtime_version


def run_selftest() -> int:
    failures = 0

    def case(name: str, expect: bool, detail: str = "") -> None:
        nonlocal failures
        ok = expect
        print(f"  [{'v' if ok else 'x'}] {name}")
        if not ok:
            print(f"      {detail}")
            failures += 1

    # 1. 套件仓库自身：没有 runtime-state.json → 不警告
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case(
            "无 runtime-state.json（套件本尊）→ 不警告",
            check_runtime_version(root) is None,
        )

    # 2. 版本一致 → 不警告
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as sd:
        root, src = Path(td), Path(sd)
        (src / "VERSION").write_text("0.4.0\n", encoding="utf-8")
        (root / "runtime-state.json").write_text(
            json.dumps({"suite_version": "0.4.0", "source_repo": str(src)}),
            encoding="utf-8",
        )
        case("版本一致 → 不警告", check_runtime_version(root) is None)

    # 3. 运行时落后 → 警告且带两个版本号
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as sd:
        root, src = Path(td), Path(sd)
        (src / "VERSION").write_text("0.5.0\n", encoding="utf-8")
        (root / "runtime-state.json").write_text(
            json.dumps({"suite_version": "0.4.0", "source_repo": str(src)}),
            encoding="utf-8",
        )
        warn = check_runtime_version(root)
        case(
            "版本落后 → 警告含双版本号与重装指引",
            warn is not None and "0.4.0" in warn and "0.5.0" in warn and "install.py" in warn,
            f"warn={warn!r}",
        )

    # 4. 套件仓库不在了（拷到别的机器等）→ 不误报
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "runtime-state.json").write_text(
            json.dumps(
                {"suite_version": "0.4.0", "source_repo": str(root / "gone")}
            ),
            encoding="utf-8",
        )
        case("源仓库不存在 → 不误报", check_runtime_version(root) is None)

    # 5. state 坏了 / 字段缺失 → 不误报
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "runtime-state.json").write_text("{bad json", encoding="utf-8")
        case("state 文件损坏 → 不误报", check_runtime_version(root) is None)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "runtime-state.json").write_text("{}", encoding="utf-8")
        case("state 缺字段 → 不误报", check_runtime_version(root) is None)

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print("自检全绿：6/6")
    return 0
