# 真实案例记录

本文件只收**真实踩过的坑**：每次都按「症状 → 根因 → 解法 → 教训」四段记。
它是未来卡片的弹药库 —— 哪个案例被反复用到，就该升格成一张卡片或一条规则。

只增不改。案例事后发现有误，追加勘误条目，不改原文。

---

## CASE-001 except-pass 用正则查不准（2026-09-24）

- **症状**：`check_debug_smells.py` 自检用例「pass 后面有其他语句不算
  except-pass」失败 —— `except Exception:` 块里 `pass` 之后跟着 `log.info(...)`
  的代码，被正则误判为吞异常。
- **根因**：「except 块里是否只有 pass」取决于**缩进层级** —— pass 之后下一条
  语句缩进 <= except 行才算出了块。纯正则表达不了这种块结构判断。
- **解法**：except-pass 从 rules JSON 的正则模式改为**代码内置检测器**
  （`_builtin_except_pass`，逐行扫缩进），规则文件用 `"builtin": ["except-pass"]`
  声明启用；正则只保留真正适合行级匹配的模式（裸 except、空 catch）。
- **教训**：涉及块结构（缩进、配对括号）的规则不要用正则硬凑 —— 正则管形状的
  「行级」部分，块级判断交给代码。自检用例必须包含边界形状，这次就是靠
  「pass 后跟同块语句」这条边界用例抓出来的。

## CASE-002 commit 门禁的时机选错会查不到东西（2026-09-24）

- **症状**：最初打算在 pre-commit 阶段校验 commit message 格式。
- **根因**：git 流程里 message 在 pre-commit **之后**才被写出来 —— pre-commit
  阶段 `.git/COMMIT_EDITMSG` 里是上一次提交的残留，校验它等于校验历史。
- **解法**：套件扩展 `commit-msg` git 事件：git 把 message 文件路径作为 `$1`
  传给钩子，执行体转发成工具的 `--msg-file` 参数。plan 文件按
  `hooks/<event>-plan.json` 约定生成。
- **教训**：门禁时机选「要检查的信息**存在的最早时刻**」—— 提前了查不到，
  推后了修复成本变高（commit-msg 拦下只需改 message 重提交，push 后才发现
  就要 amend / rebase）。
