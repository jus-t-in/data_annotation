# Git 工作流

本仓库采用适合独立开发的轻量 GitHub Flow：重要改动从短期分支进入 Pull Request（PR），经自审后 squash 到 `main`。Git Graph 用于观察历史与比较提交，VS Code Source Control 用于审查、暂存和解决冲突，GitHub Issue 与 PR 使用 `gh` CLI 管理。

## 管理边界

- Git 只记录源码、文档和经过审核的最小测试样例。
- 原始传感器 CSV、标注文件、标注项目、QA 报告和导出图放在仓库外；确需临时放在仓库内时使用 `.local-data/`。
- `tests/fixtures/` 是唯一允许提交匿名、小体积 CSV 的位置；提交前仍须人工检查内容。
- 标注项目中的 SQLite 与不可变标注修订版是业务审计记录，不以 Git commit 代替。术语见 `CONTEXT.md`，存储决策见 `docs/adr/0028-sqlite-project-and-immutable-revisions.md`。
- Issue 操作与标签分别遵循 `docs/agents/issue-tracker.md` 和 `docs/agents/triage-labels.md`。

## 先看懂四个位置

```text
工作区 -> 暂存区 -> 本地 commit -> origin（GitHub）
 修改      本次要提交      本地历史        共享历史
```

VS Code 的 `Changes` 是尚未暂存的修改，`Staged Changes` 是下一次 commit 的准确内容。Git Graph 显示 commit、分支和标签，不代替暂存区检查。

## 日常开发闭环

### 1. 建立 Issue

功能、Bug、重构和多文件修改必须先有 Issue；纯拼写修正可以省略。标题和验收标准使用 `CONTEXT.md` 中的领域术语。

```bash
gh auth status
gh issue create --title "<目标>" --body "<背景、目标和验收标准>"
```

需求完整后，人工开发使用 `ready-for-human`，交由 agent 实现时使用 `ready-for-agent`。当前认证失效时先运行 `gh auth login`，不要绕过 Issue 入口。

### 2. 从最新的 main 建分支

先确认工作区没有需要保留的未提交修改，再更新 `main`：

```bash
git status --short --branch
git switch main
git pull --ff-only
git switch -c feat/42-event-dragging
```

分支名使用 `<类型>/<Issue编号>-<英文短名>`：

| 类型 | 用途 |
| --- | --- |
| `feat` | 新功能或行为变化 |
| `fix` | Bug 修复 |
| `refactor` | 不改变外部行为的重构 |
| `docs` | 文档 |
| `test` | 测试 |
| `chore` | 维护任务 |

在 VS Code 中，可单击状态栏的分支名并选择创建分支；创建后在 Git Graph 中确认 `HEAD` 和新分支位于同一节点。

### 3. 审查并暂存

在 Source Control 中逐个打开文件的 diff，只暂存属于同一可验证目标的文件或代码块。对应命令为：

```bash
git diff
git add <path>
git diff --staged
```

一个 commit 只表达一个可独立解释、验证和撤销的目标；格式调整和无关修复另建 commit。

### 4. 执行检查

所有修改都检查暂存 diff、敏感信息、真实数据和空白错误：

```bash
git diff --staged
git diff --check
git status --short
```

按改动范围追加检查：

| 改动 | 检查 |
| --- | --- |
| Python | `python -m pytest` |
| 前端 | `npm --prefix frontend run build` |
| 安装或 CLI | `python -m pip check`，并运行相关命令的 `--version` 或 `--help` |
| 纯文档 | 核对链接、路径和命令 |

当前仓库没有 CI；以上检查由提交者在合并前执行并写入 PR。

### 5. Commit

提交信息使用英文类型和可选作用域，加简洁中文说明：

```text
feat(annotation): 支持事件拖拽
fix(qa): 修正审核状态判断
docs(git): 补充合并流程
```

在 Source Control 输入信息并提交，或运行：

```bash
git commit -m "feat(annotation): 支持事件拖拽"
```

只有 `Staged Changes` 会进入 commit。提交后在 Git Graph 中单击新节点，复核文件清单和 diff。

### 6. Push、PR 与自审

首次推送会建立本地分支与远端分支的跟踪关系：

```bash
git push -u origin feat/42-event-dragging
gh pr create --title "feat(annotation): 支持事件拖拽" --body "<摘要、验证结果；Closes #42>"
```

Git Graph 也可从分支菜单打开预填的 GitHub PR 页面。PR 合并前必须：

- 在 Files Changed 或 Git Graph 比较视图中完成自审；
- 确认 PR 只解决一个 Issue；
- 记录实际运行的检查及结果；
- 使用 `Closes #42` 关联并在合并后关闭 Issue；
- 让 PR 标题符合 commit 格式，因为 squash 后它形成 `main` 的提交标题。

使用 squash merge 并删除远端分支：

```bash
gh pr merge --squash --delete-branch
```

随后更新本地 `main`：

```bash
git switch main
git pull --ff-only
git fetch --prune
```

先确认 PR 已合并且 squash commit 已在 `main`，再删除本地分支。`git branch -d <branch>` 因 squash 拒绝时，复核无遗漏后才使用 `git branch -D <branch>`。

## Git Graph 检查法

- `HEAD` 表示当前检出的 commit；分支标签表示各分支当前指向的位置。
- `main` 与 `origin/main` 位于同一节点，表示本地记录与最近一次 fetch 后的远端状态一致。
- 本地分支与同名 `origin/...` 位于同一节点，表示没有待 push 或待 pull 的 commit。
- 单击 commit 查看文件和 diff；先选一个 commit，再按 `Ctrl`/`Cmd` 选择另一个 commit，可比较两点。
- Fetch 只更新远端跟踪信息，不改工作区；适合先观察再决定是否 pull 或 rebase。
- 右键菜单可执行 rebase、reset、revert 等高影响操作；对 `main` 只使用本文件明确允许的操作。

Squash 合并后的健康状态是：`main` 与 `origin/main` 指向同一个新 commit，功能分支已删除，一个 PR 在主线上对应一个清晰节点。

## 同步自己的功能分支

分支应保持短期，通常无需中途同步。确需吸收新的 `main` 时，只 rebase 自己的功能分支：

```bash
git fetch origin
git rebase origin/main
```

在 VS Code Merge Editor 解决冲突，暂存解决后的文件，再继续：

```bash
git rebase --continue
```

无法确认结果时回到 rebase 前：

```bash
git rebase --abort
```

分支已经推送过时，rebase 后只使用：

```bash
git push --force-with-lease
```

只整理自己尚未合并的功能分支；共享历史和 `main` 不做 rebase。

## 撤销选择

先判断修改是否已经共享：未共享历史可以整理，已 push 或已进入 `main` 的历史只追加修复。

| 状态 | VS Code / Git Graph | 命令 | 边界 |
| --- | --- | --- | --- |
| 未暂存修改 | Discard Changes | `git restore -- <path>` | 会丢弃工作区内容，先确认无需保留 |
| 已暂存、未提交 | Unstage Changes | `git restore --staged <path>` | 修改仍留在工作区 |
| 最近一次未 push commit | Commit Staged (Amend) | `git commit --amend` | 只整理自己的本地历史 |
| 取消最近一次未 push commit | 终端 | `git reset --soft HEAD~1` | commit 消失，修改保留在暂存区 |
| 已 push 或已合入 `main` | Revert Commit | `git revert <hash>` | 创建反向 commit，不改写历史 |
| 临时切换任务 | Stash | `git stash push -u -m "<说明>"` | 用 `git stash pop` 恢复并检查冲突 |
| merge / rebase 冲突失控 | Abort | `git merge --abort` / `git rebase --abort` | 回到操作开始前 |

不对 `main` 使用 hard reset，也不使用普通 force push。

## 数据防线

`.gitignore` 是最后一道防线，不代替提交前审查：

```bash
git status --short
git diff --staged --stat
git check-ignore -v .local-data/example/project.sqlite3
```

不要用 `git add -f` 强行提交被忽略的数据。确需新增 CSV 测试样例时，将匿名、最小化的文件放入 `tests/fixtures/` 并在 PR 中说明用途。若真实数据已经进入历史，停止 push 并单独制定清理方案；新增 `.gitignore` 规则不会删除历史内容。

## 发布标签

仅在 `main` 已更新、检查通过且版本可交付时创建 annotated tag：

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

使用语义化版本：修复提升 patch，向后兼容功能提升 minor，不兼容变化提升 major。已推送标签不移动、不复用。

## 官方参考

- [VS Code Source Control](https://code.visualstudio.com/docs/sourcecontrol/overview)
- [Git Graph](https://github.com/mhutchie/vscode-git-graph)
- [GitHub PR 合并方式](https://docs.github.com/en/pull-requests/reference/pull-request-merges)
