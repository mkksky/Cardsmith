#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_sync_state.py —— 多机同步状态检查

多台机器通过同一远端仓库（Gitee 等）接力干活时的经典事故：
A 机 push 前，B 机已经推了新提交。A 机如果只信 `git status`（clean）就开工，
等于在过期的历史上写代码 —— push 被拒算是幸运，--force 推上去才是灾难。

本工具做**独立对撞**：不信任任何本地缓存（remote-tracking ref 可能过期），
直接 `git ls-remote` 问远端要当前 SHA，和本地 HEAD 硬碰硬比对，输出证据表：

    本地 HEAD    本机现在站在哪个提交上
    远端 SHA     远端分支此刻真实指向（ls-remote 现查，非 fetch 缓存）
    ahead/behind 本地相对远端追踪分支的差量

远端名**自动识别**，不写死 origin（远端叫 gitee / upstream / 中文名时写死 origin
会让 ls-remote 直接失败，被当成"网络不可达"静默放行 —— 守卫看着在跑，其实从没
对撞过远端）。识别顺序：
    1. 当前分支配置的 branch.<name>.remote —— git 自己给的答案，最权威
    2. 仓库里唯一的一个远端 —— 没有选择就没有歧义
    3. 兜底 origin
显式传 --remote 则完全按传的来，不做猜测。

判定：
    远端 == HEAD        → 已同步
    仅 ahead（领先）    → 正常，可以推
    behind 或分叉       → 违规：先 pull --rebase，不许直接开工/强推
    远端分支不存在       → 新分支，通过（提示确认 remote/分支名没写错）
    网络不可达          → 放行并警告（push 本身也会因网络失败，此处不重复拦截）
    追踪分支不可信       → 阻断：ahead/behind 需要用本地追踪分支算，它过期或缺失时
                          算出来的差量会低估 behind，把落后的本地读成"领先可推送"。
                          此时宁可挡一下，让人先 git fetch 拿到真实状态。

被卡片引用：
    cards/multi-machine-sync-guard.md

用法：
    python tools/check_sync_state.py                    # 当前分支 vs 自动识别的远端
    python tools/check_sync_state.py --remote upstream --branch main
    python tools/check_sync_state.py --selftest

退出码：0=状态允许继续 1=有分叉/落后/无法判定，需先同步 2=配置错误
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TIMEOUT_GIT = 60


class GitError(Exception):
    pass


def _run(args: "list[str]", root: Path, timeout: int = TIMEOUT_GIT) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git 命令失败：{exc}") from exc
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args[:4])} 失败：{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _try_run(args: "list[str]", root: Path, timeout: int = TIMEOUT_GIT) -> "str | None":
    """跑 git，失败返回 None 而不是抛。用于「取不到也算正常」的探测。"""
    try:
        return _run(args, root, timeout) or None
    except GitError:
        return None


# ────────────────────────── 纯函数（自检覆盖） ──────────────────────────


def parse_lsremote(raw: str, ref: str) -> "str | None":
    """从 ls-remote 输出里取指定 ref 的 SHA。找不到返回 None。
    注意 refs/heads/x 要精确匹配，不能被 refs/heads/x-v2 混进去 —— 踩过。"""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, name = line.partition("\t")
        if name.strip() == ref:
            return sha.strip()
    return None


def pick_remote(tracking_remote: str, remotes: "list[str]") -> str:
    """未显式指定 --remote 时，挑一个合理的远端名。

    tracking_remote 是 `branch.<当前分支>.remote` 的值（可能为空）。
    """
    if tracking_remote and tracking_remote.strip():
        return tracking_remote.strip()
    if len(remotes) == 1:
        return remotes[0]
    return "origin"


def decide(
    local_head: "str | None",
    remote_sha: "str | None",
    ahead: int,
    behind: int,
    counts_ok: bool = True,
) -> str:
    """根据证据给判定。返回值：
    synced / ahead-ok / behind / diverged / new-branch / no-commits / unknown

    counts_ok=False 表示 ahead/behind 不可信（本地追踪分支缺失，或与远端真实 SHA
    不一致即已过期）。此时若两边 SHA 又不相等，绝不能默认判成 ahead ——
    落后的本地会被读成"领先可推送"，正是本工具要防的那类假放行。
    """
    if remote_sha is None:
        return "new-branch"
    if local_head is None:
        return "no-commits"
    if local_head == remote_sha:
        return "synced"
    if not counts_ok:
        return "unknown"
    if behind > 0 and ahead > 0:
        return "diverged"
    if behind > 0:
        return "behind"
    return "ahead-ok"


_DECIDE_MSG = {
    "synced": ("[v] 本地与远端完全一致（SHA 硬撞相等）", 0),
    "ahead-ok": ("[v] 本地领先于远端，正常可推送状态", 0),
    "behind": ("[x] 本地落后于远端 —— 另一台机器已经推过了。先 git pull --rebase 再继续", 1),
    "diverged": ("[x] 本地与远端分叉（双方都有对方没有的提交）。先 git pull --rebase，绝不许 --force", 1),
    "new-branch": ("[!] 远端还没有这个分支（新分支）。确认 remote/分支名没写错后可继续", 0),
    "no-commits": ("[!] 当前分支还没有任何提交", 0),
    "unknown": (
        "[x] 无法判定领先/落后：本地追踪分支缺失或已过期（与远端真实 SHA 不一致）。\n"
        "    这时 ahead/behind 会低估 behind，落后的本地可能被读成可推送。\n"
        "    先 git fetch --prune 把远端状态取回来，再重试。",
        1,
    ),
}


# ────────────────────────── 证据收集 ──────────────────────────


def default_remote(root: Path, branch: str) -> "tuple[str, bool]":
    """推断远端名。返回 (远端名, 是否为自动识别出来的)。

    取不到任何线索时退回 origin，并标记为非自动 —— 这样报错信息里能说清
    「origin 是你没配远端，不是我猜错了」。
    """
    tracking = _try_run(["config", "--get", f"branch.{branch}.remote"], root) or ""
    raw = _try_run(["remote"], root) or ""
    remotes = [line.strip() for line in raw.splitlines() if line.strip()]
    name = pick_remote(tracking, remotes)
    auto = bool(tracking.strip()) or len(remotes) == 1
    return name, auto


def collect(root: Path, remote: str, branch: str) -> dict:
    """收集全部证据。网络错误单独标记，不与本地错误混在一起。"""
    info: dict = {"remote": remote, "branch": branch}
    info["head"] = _try_run(["rev-parse", "HEAD"], root)  # 空仓库 / detached 时取不到
    info["current_branch"] = _try_run(["rev-parse", "--abbrev-ref", "HEAD"], root)

    try:
        raw = _run(["ls-remote", remote, f"refs/heads/{branch}"], root, timeout=TIMEOUT_GIT)
        info["remote_sha"] = parse_lsremote(raw, f"refs/heads/{branch}")
        info["network_ok"] = True
    except GitError as exc:
        info["remote_sha"] = None
        info["network_ok"] = False
        info["network_error"] = str(exc)

    info["ahead"], info["behind"] = 0, 0
    info["tracking_sha"] = None
    info["counts_ok"] = False

    if info["network_ok"] and info["remote_sha"] is not None:
        # ahead/behind 只能用本地追踪分支算 —— ls-remote 给的是远端的 SHA 字符串，
        # 没有本地对象，算不了差量。所以先确认这个追踪分支可信：
        # 它必须存在，且与远端此刻的真实 SHA 相等（否则就是过期缓存）。
        tracking = _try_run(["rev-parse", "--verify", f"{remote}/{branch}"], root)
        info["tracking_sha"] = tracking
        if tracking == info["remote_sha"]:
            try:
                counts = _run(
                    ["rev-list", "--left-right", "--count", f"HEAD...{remote}/{branch}"], root
                )
                left, right = counts.split()
                info["ahead"], info["behind"] = int(left), int(right)
                info["counts_ok"] = True
            except (GitError, ValueError):
                info["counts_ok"] = False
        # tracking 与远端不一致 → counts_ok 保持 False，交给 decide 判 unknown
    return info


# ────────────────────────── 自检 ──────────────────────────


def _selftest() -> int:
    failures = 0
    checks = 0

    def case(name: str, got: str, expect: str) -> None:
        nonlocal failures, checks
        checks += 1
        if got == expect:
            print(f"  [v] {name}")
        else:
            print(f"  [x] {name}\n      期望 {expect}，实际 {got}")
            failures += 1

    SHA_A = "a" * 40
    SHA_B = "b" * 40

    # parse_lsremote
    raw = f"{SHA_A}\trefs/heads/main\n{SHA_B}\trefs/heads/main-v2\n"
    case("ls-remote 精确匹配 ref", parse_lsremote(raw, "refs/heads/main") or "", SHA_A)
    case("ls-remote 不被相似 ref 混淆", parse_lsremote(raw, "refs/heads/main-v2") or "", SHA_B)
    case("ls-remote 找不到 ref", parse_lsremote(raw, "refs/heads/nope") or "None", "None")

    # pick_remote —— 远端名不该被写死
    case("有上游配置 → 用上游", pick_remote("okx自动交易平台", ["okx自动交易平台", "other"]), "okx自动交易平台")
    case("无上游 + 单远端 → 用该远端", pick_remote("", ["okx自动交易平台"]), "okx自动交易平台")
    case("无上游 + 多远端 → 兜底 origin", pick_remote("", ["a", "b"]), "origin")
    case("无任何远端 → 兜底 origin", pick_remote("", []), "origin")
    case("上游值只有空白 → 视为无", pick_remote("   ", ["solo"]), "solo")

    # decide 判定表 —— 这是本工具的核心语义
    case("SHA 相等 → synced", decide(SHA_A, SHA_A, 0, 0), "synced")
    case("仅 ahead → ahead-ok", decide(SHA_A, SHA_B, 3, 0), "ahead-ok")
    case("仅 behind → behind", decide(SHA_A, SHA_B, 0, 2), "behind")
    case("分叉 → diverged", decide(SHA_A, SHA_B, 3, 2), "diverged")
    case("远端无分支 → new-branch", decide(SHA_A, None, 0, 0), "new-branch")
    case("本地无提交 → no-commits", decide(None, SHA_A, 0, 0), "no-commits")
    case("差量为 0 但追踪可信 → 仍判 ahead-ok", decide(SHA_A, SHA_B, 0, 0), "ahead-ok")
    case("差量不可信 + SHA 不等 → unknown", decide(SHA_A, SHA_B, 0, 0, False), "unknown")
    case("差量不可信 + SHA 相等 → 仍判 synced", decide(SHA_A, SHA_A, 0, 0, False), "synced")

    # 判定消息与退出码映射
    checks += 1
    must_block = ("behind", "diverged", "unknown")
    bad = [k for k in must_block if _DECIDE_MSG[k][1] != 1]
    if bad:
        print(f"  [x] {'/'.join(bad)} 必须映射退出码 1")
        failures += 1
    else:
        print("  [v] behind/diverged/unknown 均映射为阻断（退出码 1）")

    print()
    if failures:
        print(f"自检失败：{failures} 项异常")
        return 1
    print(f"自检全绿：{checks}/{checks}")
    return 0


# ────────────────────────── 入口 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="多机同步状态检查（ls-remote 独立对撞）")
    ap.add_argument("--root", default=".")
    ap.add_argument("--remote", default=None, help="默认自动识别（分支上游 → 唯一远端 → origin）")
    ap.add_argument("--branch", help="默认取当前分支")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    root = Path(args.root).resolve()

    try:
        if not args.branch:
            args.branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], root)
        if args.branch in ("HEAD", ""):
            print("[x] 当前处于 detached HEAD 或空仓库，无法判定同步状态", file=sys.stderr)
            return 2
    except GitError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2

    remote_auto = False
    if args.remote is None:
        args.remote, remote_auto = default_remote(root, args.branch)

    try:
        info = collect(root, args.remote, args.branch)
    except GitError as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 2
    info["remote_auto"] = remote_auto

    if not info.get("network_ok", False):
        if not args.quiet:
            print(f"[!] 无法连接远端 {args.remote}，跳过同步检查：")
            print(f"    {info.get('network_error', '')}")
            if not remote_auto and args.remote == "origin":
                print("    提示：本仓库没有名为 origin 的远端。用 --remote 指定，或配好")
                print("          branch.<分支>.remote 后本工具会自动识别。")
            print("    push 本身也会因网络失败；网络恢复后建议手动跑一次本工具对撞。")
        return 0

    verdict_key = decide(
        info["head"], info["remote_sha"], info["ahead"], info["behind"], info.get("counts_ok", False)
    )
    verdict, code = _DECIDE_MSG[verdict_key]

    if not args.quiet:
        tag = "（自动识别）" if remote_auto else ""
        print(f"[i] 多机同步对撞（remote={info['remote']}{tag} branch={info['branch']}）")
        print(f"    本地 HEAD : {info['head'] or '（无提交）'}")
        print(f"    远端 SHA  : {info['remote_sha'] or '（分支不存在）'}   ← ls-remote 现查，非本地缓存")
        print(f"    追踪分支  : {info['tracking_sha'] or '（不存在）'}")
        if info.get("counts_ok"):
            print(f"    ahead/behind: {info['ahead']}/{info['behind']}")
        elif info["remote_sha"] is not None:
            print("    ahead/behind: 不可信（追踪分支过期）")
        print()
        print(verdict)

    return code


if __name__ == "__main__":
    sys.exit(main())
