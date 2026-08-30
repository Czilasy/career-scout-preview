# Quickstart 验证指南: 续跑账号身份修复

**Date**: 2026-08-30 | 前置：本地 `uv run` 可用；测试隔离库与临时目录由测试基建自备。

## 自动化验证（本批次主验证路径）

```powershell
# 1. 聚焦测试（双门槛判定 / 快照 / 兜底口径 / 换号可见化）
uv run python -m unittest tests.webui_app.test_resume_account_gate -v

# 2. 既有续跑集成回归（含 B057 场景：面板切号后不带 target 继续 → 仍换号）
uv run python -m unittest tests.webui_app.test_webui_app_taskrun -v

# 3. 后端全量 + 前端测试 + 构建（交付门禁，按宪法 V）
uv run python -m unittest discover -s tests
npm --prefix webui test
npm --prefix webui run build

# 4. 仓库卫生检查
uv run python -m unittest tests.test_repo_hygiene
```

预期：1、2 全绿；3 无失败；4 通过。

## 场景对照（映射 spec 验收场景）

| 验收场景 | 自动化覆盖 |
|---|---|
| US1-1 R2=b、全局=d，暂停未动账号 → 继续沿用 b | test_resume_account_gate（判定）+ taskrun 集成用例 |
| US1-2 AI 类暂停码 + 用户换过账号 → 不换 | test_resume_account_gate |
| US1-3 抓取/重抓暂停未动账号 → 冻结账号不变 | test_resume_account_gate |
| US1-4 无快照存量任务 → 不自动换 | test_resume_account_gate |
| US1-5 源类阻断 + 暂停期间激活新账号 → 换到新账号（B057 语义） | 既有 B057 测试 + taskrun 补充用例 |
| US1-6 显式 target_account → 行为不变 | 既有显式换号测试保持通过 |
| US2 换号 → account_switch 事件 + 日志行；未换号 → 无痕迹 | test_resume_account_gate |
| US3-1 BOSS 缺冻结账号 → 两入口同口径（R2 解析） | test_resume_account_gate |
| US3-2 智联兜底不变 | test_resume_account_gate |
| US4-1 任务运行中 job-detail → 409 中文提示 | taskrun 集成用例 |
| US4-2 全局目录被改后 JD 阶段仍用冻结账号浏览器 | 单元级重绑断言（activate_task_browser 调用参数） |

## 手动抽查（可选，EXE/源码模式均可）

1. 账号簿中给账号 b 打 R2 标记，全局选 d，发起 BOSS 搜索 + AI 筛选；等待或制造暂停后点"继续"——浏览器打开的资料目录应为 b，任务结果/日志中的账号应为 b。
2. 同场景下先把全局切到 e 再点"继续"且暂停码为 AI 类——应仍用 b，且日志无换号行。
3. 任务运行中对旧结果点单岗位 JD 抓取——应被拒绝并显示中文提示；任务结束后重试则正常。
