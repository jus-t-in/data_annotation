# Issue 跟踪器：GitHub

本仓库的 issue 和 PRD 均作为 GitHub issue 管理。所有操作使用 `gh` CLI。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`
- **读取 issue**：`gh issue view <number> --comments`
- **列出 issue**：使用 `gh issue list`，并按需指定状态、标签和 JSON 过滤条件
- **评论**：`gh issue comment <number> --body "..."`
- **添加或移除标签**：`gh issue edit <number> --add-label "..."` 或 `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

通过 `git remote -v` 推断仓库；在克隆目录中运行时，`gh` 会自动完成此操作。

## 将 Pull Request 作为 triage 入口

**PRs as a request surface: no.**（不将 PR 作为 triage 请求入口。）

## Skill 操作语义

- “发布到 issue 跟踪器”：创建一个 GitHub issue。
- “获取相关 ticket”：运行 `gh issue view <number> --comments`。
- 单独出现的 `#42` 可能是 issue 或 PR；先尝试 `gh pr view 42`，再尝试 `gh issue view 42`。

## Wayfinding 操作

- **地图（Map）**：一个带有 `wayfinder:map` 标签的 issue。
- **子 ticket**：GitHub 子 issue；若不可用，则使用任务列表条目并注明 `Part of #<map>`。
- **阻塞关系**：使用原生 issue 依赖；若不可用，则添加 `Blocked by: #<n>`。
- **前沿查询（Frontier query）**：选择第一个仍开放、未分配且没有开放阻塞项的子 ticket。
- **认领**：`gh issue edit <n> --add-assignee @me`。
- **解决**：评论处理结果、关闭 ticket，然后更新地图。
