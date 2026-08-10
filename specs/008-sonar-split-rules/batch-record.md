# 批次记录：SonarQube 清理 + 大文件拆分 + 新流程规则

日期：2026-08-10
状态：本地实现与验证完成，收口处理待用户授权

## 这次做了什么

- 接入并运行 SonarQube 扫描，新增 `sonar-project.properties`；`.gitignore` 增加 `.scannerwork/` 与 `coverage.xml`。
- 拆分超大文件：
  - `webui/store.py`：7008 行 -> 4773 行，迁移逻辑拆到 `webui/store_migrations.py`（2126 行）、`webui/store_helpers.py`（158 行）。
  - `webui/app.py`：8886 行 -> 8107 行，任务运行与工作台编排拆到 `webui/task_runners.py`（864 行）。
  - 拆分只搬代码，保留原入口与接口，不改变数据库结构。
- 落地新流程规则：全局规则加入 `speckit-constitution -> speckit-clarify（按需）-> speckit-specify -> speckit-plan -> speckit-tasks -> speckit-implement -> speckit-converge`；项目 `AGENTS.md` 新增“功能开发流程与架构边界”；plan/tasks 模板增加 File Boundaries；`.specify/memory/constitution.md` 定稿 1.0.0。
- 同步构建与卫生配置：`pyproject.toml` 增加 dev dependency group，`vite.config.ts` 调整排序写法，卫生测试保留未跟踪文件门禁。

## 为什么拆

- `webui/store.py` 与 `webui/app.py` 都超过单文件规模边界，继续在入口或 Store 中追加业务逻辑会放大耦合与回归风险。
- 新流程要求功能先冻结需求，再按 Constitution/Plan/Tasks 分片实现，文件边界必须落在计划与任务模板中。

## 验证结果（2026-08-10 最终代码）

- 后端全量：1990 tests OK，3 skipped；排除 `test_repo_hygiene` 与 `test_historical_recovery_realdb`。
- 前端全量：315 tests OK。
- `npm run build`：通过。
- `git diff --check`：通过，仅有 CRLF 提示。
- 卫生测试：唯一失败项为未跟踪新文件尚未纳入版本管理，属预期收口状态。

## 未验证边界

- SonarQube 已停止且本地无扫描结果存档，`bugs=0 / vulnerabilities=0 / code smells=412 / 33 security hotspots` 未重新复核。
- `tests/test_historical_recovery_realdb.py` 是本地真实库测试，未纳入本次全量。
- 行数为 PowerShell 文件行计数，未按逻辑行或注释行单独统计。
- 前端仅通过自动化测试与构建，未做本轮浏览器视觉回归。
